import logging
import math
import time
import os
import json
import subprocess
import requests
from typing import Dict, Optional, List, Tuple

from ..config_model import LocalFilesConfig
from .enricher_plugin import EnricherPlugin
from .id_generator import generate_artist_id, generate_album_id
from .match_utils import duration_bonus


logger = logging.getLogger(__name__.split(".")[-1])


class AcoustIdPlugin(EnricherPlugin):
    """AcoustID audio fingerprinting plugin for track identification"""

    def __init__(self, config: LocalFilesConfig, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.api_key = config.enricher.plugins.acoustid.api_key

        if not self.api_key:
            logger.warning("AcoustID API key not configured. Plugin will be disabled.")

        # Rate limiting
        self.last_request_time = 0
        self.request_interval = (
            1.0 / 3
        )  # 3 request per second to respect AcoustID limits

        # Match confidence thresholds (0-1.0)
        self.min_score_threshold = 0.7  # Minimum score to consider a match valid

    def _wait_for_rate_limit(self):
        """Wait to respect rate limits"""
        now = time.time()
        elapsed = now - self.last_request_time

        if elapsed < self.request_interval:
            sleep_time = self.request_interval - elapsed
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def _generate_fingerprint(
        self, file_path: str
    ) -> Tuple[Optional[str], Optional[int]]:
        """
        Generate audio fingerprint using chromaprint (fpcalc)

        Returns:
            Tuple of (fingerprint, duration)
        """
        try:
            # Check if file exists
            if not os.path.isfile(file_path):
                logger.error(f"File not found: {file_path}")
                return None, None

            # Run fpcalc tool to generate fingerprint
            cmd = ["fpcalc", "-json", file_path]
            logger.debug(f"Running command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,  # Timeout after 30 seconds
            )

            # Parse JSON output
            data = json.loads(result.stdout)
            if "fingerprint" in data and "duration" in data:
                return data["fingerprint"], data["duration"]
            else:
                logger.error(
                    f"Missing fingerprint or duration in fpcalc output for {file_path}"
                )
                return None, None

        except FileNotFoundError:
            logger.error("fpcalc (chromaprint) not found. Please install chromaprint.")
            return None, None
        except subprocess.SubprocessError as e:
            logger.error(f"Error running fpcalc on {file_path}: {str(e)}")
            return None, None
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing fpcalc JSON output for {file_path}: {str(e)}")
            return None, None
        except Exception as e:
            logger.error(
                f"Unexpected error generating fingerprint for {file_path}: {str(e)}"
            )
            return None, None

    def _lookup_fingerprint(
        self, fingerprint: str, duration: int
    ) -> Optional[List[Dict]]:
        """
        Look up a fingerprint in the AcoustID database

        Returns:
            List of matching recordings with MusicBrainz IDs
        """
        if not self.api_key:
            logger.error("Cannot lookup fingerprint: AcoustID API key not configured")
            return None

        try:
            self._wait_for_rate_limit()

            # Prepare API request
            url = "https://api.acoustid.org/v2/lookup"
            params = {
                "client": self.api_key,
                "meta": "recordings releases releasegroups tracks compress",
                "fingerprint": fingerprint,
                "duration": math.floor(duration),
            }

            response = requests.get(url, params=params)
            if response.status_code != 200:
                logger.error(
                    f"AcoustID API error: {response.status_code} - {response.text}"
                )
                return None

            data = response.json()
            if data.get("status") != "ok":
                logger.error(
                    f"AcoustID lookup failed: {data.get('error', 'Unknown error')}"
                )
                return None

            # Process and return results
            results = data.get("results", [])
            if not results:
                logger.debug("No AcoustID matches found")
                return None

            # Filter results by score
            valid_results = [
                r for r in results if r.get("score", 0) >= self.min_score_threshold
            ]
            if not valid_results:
                logger.debug(
                    f"No AcoustID matches with score >= {self.min_score_threshold}"
                )
                return None

            return valid_results

        except requests.RequestException as e:
            logger.error(f"AcoustID API request error: {str(e)}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing AcoustID API response: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during AcoustID lookup: {str(e)}")
            return None

    # When two candidate recordings end up with nearly-tied combined
    # scores, we can't reliably tell them apart. Leave the track as
    # orphan rather than commit to the wrong release — the artist view
    # still surfaces orphan tracks, and re-enrichment may resolve later.
    MIN_MATCH_MARGIN = 5.0

    @staticmethod
    def _release_group_score(rg_type, secondary_types) -> float:
        """Heuristic score for a release group by its type metadata.

        Prefer a clean 'Album' release; deprioritize compilations, live
        cuts, soundtracks, and other secondary placements. The same
        recording usually appears on many releases — the original studio
        album, a greatest-hits compilation, a regional re-issue, a
        live recording — and the compilations/lives are almost never
        what the user has on disk, so we lean hard against them.
        """
        primary = (rg_type or "").lower()
        secondary = [s.lower() for s in (secondary_types or [])]

        score = 0.0
        if primary == "album":
            score += 30
        elif primary == "ep":
            score += 20
        elif primary == "single":
            score += 15
        elif primary == "broadcast":
            score += 5

        for sec in secondary:
            if sec in ("compilation", "live", "remix"):
                score -= 15
            elif sec in ("soundtrack", "demo", "interview", "spokenword", "audiobook"):
                score -= 25

        return score

    def _pick_best_release(self, recording: Dict) -> Tuple[Optional[str], Optional[str]]:
        """Pick the best (album_title, album_mbid) for a recording.

        AcoustID with ``meta=releasegroups`` returns releases nested
        under their release groups with a ``type`` (and
        ``secondarytypes``) field, which lets us prefer the original
        studio album over a compilation re-release of the same
        recording. When that nested structure isn't present (older
        responses or recordings with no release-group meta), we fall
        back to the recording's flat ``releases`` list and just take
        the first.
        """
        candidates: List[Tuple[float, Dict]] = []

        for rg in recording.get("releasegroups") or []:
            rg_score = self._release_group_score(
                rg.get("type"), rg.get("secondarytypes")
            )
            for rel in rg.get("releases") or []:
                candidates.append((rg_score, rel))

        if not candidates:
            for rel in recording.get("releases") or []:
                candidates.append((0.0, rel))

        if not candidates:
            return None, None

        # Highest-scoring release wins; first occurrence breaks ties so
        # behavior reduces to ``releases[0]`` when no type info exists.
        best = max(range(len(candidates)), key=lambda i: candidates[i][0])
        release = candidates[best][1]
        return release.get("title"), release.get("id")

    def _get_best_match_info(
        self,
        acoustid_results: List[Dict],
        target_duration_s: Optional[float] = None,
    ) -> Optional[Dict]:
        """Extract the best match information from AcoustID results.

        AcoustID typically returns several results, each potentially
        containing several recordings. Picking ``results[0].recordings[0]``
        blindly (the prior behavior) often grabs a re-issue or compilation
        version of a track whose original recording is also in the
        candidate list. We instead score every (result, recording) pair
        by combined AcoustID confidence + duration match against the
        local file, pick the highest, and reject the pick if the next
        candidate is within ``MIN_MATCH_MARGIN`` — the latter prevents
        coin-flip assignments to the wrong recording when several
        candidates look equally plausible.
        """
        if not acoustid_results:
            return None

        best_pair: Optional[Tuple[Dict, Dict]] = None
        best_combined = float("-inf")
        runner_up_combined: float = float("-inf")
        best_score = 0.0

        for result in acoustid_results:
            base_score = float(result.get("score", 0)) * 100  # 0-100 scale
            for recording in result.get("recordings", []) or []:
                if not recording.get("id"):
                    continue
                # AcoustID recording duration is in seconds.
                cand_s = recording.get("duration")
                combined = base_score + duration_bonus(target_duration_s, cand_s)
                if combined > best_combined:
                    runner_up_combined = best_combined
                    best_combined = combined
                    best_pair = (result, recording)
                    best_score = result.get("score", 0)
                elif combined > runner_up_combined:
                    runner_up_combined = combined

        if best_pair is None:
            logger.debug("No recordings with MBIDs in AcoustID results")
            return None

        # Skip when the top two candidates are effectively tied.
        if (
            runner_up_combined != float("-inf")
            and (best_combined - runner_up_combined) < self.MIN_MATCH_MARGIN
        ):
            logger.info(
                "AcoustID match is ambiguous: best=%.1f runner_up=%.1f "
                "margin<%.1f — holding as orphan rather than committing",
                best_combined,
                runner_up_combined,
                self.MIN_MATCH_MARGIN,
            )
            return None

        best_result, best_recording = best_pair

        mb_recording_id = best_recording.get("id")
        title = best_recording.get("title")

        artists = best_recording.get("artists", []) or []
        artist_name = artists[0].get("name") if artists else None
        artist_id = artists[0].get("id") if artists else None

        album_title, album_id = self._pick_best_release(best_recording)

        return {
            "score": best_score,
            "recording_mbid": mb_recording_id,
            "title": title,
            "artist_name": artist_name,
            "artist_mbid": artist_id,
            "album_title": album_title,
            "album_mbid": album_id,
        }

    async def _create_or_get_artist(
        self, artist_name: str, artist_mbid: Optional[str] = None
    ) -> Optional[str]:
        """
        Find artist by name or MBID, or create if not exists

        Returns:
            Artist ID
        """
        if not artist_name:
            return None

        # Try to find existing artist by MBID first (most accurate)
        if artist_mbid:
            existing_artist = await self.db_manager.get_artist_by_mbid(artist_mbid)
            if existing_artist:
                return existing_artist["id"]

        # Try to find by name
        artists, _ = await self.db_manager.search_artists(artist_name, limit=1)
        # Check for exact match
        existing_artist = next(
            (a for a in artists if a["name"].lower() == artist_name.lower()), None
        )
        if existing_artist:
            # If found and we have an MBID but they don't, update it
            if artist_mbid and not existing_artist.get("mbid"):
                await self.db_manager.update_artist(
                    existing_artist["id"], {"mbid": artist_mbid}
                )
            return existing_artist["id"]

        # Create new artist
        artist_data = {
            "name": artist_name,
            "mbid": artist_mbid,
        }

        artist_id = generate_artist_id(artist_name)
        artist_data["id"] = artist_id
        artist_data["last_updated"] = int(time.time())

        await self.db_manager.insert_artist(artist_data)
        return artist_data["id"]

    async def _create_or_get_album(
        self, album_title: str, artist_id: str, album_mbid: Optional[str] = None
    ) -> Optional[str]:
        """
        Find album by title/artist or MBID, or create if not exists

        Returns:
            Album ID
        """
        if not album_title or not artist_id:
            return None

        # Try to find existing album by MBID first
        if album_mbid:
            existing_album = await self.db_manager.get_album_by_mbid(album_mbid)
            if existing_album:
                return existing_album["id"]

        # Try to find by title and artist
        existing_album = await self.db_manager.get_album_by_title_and_artist(
            album_title, artist_id
        )
        if existing_album:
            # If found and we have MBID but they don't, update it
            if album_mbid and not existing_album.get("mbid"):
                await self.db_manager.update_album(
                    existing_album["id"], {"mbid": album_mbid}
                )
            return existing_album["id"]

        # Create new album
        album_id = generate_album_id(album_title, artist_id)

        album_data = {
            "id": album_id,
            "title": album_title,
            "artist_id": artist_id,
            "mbid": album_mbid,
            "enriched": 0,
            "last_updated": int(time.time()),
        }

        await self.db_manager.insert_album(album_data)
        return album_id

    def can_enrich_artist(self) -> bool:
        return False  # This plugin doesn't directly enrich artists, only via track identification

    def can_enrich_album(self) -> bool:
        return False  # This plugin doesn't directly enrich albums, only via track identification

    def can_enrich_track(self) -> bool:
        return bool(self.api_key)  # Only if API key is configured

    async def enrich_artist(self, artist: Dict) -> Optional[Dict]:
        """
        AcoustID doesn't directly enrich artists.
        This is a placeholder to satisfy the abstract method requirement.
        """
        return None

    async def enrich_album(self, album: Dict) -> Optional[Dict]:
        """
        AcoustID doesn't directly enrich albums.
        This is a placeholder to satisfy the abstract method requirement.
        """
        return None

    async def enrich_track(self, track: Dict) -> Optional[Dict]:
        """
        Enrich track using audio fingerprinting

        This will:
        1. Skip if track is already enriched
        2. Generate fingerprint from audio file
        3. Look up fingerprint in AcoustID
        4. Extract metadata and update track
        5. Create missing artists and albums if necessary
        """
        try:
            # Skip if track is already enriched or no file path
            if track.get("enriched") or not track.get("file_path"):
                return None

            if (
                track.get("artist_id") != "unknown_artist"
                and track.get("album_id") != "unknown_album"
            ):
                logger.debug(
                    f"Skipping acoustid enrichment for track {track.get('title', 'Unknown')} - "
                    f"Already enriched or has known artist/album"
                )
                return None

            logger.debug(
                f"Fingerprinting track: {track.get('title', 'Unknown')} - {track.get('file_path')}"
            )

            # Generate fingerprint
            fingerprint, duration = self._generate_fingerprint(track["file_path"])
            if not fingerprint or not duration:
                logger.debug(
                    f"Could not generate fingerprint for {track.get('file_path')}"
                )
                return None

            # Look up fingerprint
            results = self._lookup_fingerprint(fingerprint, duration)
            if not results:
                logger.debug(f"No AcoustID matches for {track.get('file_path')}")
                return None

            # Extract best match information. Pass the local file's
            # duration (preferring the tag-derived value when present,
            # falling back to the fpcalc-measured one) so duration is used
            # to pick among same-named recordings.
            target_duration_s = track.get("duration") or duration
            match_info = self._get_best_match_info(results, target_duration_s)
            if not match_info:
                logger.debug(
                    f"Could not extract match info for {track.get('file_path')}"
                )
                return None

            logger.info(
                f"AcoustID match found for {track.get('file_path')} - "
                f"Score: {match_info['score']:.2f}, "
                f"Title: {match_info.get('title')}, "
                f"Artist: {match_info.get('artist_name')}"
            )

            updates = {
                "mbid": match_info["recording_mbid"],
                "match_score": int(match_info["score"] * 100),  # Convert to 0-100 scale
            }

            updates["title"] = match_info["title"]

            # Track items that need further enrichment
            changed_items = {"artists": set(), "albums": set()}

            # Create or get artist if needed
            if match_info.get("artist_name"):
                # Store original artist ID to check if a new one was created
                original_artist_id = track.get("artist_id")

                artist_id = await self._create_or_get_artist(
                    match_info["artist_name"], match_info.get("artist_mbid")
                )

                if artist_id:
                    updates["artist_id"] = artist_id
                    updates["artist_name"] = match_info["artist_name"]

                    # Check if this is a newly created or different artist
                    if artist_id != original_artist_id:
                        logger.debug(
                            f"Adding artist {artist_id} to changed items for further enrichment"
                        )
                        changed_items["artists"].add(artist_id)

                    # Create or get album if we have artist and album info
                    if match_info.get("album_title") and artist_id:
                        # Store original album ID to check if a new one was created
                        original_album_id = track.get("album_id")

                        album_id = await self._create_or_get_album(
                            match_info["album_title"],
                            artist_id,
                            match_info.get("album_mbid"),
                        )

                        if album_id:
                            updates["album_id"] = album_id
                            updates["album_title"] = match_info["album_title"]

                            # Check if this is a newly created or different album
                            if album_id != original_album_id:
                                logger.debug(
                                    f"Adding album {album_id} to changed items for further enrichment"
                                )
                                changed_items["albums"].add(album_id)

            result = {"updates": updates}

            # If we have any items that need further enrichment, add them to result
            if any(changed_items.values()):
                result["changed_items"] = {
                    "artists": list(changed_items["artists"]),
                    "albums": list(changed_items["albums"]),
                    "tracks": [track["id"]],
                }

            # Return the updates for this track
            return result

        except Exception as e:
            logger.error(
                f"Error enriching track {track.get('title', 'Unknown')} with AcoustID: {str(e)}"
            )
            return None
