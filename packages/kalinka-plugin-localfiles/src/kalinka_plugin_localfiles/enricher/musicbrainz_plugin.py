import asyncio
import logging
import musicbrainzngs
import re
from difflib import SequenceMatcher
from typing import Dict, Optional, List, Tuple

from ..config_model import LocalFilesConfig
from .enricher_plugin import EnricherPlugin, inferred_claims
from .match_utils import (
    album_duration_bonus,
    duration_bonus,
    flatten_mb_tracklist,
    parse_mb_length_seconds,
    parse_mb_track_count,
    release_total_length_seconds,
    track_count_bonus,
    tracklist_coverage_bonus,
)
from .tracklist_align import Alignment, align_tracklist

logger = logging.getLogger(__name__.split(".")[-1])

# How close (in weighted score points) the top two album candidates have
# to be before we hold the album as orphan rather than commit a guess.
# Smaller than the track-level margin (5.0) because albums have richer
# disambiguating signal (track count + total duration) — a 3-point gap
# between two stage-B candidates is already meaningful.
ALBUM_MATCH_MIN_MARGIN = 3.0

# Tracklist coverage at or above which an accepted release's track_map is
# trustworthy enough to drive per-track identity (Phase 3, §6.2).
TRACKLIST_ACCEPT_COVERAGE = 0.9


def _same_release_group(a: Dict, b: Dict) -> bool:
    """Return True when two MB ``release-list`` entries share a
    release-group MBID. Used by the album ambiguity guard to
    distinguish "two different albums that happen to score the same"
    (orphan-hold) from "two physical releases of the same logical
    album" (commit either)."""
    a_id = (a.get("release-group") or {}).get("id")
    b_id = (b.get("release-group") or {}).get("id")
    return bool(a_id and b_id and a_id == b_id)


class MusicBrainzPlugin(EnricherPlugin):
    """MusicBrainz metadata enrichment plugin"""

    ENRICHER_VERSION = 1

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

        # Memoised release-detail fetches. Album enrichment seeds this
        # when it picks a release; per-track enrichment then reads
        # straight from the cache instead of refetching. Process-
        # lifetime; never gets large enough to need eviction in
        # practice (one entry per album we enrich).
        self._release_cache: Dict[str, Dict] = {}

        # Accepted release candidate per album_id (None = looked up, none
        # accepted), so placing N tracks costs one query per album, not N.
        self._accepted_map_cache: Dict[str, Optional[Dict]] = {}

        # Set up MusicBrainz API
        musicbrainzngs.set_useragent(
            self.user_agent.split("/")[0],
            self.user_agent.split("/")[1].split(" ")[0],
            self.user_agent.split(" ", 1)[1].strip("()"),
        )

    def config_signature(self) -> Dict:
        # Thresholds and the string-similarity floor decide whether a
        # candidate is accepted, so changing any of them can flip a
        # FAILED row to enriched. ``debug_matching`` is logging-only and
        # deliberately excluded.
        return {
            "artist_threshold": self.artist_threshold,
            "album_threshold": self.album_threshold,
            "track_threshold": self.track_threshold,
            "string_similarity": self.string_similarity_threshold,
        }

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
            result = await asyncio.to_thread(
                musicbrainzngs.search_artists,
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

            # Country of origin (ISO 3166-1 alpha-2, e.g. "IT") drives
            # nationality queries. `area`/`begin-area` (a place name) is the
            # fallback for acts MB has no country code for — common for
            # Soviet-era / historical artists. Both already ride along in the
            # search response, so this costs no extra request.
            country = best_match.get("country")
            area = best_match.get("area") or best_match.get("begin-area") or {}
            area_name = area.get("name") if isinstance(area, dict) else None

            # Every resolvable field is emitted as a claim only — resolution is
            # the sole writer (updates carries just identity + match metadata).
            # The canonical name re-cases a matching local name ("THE BEATLES"
            # -> "The Beatles") but never replaces a different one; country/area
            # are external-first and fill uncontested (no competing local tag).
            claims = inferred_claims(
                f"musicbrainz:{artist_mbid}",
                {"name": best_match.get("name"), "country": country, "area": area_name},
            )

            return {"updates": updates, "mbid": artist_mbid, "claims": claims}

        except Exception as e:
            logger.error(f"Error enriching artist {artist['name']}: {str(e)}")
            return None

    def _shortlist_releases(
        self,
        items: List[Dict],
        name: str,
        target_track_count: Optional[int],
    ) -> List[Tuple[Dict, float, float]]:
        """Stage-A ranking from cheap signals already in search_releases
        results. Returns ``[(item, weighted_score, similarity), ...]``
        sorted best-first, filtered to items at or above the album
        score threshold.

        The weighted score folds in the MB ext:score, our string
        similarity, and a track-count bonus computed from
        ``medium-track-count`` (or summed per-medium track counts).

        Candidates below the configured similarity threshold are
        filtered out here, not after the weighted score is computed —
        otherwise a noisy low-similarity candidate with a coincidental
        duration match could outrank the correct one and then get
        rejected, leaving us with no match at all. Real releases with
        sub-threshold title similarity are vanishingly rare; defaulting
        to "no match" (orphan) is safer than risking a wrong commit.
        """
        ranked: List[Tuple[Dict, float, float]] = []
        for item in items:
            score = int(item.get("ext:score", 0))
            if score < self.album_threshold:
                continue
            similarity = self._string_similarity(name, item.get("title", ""))
            if similarity < self.string_similarity_threshold:
                continue
            weighted = 0.5 * score + 0.5 * (similarity * 100)
            weighted += track_count_bonus(
                target_track_count, parse_mb_track_count(item)
            )
            ranked.append((item, weighted, similarity))
        ranked.sort(key=lambda t: -t[1])
        return ranked

    async def enrich_album(self, album: Dict) -> Optional[Dict]:
        """Enrich album metadata with MusicBrainz data.

        Two-stage matcher:

          A. Cheap rank from ``search_releases`` results — combines
             ext:score, title similarity, and track-count match. Used
             to shortlist the top candidates without extra API calls.

          B. Fetch the top shortlisted releases with recordings included,
             then disambiguate by total-tracklist duration. This is what
             tells a 12-track standard edition from a 16-track deluxe
             with the same title.
        """
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

            result = await asyncio.to_thread(
                musicbrainzngs.search_releases,
                album["title"],
                artistname=album["artist_name"],
                strict=True,
                limit=20,
            )
            if not result["release-list"]:
                logger.debug(
                    f"No MusicBrainz match found for album: {album['title']} by {album['artist_name']}"
                )
                return None

            local_track_count = album.get("track_count")
            local_duration_s = album.get("duration")

            # ---- Stage A: rank by score + similarity + track-count ----
            shortlist = self._shortlist_releases(
                result["release-list"],
                album["title"],
                local_track_count,
            )
            if not shortlist:
                logger.debug(
                    f"No MB candidates clear the threshold for album: {album['title']}"
                )
                return None

            # ---- Stage B: fetch top 3, score by tracklist duration ----
            # Fetching the winner's recordings is something we'd do anyway
            # below; doing it for two extras costs ~2 additional MB calls
            # per album but lets us tell std/deluxe/reissue apart.
            # Local tracklist for alignment (Phase 3): fetched once and reused
            # for every candidate, so alignment costs no extra MB requests —
            # stage B already retrieves each candidate's recordings. Alignment
            # only *adds* evidence, so a failure here degrades to the
            # pre-alignment scoring rather than costing the album its match.
            try:
                local_tracks = await self.db_manager.get_album_tracks_for_alignment(
                    album["id"]
                )
            except Exception as e:
                logger.debug(f"Could not load local tracklist for alignment: {e}")
                local_tracks = []

            scored: List[Tuple[Dict, float, float, Dict]] = []
            candidate_rows: List[Dict] = []
            for cand, base_score, similarity in shortlist[:3]:
                try:
                    details = await asyncio.to_thread(
                        musicbrainzngs.get_release_by_id,
                        cand["id"],
                        includes=["recordings", "artist-credits", "tags",
                                  "release-groups"],
                    )
                except Exception as e:
                    logger.debug(
                        f"Could not fetch release {cand['id']} for stage-B scoring: {e}"
                    )
                    continue
                release_data = details["release"]
                cand_duration = release_total_length_seconds(release_data)
                d_bonus = album_duration_bonus(local_duration_s, cand_duration)
                alignment = self._align_release(local_tracks, release_data)
                c_bonus = tracklist_coverage_bonus(alignment.coverage)
                combined = base_score + d_bonus + c_bonus
                if self.debug_matching:
                    logger.debug(
                        f"  Stage B: '{release_data.get('title')}' "
                        f"local_dur={local_duration_s} cand_dur={cand_duration} "
                        f"base={base_score:.1f} d_bonus={d_bonus:+.0f} "
                        f"coverage={alignment.coverage:.2f} c_bonus={c_bonus:+.0f} "
                        f"combined={combined:.1f}"
                    )
                scored.append((cand, combined, similarity, release_data))
                candidate_rows.append({
                    "release_id": cand["id"],
                    "rg_id": (release_data.get("release-group") or {}).get("id"),
                    "score": combined,
                    "coverage": alignment.coverage,
                    "track_map": alignment.track_map,
                })

            if not scored:
                logger.debug(
                    f"No stage-B candidates retrievable for album: {album['title']}"
                )
                return None

            scored.sort(key=lambda t: -t[1])
            best, best_score, best_similarity, mb_release_data = scored[0]

            # Reject ambiguous picks (deluxe vs standard etc.) so the album
            # stays orphan rather than committing the wrong edition. The
            # guard fires when the top two are within ``ALBUM_MATCH_MIN_MARGIN``
            # AND belong to different release-groups — different
            # release-groups means truly-different albums (the case we
            # want to catch), but the *same* release-group is just MB
            # listing CD / vinyl / remaster variants of one logical
            # album, where picking either is correct enough. Without
            # this carve-out the guard wrongly orphans common cases
            # like "Abbey Road" (every reissue scores identically).
            held = False
            if len(scored) > 1:
                runner_up_cand, runner_up_score, _runner_sim, _runner_rel = scored[1]
                held = (
                    best_score - runner_up_score < ALBUM_MATCH_MIN_MARGIN
                    and not _same_release_group(best, runner_up_cand)
                )

            release_mbid = best["id"]

            # Record what was considered — including on the orphan-hold path,
            # which is exactly what a human review needs to see. `accepted`
            # means the track_map is trustworthy for per-track identity; weak
            # alignment never rejects a match (see tracklist_coverage_bonus).
            for row in candidate_rows:
                row["status"] = (
                    "accepted"
                    if not held
                    and row["release_id"] == release_mbid
                    and row["coverage"] >= TRACKLIST_ACCEPT_COVERAGE
                    else "candidate"
                )
            try:
                await self.db_manager.save_release_candidates(
                    album["id"], "musicbrainz", candidate_rows
                )
            except Exception as e:  # observability must never break enrichment
                logger.debug(f"Could not save release candidates: {e}")

            if held:
                logger.info(
                    f"Ambiguous album match for '{album['title']}': "
                    f"best={best_score:.1f} runner_up={runner_up_score:.1f} — "
                    f"holding as orphan rather than committing"
                )
                return None

            updates = {
                "mbid": release_mbid,
                "match_score": int(best.get("ext:score", 0)),
                "match_similarity": round(best_similarity * 100),
            }

            genre = None
            if mb_release_data.get("tag-list"):
                sorted_tags = sorted(
                    mb_release_data["tag-list"],
                    key=lambda x: int(x.get("count", 0)),
                    reverse=True,
                )
                genre = sorted_tags[0]["name"]

            year = None
            if "date" in mb_release_data:
                try:
                    year = int(mb_release_data["date"].split("-")[0])
                except (ValueError, IndexError):
                    pass

            # Lyrics language (ISO 639-3, e.g. "ita") — drives language queries
            # and disambiguates nationality ones (an Italian-language track by a
            # non-Italian artist).
            text_rep = mb_release_data.get("text-representation") or {}
            language = text_rep.get("language")

            # First-release year of the *release group* — the era this music is
            # from, as opposed to `year` (this pressing/reissue). A 2010
            # compilation of 80s songs keeps year=2010 but original_year=198x, so
            # "80s" era queries match the music, not the CD manufacturing date.
            rg = mb_release_data.get("release-group") or {}
            original_year = None
            first_release = rg.get("first-release-date")
            if first_release:
                try:
                    original_year = int(first_release.split("-")[0])
                except (ValueError, IndexError):
                    pass

            # Cache the release detail so the per-track enrichment below
            # (which needs the same tracklist to look up each recording's
            # disc/track position) doesn't refetch — one extra API call
            # per album vs N calls per album of N tracks.
            self._release_cache[release_mbid] = mb_release_data

            # Every resolvable field is emitted as a claim only — resolution is
            # the sole writer (updates carries just identity + match metadata).
            # Title re-cases a matching local title but never replaces a
            # different one (a fuzzy match can misidentify the edition).
            # genre/year/language have a competing local tag (observed) that
            # outranks this inferred claim, so the local value wins when present;
            # original_year has no local tag, so MB fills it uncontested.
            claims = inferred_claims(
                f"musicbrainz:{release_mbid}",
                {
                    "title": mb_release_data.get("title") or best.get("title"),
                    "genre": genre,
                    "year": year,
                    "original_year": original_year,
                    "language": language,
                },
            )

            return {"updates": updates, "mbid": release_mbid, "claims": claims}

        except Exception as e:
            logger.error(f"Error enriching album {album['title']}: {str(e)}")
            return None

    async def _get_release_detail(self, release_mbid: str) -> Optional[Dict]:
        """Return the recordings-include release detail for ``release_mbid``.

        Memoised on the plugin instance so per-track enrichment can pull
        the canonical tracklist without an MB call per track. Album
        enrichment seeds the cache when it commits to a release, so the
        common path (enrich one album → enrich its 12-30 tracks) does
        exactly one extra MB fetch per album.
        """
        if not release_mbid:
            return None
        cached = self._release_cache.get(release_mbid)
        if cached is not None:
            return cached
        try:
            details = await asyncio.to_thread(
                musicbrainzngs.get_release_by_id,
                release_mbid,
                includes=["recordings"],
            )
        except Exception as e:
            logger.debug(
                f"Could not fetch release {release_mbid} for tracklist lookup: {e}"
            )
            return None
        release = details.get("release") if details else None
        if release is not None:
            self._release_cache[release_mbid] = release
        return release

    async def _lookup_track_in_accepted_map(self, track: Dict) -> Optional[Dict]:
        """Place a track from its album's accepted release candidate (Phase 3).

        The album-level alignment assigned every slot in one pass, so this is
        globally consistent — monotonic, no two tracks claiming one slot — which
        independent per-track matching can't guarantee. Falls through (None) to
        the existing paths whenever the map can't answer.
        """
        album_id = track.get("album_id")
        if not album_id or album_id == "unknown_album":
            return None

        if album_id not in self._accepted_map_cache:
            try:
                accepted = await self.db_manager.get_accepted_release_candidate(
                    album_id
                )
            except Exception as e:
                logger.debug(f"Could not read accepted release candidate: {e}")
                accepted = None
            self._accepted_map_cache[album_id] = accepted

        accepted = self._accepted_map_cache[album_id]
        if not accepted:
            return None
        entry = (accepted.get("track_map") or {}).get(track["id"])
        # Shape-guard rather than pad: a malformed map should fall through to
        # per-track matching, not half-fill a row.
        if not isinstance(entry, (list, tuple)) or len(entry) < 3:
            return None

        disc, pos, rec_id = entry[0], entry[1], entry[2]
        updates: Dict[str, object] = {}
        if disc is not None:
            updates["disc_number"] = disc
        if pos is not None:
            updates["track_number"] = pos
        if rec_id:
            updates["mbid"] = rec_id
        if not updates:
            return None
        # The confidence is the album alignment's, not a per-track score.
        updates["match_score"] = int(float(accepted.get("coverage") or 0) * 100)
        if self.debug_matching:
            logger.debug(
                f"  Track map: '{track.get('title')}' -> disc={disc} pos={pos}"
            )
        return {"updates": updates, "mbid": rec_id}

    @staticmethod
    def _align_release(local_tracks: List[Dict], release_data: Dict) -> Alignment:
        """Align the local tracklist against one candidate release (Phase 3)."""
        release_tracks = [
            {
                "title": t.get("title"),
                "length_s": parse_mb_length_seconds(t.get("length")),
                "disc": t.get("medium"),
                "pos": t.get("track"),
                "rec_id": t.get("recording_id"),
            }
            for t in flatten_mb_tracklist(release_data)
        ]
        return align_tracklist(local_tracks, release_tracks)

    async def _lookup_track_in_release(
        self, track: Dict, release_mbid: str
    ) -> Optional[Dict]:
        """Find ``track`` on the matched album's MB release tracklist.

        Builds a ``[{disc, track, length_s, title, recording_id}, ...]``
        flat view from the release's media, scores each against the
        local track via ``title_similarity * 100 + duration_bonus``
        (duration penalty capped to -25; within a single release the
        unique-slot constraint matters more than the worst-case
        version-mismatch penalty), and returns the best pick that
        clears a confidence floor.

        On success, returns the standard ``{"updates": …, "mbid": …}``
        contract that ``enrich_track`` returns to the enricher pipeline.
        On no match (low confidence or no release detail), returns
        ``None`` so the caller can fall back to the global search.
        """
        release = await self._get_release_detail(release_mbid)
        if not release:
            return None
        mb_tracks = flatten_mb_tracklist(release)
        if not mb_tracks:
            return None

        l_title = track.get("title") or ""
        l_dur = track.get("duration")
        best: Optional[Dict] = None
        best_score = float("-inf")
        best_similarity = 0.0
        runner_up_score = float("-inf")
        for mt in mb_tracks:
            similarity = self._string_similarity(l_title, mt["title"])
            dur_score = max(duration_bonus(l_dur, mt["length_s"]), -25.0)
            score = similarity * 100 + dur_score
            if score > best_score:
                runner_up_score = best_score
                best_score = score
                best = mt
                best_similarity = similarity
            elif score > runner_up_score:
                runner_up_score = score

        if best is None or best_score < 50.0:
            return None

        # Ambiguity guard: many releases have nearly-titled tracks
        # ("In the Flesh?" on disc 1 vs "In the Flesh" on disc 2 of
        # The Wall — two different songs). Without a guard, a local
        # title with no duration to disambiguate would silently commit
        # to whichever iteration order surfaced first.
        #
        # The bar is intentionally low: a 1.0-point gap. Real "tie"
        # cases (identical titles, no duration, no other signal) have
        # gaps of ~0; suffix-distinct titles like "Part 1" vs "Part 2"
        # — common on themed albums where MB has every part listed
        # separately — only differ by a few characters which
        # SequenceMatcher prices at ~3 points. We want those through.
        if (
            runner_up_score != float("-inf")
            and (best_score - runner_up_score) < 1.0
        ):
            logger.info(
                f"Ambiguous in-release match for '{l_title}': best={best_score:.1f} "
                f"runner_up={runner_up_score:.1f} — holding as orphan"
            )
            return None

        updates: Dict[str, object] = {
            "disc_number": best["medium"],
            "track_number": best["track"],
            "match_score": int(best_score),
            "match_similarity": round(best_similarity * 100),
        }
        if best["recording_id"]:
            updates["mbid"] = best["recording_id"]
        if self.debug_matching:
            logger.debug(
                f"  In-release: '{l_title}' → ({best['medium']}, {best['track']}) "
                f"'{best['title']}' score={best_score:.1f} sim={best_similarity:.2f}"
            )
        return {"updates": updates, "mbid": best["recording_id"]}

    async def enrich_track(self, track: Dict) -> Optional[Dict]:
        """Enrich track metadata with MusicBrainz data.

        Two paths:

          1. **In-release lookup** — when the local album has already
             been matched to a specific MB release, we pull that
             release's canonical tracklist and find this track's
             position via title + duration. This is the only way to
             recover disc/track positions for vinyl rips whose tags
             carry a per-side track number and no ``DISCNUMBER`` at
             all (the Pink Floyd "The Wall" case). The recording's
             MBID also drops out for free.

          2. **Global search** — the fallback when there's no album
             MBID to scope the lookup, or no confident in-release
             match. Behaves as before: search MB recordings globally,
             rank by combined title / score / duration.
        """
        try:
            # If artist_name is missing, try to get it from the artist record
            if "artist_name" not in track and "artist_id" in track:
                artist = await self.db_manager.get_artist_by_id(track["artist_id"])
                if artist:
                    track["artist_name"] = artist["name"]
                else:
                    logger.error(f"Could not find artist for track: {track['title']}")
                    return None

            # Look up the local album row once — we need both its title
            # (existing fallback) and its MBID (path 1).
            album_mbid: Optional[str] = None
            if "album_id" in track:
                album = await self.db_manager.get_album_by_id(track["album_id"])
                if album:
                    if "album_title" not in track:
                        track["album_title"] = album["title"]
                    album_mbid = album.get("mbid")
                elif "album_title" not in track:
                    logger.error(f"Could not find album for track: {track['title']}")
                    return None

            # ---- Path 0: accepted release candidate's track_map ----
            mapped = await self._lookup_track_in_accepted_map(track)
            if mapped is not None:
                return mapped

            # ---- Path 1: in-release tracklist lookup ----
            if album_mbid:
                in_release = await self._lookup_track_in_release(
                    track, album_mbid
                )
                if in_release is not None:
                    return in_release

            # Path 2 below queries MB by ``release=track["album_title"]``;
            # if we got here without a title (no album_id and no
            # album_title was provided), give up rather than crash.
            if "album_title" not in track:
                logger.debug(
                    f"Track {track.get('id')} has no album_title; "
                    f"skipping MB recording search"
                )
                return None

            logger.debug(
                f"Enriching track: {track['title']} from {track['album_title']}"
            )

            # ---- Path 2: fallback global recording search ----
            result = await asyncio.to_thread(
                musicbrainzngs.search_recordings,
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
