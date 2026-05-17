import logging
import musicbrainzngs
import re
from difflib import SequenceMatcher
from typing import Dict, Optional, List, Tuple

from ..config_model import LocalFilesConfig
from .enricher_plugin import EnricherPlugin
from .match_utils import duration_bonus, parse_mb_length_seconds

logger = logging.getLogger(__name__.split(".")[-1])


class MusicBrainzPlugin(EnricherPlugin):
    """MusicBrainz metadata enrichment plugin"""

    def __init__(self, config: LocalFilesConfig, db_manager):
        self.config = config
        self.db_manager = db_manager

        # Threshold configurations - can be overridden in config
        self.artist_threshold = config.enricher.plugins.musicbrainz.artist_threshold
        self.album_threshold = config.enricher.plugins.musicbrainz.album_threshold
        self.track_threshold = config.enricher.plugins.musicbrainz.track_threshold

        # String similarity threshold (0.0-1.0) - set lower to be more permissive
        self.string_similarity_threshold = (
            config.enricher.plugins.musicbrainz.string_similarity
        )

        # Enable detailed logging of match results for debugging
        self.debug_matching = config.enricher.plugins.musicbrainz.debug_matching
        self.user_agent = config.enricher.plugins.user_agent

        # Set up MusicBrainz API
        musicbrainzngs.set_useragent(
            self.user_agent.split("/")[0],
            self.user_agent.split("/")[1].split(" ")[0],
            self.user_agent.split(" ", 1)[1].strip("()"),
        )

    def _normalize_string(self, text: str) -> str:
        """Normalize string for comparison by removing special characters and lowercasing"""
        if not text:
            return ""
        # Remove special characters, convert to lowercase
        return re.sub(r"[^\w\s]", "", text.lower()).strip()

    def _string_similarity(self, a: str, b: str) -> float:
        """Calculate string similarity using SequenceMatcher"""
        # Normalize strings before comparison
        a_norm = self._normalize_string(a)
        b_norm = self._normalize_string(b)

        if not a_norm or not b_norm:
            return 0.0

        return SequenceMatcher(None, a_norm, b_norm).ratio()

    def _find_best_match(
        self,
        items: List[Dict],
        name: str,
        threshold: int,
        match_key: str = "name",
        additional_checks: bool = True,
        target_duration_s: Optional[float] = None,
        length_key: Optional[str] = None,
        min_margin: float = 0.0,
    ) -> Tuple[Optional[Dict], int, float]:
        """
        Find the best match among items based on score and string similarity

        Args:
            items: List of items with ext:score
            name: Name to match against
            threshold: Score threshold (0-100)
            match_key: Key to use for string comparison
            additional_checks: Whether to perform additional string similarity checks
            target_duration_s: Local file duration (seconds). When supplied
                together with ``length_key``, candidates are scored by how
                closely their length matches — a critical signal for
                distinguishing same-titled recordings (live cut vs studio,
                edit vs album version, etc.).
            length_key: Key on each item holding a MusicBrainz-style length
                (ms, typically a string). When set together with
                ``target_duration_s``, the duration bonus is added to the
                weighted score.
            min_margin: When >0, reject the pick if the best and runner-up
                weighted scores are within this margin. Used for tracks,
                where two indistinguishable candidates are usually safer
                left as orphan than committed to the wrong release.

        Returns:
            Tuple of (best_match, score, similarity)
        """
        if not items:
            return None, 0, 0.0

        best_match = None
        best_score = 0.0
        best_similarity = 0.0
        runner_up_score: Optional[float] = None

        # Debug logging
        if self.debug_matching:
            logger.debug(
                f"Finding best match for '{name}' among {len(items)} candidates"
            )

        for item in items:
            score = int(item.get("ext:score", 0))

            # Skip if below threshold
            if score < threshold:
                continue

            # Get the name to compare against
            compare_name = item.get(match_key, "")
            if not compare_name and match_key == "name" and "sort-name" in item:
                compare_name = item["sort-name"]

            similarity = (
                self._string_similarity(name, compare_name)
                if additional_checks
                else 1.0
            )

            # Use a weighted average of score and string similarity, then
            # fold in a duration match when the caller supplied one. The
            # duration bonus is additive on the same 0-100 scale.
            weighted_score = 0.5 * score + 0.5 * (similarity * 100)
            d_bonus = 0.0
            if target_duration_s is not None and length_key:
                cand_s = parse_mb_length_seconds(item.get(length_key))
                d_bonus = duration_bonus(target_duration_s, cand_s)
                weighted_score += d_bonus

            # Debug logging
            if self.debug_matching and score >= threshold:
                logger.debug(
                    f"  Candidate: '{compare_name}' score={score} "
                    f"similarity={similarity:.2f} dur_bonus={d_bonus:+.0f} "
                    f"weighted={weighted_score:.1f}"
                )

            if weighted_score > best_score or (
                weighted_score == best_score and similarity > best_similarity
            ):
                # Previous best becomes the new runner-up.
                if best_match is not None:
                    if runner_up_score is None or best_score > runner_up_score:
                        runner_up_score = best_score
                best_score = weighted_score
                best_match = item
                best_similarity = similarity
            elif runner_up_score is None or weighted_score > runner_up_score:
                runner_up_score = weighted_score

        # If best match has poor string similarity, log a warning
        if (
            best_match
            and best_similarity < self.string_similarity_threshold
            and additional_checks
        ):
            logger.warning(
                f"Low similarity match: '{name}' -> '{best_match.get(match_key, '')}' "
                f"(score={int(best_match.get('ext:score', 0))}, similarity={best_similarity:.2f})"
            )

            return None, 0, 0.0

        # Reject ambiguous picks: when two candidates are nearly tied, we
        # can't reliably tell them apart. Leaving the track as orphan is
        # safer than committing the wrong release/recording — the artist
        # view surfaces orphan tracks, and re-enrichment can try again.
        if (
            best_match
            and min_margin > 0
            and runner_up_score is not None
            and (best_score - runner_up_score) < min_margin
        ):
            logger.info(
                f"Ambiguous match for '{name}': best={best_score:.1f} "
                f"runner_up={runner_up_score:.1f} margin<{min_margin:.1f} — "
                f"holding as orphan rather than committing"
            )
            return None, 0, 0.0

        if best_match and self.debug_matching:
            logger.debug(
                f"Best match for '{name}': '{best_match.get(match_key, '')}' "
                f"(score={int(best_match.get('ext:score', 0))}, similarity={best_similarity:.2f})"
            )

        return (
            best_match,
            int(best_match.get("ext:score", 0)) if best_match else 0,
            best_similarity,
        )

    def can_enrich_artist(self) -> bool:
        return True

    def can_enrich_album(self) -> bool:
        return True

    def can_enrich_track(self) -> bool:
        return True

    async def enrich_artist(self, artist: Dict) -> Optional[Dict]:
        """Enrich artist metadata with MusicBrainz data"""
        try:
            if artist["id"] == "unknown_artist":
                return None

            logger.debug(f"Enriching artist: {artist['name']}")

            # Use alias to improve search, and limit results for faster processing
            result = musicbrainzngs.search_artists(
                artist["name"],
                strict=True,  # Use strict search mode
                limit=20,  # Limit results to top matches
            )

            if not result["artist-list"]:
                logger.debug(f"No MusicBrainz match found for artist: {artist['name']}")
                return None

            # Find best match with string similarity check
            best_match, score, similarity = self._find_best_match(
                result["artist-list"],
                artist["name"],
                self.artist_threshold,
                match_key="name",
            )

            if not best_match:
                logger.debug(
                    f"No good MusicBrainz match for artist: {artist['name']} (best score: {score})"
                )
                return None

            # Get more details from artist
            artist_mbid = best_match["id"]

            # Update artist data
            updates = {
                "mbid": artist_mbid,
                "match_score": score,
                "match_similarity": round(similarity * 100),
            }

            # Return data for further enrichment if needed
            return {"updates": updates, "mbid": artist_mbid}

        except Exception as e:
            logger.error(f"Error enriching artist {artist['name']}: {str(e)}")
            return None

    async def enrich_album(self, album: Dict) -> Optional[Dict]:
        """Enrich album metadata with MusicBrainz data"""
        try:
            if album["id"] == "unknown_album":
                return None
            logger.debug(f"Enriching album: {album['title']}")
            # If artist_name is missing, try to get it from the artist record
            if "artist_name" not in album and "artist_id" in album:
                logger.debug(
                    "Artist name not found, trying to get it from the artist record"
                )
                artist = await self.db_manager.get_artist_by_id(album["artist_id"])
                if artist:
                    logger.debug(
                        f"Found artist for album: {album['title']} -> {artist['name']}"
                    )
                    album["artist_name"] = artist["name"]
                else:
                    logger.error(f"Could not find artist for album: {album['title']}")
                    return None

            logger.debug(f"Enriching album: {album['title']} by {album['artist_name']}")

            # Search for album in MusicBrainz with better query parameters
            result = musicbrainzngs.search_releases(
                album["title"],
                artistname=album["artist_name"],
                strict=True,  # Use strict search mode
                limit=20,  # Limit results to top matches
            )

            if not result["release-list"]:
                logger.debug(
                    f"No MusicBrainz match found for album: {album['title']} by {album['artist_name']}"
                )
                return None

            # Find best match with string similarity check
            best_match, score, similarity = self._find_best_match(
                result["release-list"],
                album["title"],
                self.album_threshold,
                match_key="title",
            )

            if not best_match:
                logger.debug(
                    f"No good MusicBrainz match for album: {album['title']} (best score: {score})"
                )
                return None

            # Get more details about the release
            release_mbid = best_match["id"]
            mb_release_details = musicbrainzngs.get_release_by_id(
                release_mbid, includes=["recordings", "artist-credits", "tags"]
            )
            mb_release_data = mb_release_details["release"]

            # Update album data
            updates = {
                "mbid": release_mbid,
                "match_score": score,
                "match_similarity": round(similarity * 100),
            }

            # Add genre if available from tags
            if "tag-list" in mb_release_data and mb_release_data["tag-list"]:
                # Sort by tag count to get the most popular tag first
                sorted_tags = sorted(
                    mb_release_data["tag-list"],
                    key=lambda x: int(x.get("count", 0)),
                    reverse=True,
                )
                updates["genre"] = sorted_tags[0]["name"]
                logger.debug(
                    f"Found genre for album {album['title']}: {updates['genre']}"
                )

            # Add year if available
            if "date" in mb_release_data:
                try:
                    updates["year"] = int(mb_release_data["date"].split("-")[0])
                except (ValueError, IndexError):
                    pass

            # Return data for further enrichment if needed
            return {"updates": updates, "mbid": release_mbid}

        except Exception as e:
            logger.error(f"Error enriching album {album['title']}: {str(e)}")
            return None

    async def enrich_track(self, track: Dict) -> Optional[Dict]:
        """Enrich track metadata with MusicBrainz data"""
        try:
            # If artist_name is missing, try to get it from the artist record
            if "artist_name" not in track and "artist_id" in track:
                artist = await self.db_manager.get_artist_by_id(track["artist_id"])
                if artist:
                    track["artist_name"] = artist["name"]
                else:
                    logger.error(f"Could not find artist for track: {track['title']}")
                    return None

            # If album_title is missing, try to get it from the album record
            if "album_title" not in track and "album_id" in track:
                album = await self.db_manager.get_album_by_id(track["album_id"])
                if album:
                    track["album_title"] = album["title"]
                else:
                    logger.error(f"Could not find album for track: {track['title']}")
                    return None

            logger.debug(
                f"Enriching track: {track['title']} from {track['album_title']}"
            )

            # Search for recording in MusicBrainz with better parameters
            result = musicbrainzngs.search_recordings(
                track["title"],
                artistname=track["artist_name"],
                release=track["album_title"],
                strict=True,  # Use strict search mode
                limit=20,  # Limit results to top matches
            )

            if not result["recording-list"]:
                logger.debug(f"No MusicBrainz match found for track: {track['title']}")
                return None

            # Find best match — fold in track duration when we have it so
            # same-titled recordings (live cuts, edits, demos, remixes) are
            # disambiguated rather than picked arbitrarily by title alone.
            # Require a margin to runner-up so genuinely-ambiguous picks
            # stay as orphan instead of being committed wrong.
            best_match, score, similarity = self._find_best_match(
                result["recording-list"],
                track["title"],
                self.track_threshold,
                match_key="title",
                target_duration_s=track.get("duration"),
                length_key="length",
                min_margin=5.0,
            )

            if not best_match:
                logger.debug(
                    f"No good MusicBrainz match for track: {track['title']} (best score: {score})"
                )
                return None

            # Update track data
            recording_mbid = best_match["id"]
            updates = {
                "mbid": recording_mbid,
                "match_score": score,
                "match_similarity": round(similarity * 100),
            }

            # Return data for further enrichment if needed
            return {"updates": updates, "mbid": recording_mbid}

        except Exception as e:
            logger.error(f"Error enriching track {track['title']}: {str(e)}")
            return None
