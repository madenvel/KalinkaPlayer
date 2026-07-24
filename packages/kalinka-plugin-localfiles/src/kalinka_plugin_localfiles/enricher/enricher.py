#!/usr/bin/env python3
import multiprocessing
import asyncio
import enum
import importlib.util
import json
import logging
from typing import Optional

from ..config_model import LocalFilesConfig
from ..resolution.resolver import Claim, resolve_display_name, resolve_field
from ..resolution.tag_consensus import album_tag_consensus

from .musicbrainz_plugin import MusicBrainzPlugin
from .acoustid_plugin import AcoustIdPlugin
from .wikidata_plugin import WikidataPlugin
from .deezer_plugin import DeezerPlugin
from .filesystem_fallback_plugin import FilesystemFallbackPlugin
from .enricher_db import AsyncEnricherDb

logger = logging.getLogger("enricher")

# Set MusicBrainzNGS log level to warning to reduce verbosity
musicbrainz_logger = logging.getLogger("musicbrainzngs")
musicbrainz_logger.setLevel(logging.WARNING)
aiosqlite_logger = logging.getLogger("aiosqlite")
aiosqlite_logger.setLevel(logging.WARNING)

# REQUIRED = the genuinely-local fields that gate ENRICHED vs FAILED (Phase
# 2e). A cluster whose local identity resolves is done — "ENRICHED
# (local-only)", not FAILED — even with no external match or cover. This
# deletes the old failure mode "correct MB match without a cover ⇒ FAILED".
ARTIST_REQUIRED_FIELDS = ["name"]
ALBUM_REQUIRED_FIELDS = ["title", "artist_id"]
TRACK_REQUIRED_FIELDS = ["title", "artist_id", "album_id", "duration", "track_number"]

# DESIRED = the full target set (the previous required lists). Reaching it
# means external matching succeeded and covers are filled, so the enricher can
# stop early; NOT reaching it is fine — mbid/image_url/year/genre are desirable,
# not required, and their absence never fails a row.
ARTIST_DESIRED_FIELDS = ["name", "mbid", "image_url"]
ALBUM_DESIRED_FIELDS = ["title", "artist_id", "mbid", "image_url", "year", "genre"]
TRACK_DESIRED_FIELDS = [
    "title", "artist_id", "album_id", "mbid", "duration", "track_number",
]

# The plugin-*fetched* subset of ALBUM_DESIRED. genre/year are written by
# resolution after the plugin loop (from claims, not a plugin direct-write), so
# they must not gate whether we keep calling fetch plugins — only these do. The
# full DESIRED set still gates the top-level "already done, skip the pass" check.
ALBUM_FETCH_FIELDS = ["title", "artist_id", "mbid", "image_url"]

# Origin/era fields resolved external-first (Phase 2d slice 3). A local tag
# value is an `observed` claim; a fuzzy MB match is `inferred`, so for
# genre/year/language the local tag wins when present, while country/area/
# original_year (no competing local tag) are filled by MB. See resolver
# `_EXTERNAL_FIRST_FIELDS`.
ARTIST_EXTERNAL_FIELDS = ("country", "area")
ALBUM_EXTERNAL_FIELDS = ("genre", "year", "original_year", "language")

# Global variables to manage enricher state. The enricher runs inside the
# librarian process (Phase 2b); the indexer feeds it "enrich"/"stop" over an
# in-process asyncio queue, and the searcher nudge stays cross-process.
_enricher_task: Optional[asyncio.Task] = None
_enricher_queue: Optional[asyncio.Queue] = None
# Nudges the searcher (a separate process) to re-tag once enrichment finishes;
# the searcher in turn nudges the embedder. Stays a cross-process queue.
_searcher_nudge_queue: Optional[multiprocessing.Queue] = None
_shutdown_event = asyncio.Event()


class EnrichmentStatus(enum.IntEnum):
    """Enum for enrichment status values"""

    NOT_ENRICHED = 0  # Initial state, needs enrichment
    ENRICHED = 1  # Successfully enriched with all required fields
    FAILED = 2  # Failed enrichment, missing required fields after all plugins


class MetadataEnricher:
    """Main enricher class that processes database entries using async"""

    def __init__(self, config: LocalFilesConfig, db_manager: AsyncEnricherDb):
        self.config = config
        self.db_manager = db_manager
        self.running = False
        self.lock = asyncio.Lock()
        self.plugins = []

        logger.info("Initializing MetadataEnricher plugins...")

        # The order of plugins matters for the enrichment process
        # as the first one found a match will be used
        if config.enricher.plugins.acoustid.enabled:
            logger.info("Loading AcoustIdPlugin")
            self.plugins.append(AcoustIdPlugin(config, self.db_manager))

        # Filesystem fallback plugin - always enabled and runs last
        # This provides basic metadata extraction from file paths when other plugins fail
        if config.enricher.plugins.filesystem_fallback_enabled:
            logger.info("Loading FilesystemFallbackPlugin")
            self.plugins.append(FilesystemFallbackPlugin(config, self.db_manager))

        if config.enricher.plugins.musicbrainz.enabled:
            logger.info("Loading MusicBrainzPlugin")
            self.plugins.append(MusicBrainzPlugin(config, self.db_manager))

        if config.enricher.plugins.wikidata.enabled:
            logger.info("Loading WikidataPlugin")
            self.plugins.append(WikidataPlugin(config, self.db_manager))

        if config.enricher.plugins.deezer.enabled:
            logger.info("Loading DeezerPlugin")
            self.plugins.append(DeezerPlugin(config, self.db_manager))

        # Generated album art is strictly last: it only fills covers no
        # real source could provide, so every fetching plugin above must
        # get its chance first.
        if config.enricher.plugins.procedural_artwork.enabled:
            if importlib.util.find_spec("numpy") is not None:
                logger.info("Loading ProceduralArtworkPlugin")
                from .procedural_artwork_plugin import ProceduralArtworkPlugin

                self.plugins.append(ProceduralArtworkPlugin(config, self.db_manager))
            else:
                logger.warning(
                    "Generated album art is enabled but numpy is not "
                    "installed; skipping. Use 'Restart with install' on the "
                    "modules page to fetch it."
                )

        logger.info(
            f"Loaded {len(self.plugins)} enrichment plugins: {[p.__class__.__name__ for p in self.plugins]}"
        )

    def compute_fingerprint(self) -> str:
        """Stable signature of the *active* enrichment setup.

        Built from ``self.plugins`` in load order — so it captures which
        plugins are enabled and in what order (first match wins), each
        one's ``ENRICHER_VERSION`` (bumped when its matching code
        changes), and its ``config_signature()`` (match-affecting config
        such as MB thresholds or whether an AcoustID key is set).

        Only enabled plugins contribute, which is the whole point:
        bumping the version of, or reconfiguring, a plugin the user has
        *disabled* leaves the fingerprint untouched and so triggers no
        needless retry sweep. The fingerprint changing is the signal
        that previously-FAILED rows deserve another attempt.
        """
        components = [
            {
                "name": p.__class__.__name__,
                "version": p.ENRICHER_VERSION,
                "config": p.config_signature(),
            }
            for p in self.plugins
        ]
        return json.dumps(components, sort_keys=True)

    async def start(self):
        """Start the enricher process for general enrichment"""
        async with self.lock:
            if self.running:
                logger.warning("Enricher already running, skipping")
                return
            self.running = True

        try:
            # Idle-path logs demoted to DEBUG: the indexer sends "enrich"
            # after every scheduled scan (default every 5 min), so on a
            # quiescent library the user used to see 11 INFO lines of
            # nothing-to-do noise per pass.
            logger.debug("Starting enrichment process")
            await self.run_enrichment()
            logger.debug("Enricher process completed")
        except Exception as e:
            logger.exception(f"Error running enricher: {e}")
        finally:
            async with self.lock:
                self.running = False

    async def run_enrichment(self):
        """Run the enrichment process"""
        totals = {"artists": 0, "albums": 0, "tracks": 0}
        while True:
            logger.debug("Processing artists for enrichment")
            artist_update_count = await self._process_artists()
            logger.debug("Processing albums for enrichment")
            album_update_count = await self._process_albums()
            logger.debug("Processing tracks for enrichment")
            track_update_count = await self._process_tracks()

            totals["artists"] += artist_update_count
            totals["albums"] += album_update_count
            totals["tracks"] += track_update_count

            total_updates = (
                artist_update_count + album_update_count + track_update_count
            )

            if total_updates == 0:
                logger.debug("No more items to process, finishing enrichment")
                break

        # Single INFO summary, only when the pass actually did work. On
        # an idle library this stays silent entirely.
        if any(totals.values()):
            logger.info(
                "Enrichment pass complete: %d artists, %d albums, %d tracks updated",
                totals["artists"],
                totals["albums"],
                totals["tracks"],
            )

    async def _process_artists(self) -> int:
        """Process non-enriched artists"""

        processed_artists = set()
        while True:
            logger.debug("Querying database for non-enriched artists...")
            artists = await self.db_manager.get_non_enriched_artists(limit=1)
            logger.debug(f"Found {len(artists)} non-enriched artists")
            if not artists:
                logger.debug("No more artists to process")
                return len(processed_artists)
            artist = artists[0]
            logger.debug(f"Processing artist: {artist}")
            await self._enrich_artist(artist)
            processed_artists.add(artist["id"])
            logger.debug(f"Processed artist {artist['name']}")

    async def _enrich_artist(self, artist):
        """Enrich a single artist"""
        logger.debug(
            f"Starting enrichment for artist {artist.get('name', artist['id'])}"
        )
        updated_artist = artist.copy()  # Make a copy to carry updates between plugins
        had_updates = False

        # Desired = the full target set (id + cover). Reaching it lets us skip
        # the plugins entirely; not reaching it is fine (see the tail).
        is_desired_complete = all(
            updated_artist.get(field) for field in ARTIST_DESIRED_FIELDS
        )
        if is_desired_complete:
            logger.debug(f"Artist {artist['id']} already has all desired fields")
            updated_artist["enriched"] = EnrichmentStatus.ENRICHED
            await self.db_manager.update_artist(
                artist["id"], {"enriched": EnrichmentStatus.ENRICHED}
            )
            return

        logger.debug(
            f"Artist {artist.get('name', artist['id'])} missing fields - starting plugin enrichment"
        )
        emitted_claims: list[dict] = []
        for plugin in self.plugins:
            if not plugin.can_enrich_artist():
                continue

            logger.debug(
                f"Enriching artist {artist['name'] if 'name' in artist else artist['id']} with {plugin.__class__.__name__}"
            )
            result = await plugin.enrich_artist(updated_artist)
            if result:
                emitted_claims.extend(result.get("claims") or [])
            if result and "updates" in result:
                # Apply updates to our working copy
                updated_artist.update(result["updates"])
                # Track that we had updates
                had_updates = True

                # Stop early once every desired field is filled.
                is_desired_complete = all(
                    updated_artist.get(field) for field in ARTIST_DESIRED_FIELDS
                )
                if is_desired_complete:
                    logger.debug(f"Artist {artist['name']} has all desired fields")
                    updated_artist["enriched"] = EnrichmentStatus.ENRICHED
                    break  # No need to check further plugins

        # Resolve the display name from claims: a matching external match
        # supplies canonical casing without replacing a different local name.
        if await self._resolve_artist_name(artist, updated_artist, emitted_claims):
            had_updates = True

        # Resolve external-first origin fields (country/area) from claims.
        if await self._resolve_external_fields(
            "artist", artist, updated_artist, emitted_claims, ARTIST_EXTERNAL_FIELDS
        ):
            had_updates = True

        # Status decision (Phase 2e): if external matching didn't fill every
        # desired field, the artist is still ENRICHED when its required local
        # fields resolved — only a missing required field is a FAILURE.
        if updated_artist.get("enriched") != EnrichmentStatus.ENRICHED:
            if all(updated_artist.get(f) for f in ARTIST_REQUIRED_FIELDS):
                updated_artist["enriched"] = EnrichmentStatus.ENRICHED  # local-only
            else:
                missing = [f for f in ARTIST_REQUIRED_FIELDS if not updated_artist.get(f)]
                logger.debug("Artist %s failed enrichment - missing: %s",
                             artist["id"], ", ".join(missing))
                updated_artist["enriched"] = EnrichmentStatus.FAILED
            had_updates = True

        # Only update the database once at the end if we had any updates
        if had_updates:
            await self.db_manager.update_artist(artist["id"], updated_artist)

    async def _resolve_artist_name(
        self, artist: dict, updated_artist: dict, emitted_claims: list
    ) -> bool:
        """Resolve the artist display name from claims. Returns True if the name
        changed. First field wired through the claims/resolution path (Phase
        2d): an external match re-cases a matching local name but never replaces
        a different one; provenance lands in resolved_origin."""
        local_name = artist.get("name")
        if not local_name:
            return False

        entity_id = artist["id"]
        external = []
        for c in emitted_claims:
            if c.get("field") != "name":
                continue
            await self.db_manager.record_claim(
                "artist", entity_id, "name", c["value"], c["source"], c["tier"]
            )
            external.append(
                Claim("name", c["value"], c["source"], c["tier"],
                      c.get("evidence_ref"))
            )

        # Record provenance for the resolved name even when the local value
        # wins uncontested (resolved_origin covers every resolved field).
        winner = resolve_display_name(local_name, external)
        await self.db_manager.record_resolved_origin(
            "artist", entity_id, "name", winner.source, winner.tier,
            winner.evidence_ref,
        )
        if winner.value != local_name:
            updated_artist["name"] = winner.value
            logger.debug(
                "Artist name resolved: %r -> %r (%s)",
                local_name, winner.value, winner.source,
            )
            return True
        return False

    async def _resolve_album_title(
        self, album: dict, updated_album: dict, emitted_claims: list
    ) -> bool:
        """Resolve the album display title from claims. Returns True if the
        title changed. Second field on the claims/resolution path (Phase 2d):
        an external release match re-cases a matching local title but never
        replaces a different one; provenance lands in resolved_origin."""
        local_title = album.get("title")
        if not local_title:
            return False

        entity_id = album["id"]
        external = []
        for c in emitted_claims:
            if c.get("field") != "title":
                continue
            await self.db_manager.record_claim(
                "album", entity_id, "title", c["value"], c["source"], c["tier"]
            )
            external.append(
                Claim("title", c["value"], c["source"], c["tier"],
                      c.get("evidence_ref"))
            )

        winner = resolve_display_name(local_title, external, field="title")
        await self.db_manager.record_resolved_origin(
            "album", entity_id, "title", winner.source, winner.tier,
            winner.evidence_ref,
        )
        if winner.value != local_title:
            updated_album["title"] = winner.value
            logger.debug(
                "Album title resolved: %r -> %r (%s)",
                local_title, winner.value, winner.source,
            )
            return True
        return False

    async def _resolve_external_fields(
        self,
        entity_type: str,
        entity: dict,
        updated: dict,
        emitted_claims: list,
        fields,
        local_overrides: Optional[dict] = None,
    ) -> bool:
        """Resolve external-first origin/era fields from claims (Phase 2d slice
        3). For each field, the local value is an `observed` tag_consensus claim
        and each emitted external value is `inferred`; the resolver picks the
        winner (local tag beats fuzzy MB for genre/year/language; MB fills
        country/area/original_year uncontested). Records the winner in
        resolved_origin and returns True if any value changed.

        ``local_overrides`` (Phase 2g) supplies the local value from actual tag
        evidence (§6.5 consensus) instead of the album row — the genuine
        observed claim — and, being evidence-derived, is persisted as a
        tag_consensus claim for provenance."""
        entity_id = entity["id"]
        overrides = local_overrides or {}
        ext_by_field: dict[str, list] = {}
        for c in emitted_claims:
            f = c.get("field")
            if f in fields:
                ext_by_field.setdefault(f, []).append(c)

        changed = False
        for field in fields:
            externals = ext_by_field.get(field, [])
            local_value = overrides.get(field, entity.get(field))
            claims = []
            if local_value not in (None, ""):
                claims.append(
                    Claim(field, local_value, "tag_consensus", "observed")
                )
                if field in overrides:
                    await self.db_manager.record_claim(
                        entity_type, entity_id, field, local_value,
                        "tag_consensus", "observed",
                    )
            for c in externals:
                await self.db_manager.record_claim(
                    entity_type, entity_id, field, c["value"], c["source"], c["tier"]
                )
                claims.append(
                    Claim(field, c["value"], c["source"], c["tier"],
                          c.get("evidence_ref"))
                )
            if not claims:
                continue

            winner = resolve_field(claims, current_value=local_value)
            await self.db_manager.record_resolved_origin(
                entity_type, entity_id, field, winner.source, winner.tier,
                winner.evidence_ref,
            )
            if winner.value != updated.get(field):
                updated[field] = winner.value
                changed = True
        return changed

    # Album origin/era fields with a local tag source (original_year has none).
    _ALBUM_TAG_FIELDS = ("genre", "year", "language")

    async def _album_tag_consensus(self, album_id: str) -> dict:
        """The album's genre/year/language as its tracks' tags agree (§6.5)."""
        track_tags = await self.db_manager.get_album_track_tags(album_id)
        return album_tag_consensus(track_tags, self._ALBUM_TAG_FIELDS)

    async def _process_albums(self) -> int:
        """Process non-enriched albums"""
        processed_albums = set()
        while True:
            logger.debug("Querying database for non-enriched albums...")
            albums = await self.db_manager.get_non_enriched_albums(limit=1)
            logger.debug(f"Found {len(albums)} non-enriched albums")
            if not albums:
                logger.debug("No more albums to process")
                return len(processed_albums)
            album = albums[0]
            logger.debug(f"Processing album: {album}")
            await self._enrich_album(album)
            processed_albums.add(album["id"])
            logger.debug(f"Processed album {album['title']}")

    async def _enrich_album(self, album):
        """Enrich a single album"""
        updated_album = album.copy()  # Make a copy to carry updates between plugins
        had_updates = False

        # Desired = full target set (external id, cover, year, genre). Reaching
        # it lets us skip the plugins; not reaching it is fine (see the tail).
        is_desired_complete = all(
            updated_album.get(field) for field in ALBUM_DESIRED_FIELDS
        )
        if is_desired_complete:
            logger.debug(f"Album {album['id']} already has all desired fields")
            updated_album["enriched"] = EnrichmentStatus.ENRICHED
            await self.db_manager.update_album(
                album["id"], {"enriched": EnrichmentStatus.ENRICHED}
            )
            return

        emitted_claims: list[dict] = []
        for plugin in self.plugins:
            if not plugin.can_enrich_album():
                continue

            logger.debug(
                f"Enriching album {album['name'] if 'name' in album else album['id']} with {plugin.__class__.__name__}"
            )
            result = await plugin.enrich_album(updated_album)
            if result:
                emitted_claims.extend(result.get("claims") or [])
            if result and "updates" in result:
                # Apply updates to our working copy
                updated_album.update(result["updates"])
                # Track that we had updates
                had_updates = True

                # Stop calling fetch plugins once every fetched field is filled.
                # genre/year are resolved after the loop, so they don't gate this.
                if all(updated_album.get(field) for field in ALBUM_FETCH_FIELDS):
                    logger.debug(f"Album {album['title']} has all fetched fields")
                    updated_album["enriched"] = EnrichmentStatus.ENRICHED
                    break

        # Resolve the display title from claims: a matching external match
        # supplies canonical casing without replacing a different local title.
        if await self._resolve_album_title(album, updated_album, emitted_claims):
            had_updates = True

        # Resolve external-first origin/era fields (genre/year/original_year/
        # language): local tags win over fuzzy MB where present. The local tag
        # value comes from tag-evidence consensus (§6.5), not the album row.
        consensus = await self._album_tag_consensus(album["id"])
        if await self._resolve_external_fields(
            "album", album, updated_album, emitted_claims, ALBUM_EXTERNAL_FIELDS,
            local_overrides=consensus,
        ):
            had_updates = True

        # Status decision (Phase 2e): an album whose required local fields
        # (title + artist_id) resolved is ENRICHED even without an external
        # match/cover/year/genre — "ENRICHED (local-only)". FAILED only when a
        # required local field is still missing.
        if updated_album.get("enriched") != EnrichmentStatus.ENRICHED:
            if all(updated_album.get(f) for f in ALBUM_REQUIRED_FIELDS):
                updated_album["enriched"] = EnrichmentStatus.ENRICHED  # local-only
            else:
                missing = [f for f in ALBUM_REQUIRED_FIELDS if not updated_album.get(f)]
                logger.debug("Album %s failed enrichment - missing: %s",
                             album["id"], ", ".join(missing))
                updated_album["enriched"] = EnrichmentStatus.FAILED
            had_updates = True

        # Only update the database once at the end if we had any updates
        if had_updates:
            await self.db_manager.update_album(album["id"], updated_album)

    async def _process_tracks(self) -> int:
        """Process non-enriched tracks"""
        processed_tracks = set()
        while True:
            logger.debug("Querying database for non-enriched tracks...")
            tracks = await self.db_manager.get_non_enriched_tracks(limit=1)
            logger.debug(f"Found {len(tracks)} non-enriched tracks")
            if not tracks:
                logger.debug("No more tracks to process")
                return len(processed_tracks)
            track = tracks[0]
            logger.debug(f"Processing track: {track}")
            await self._enrich_track(track)
            processed_tracks.add(track["id"])
            logger.debug(f"Processed track {track['title']}")

    async def _enrich_track(self, track):
        """Enrich a single track"""
        updated_track = track.copy()  # Make a copy to carry updates between plugins
        had_updates = False

        # Desired = full target set (incl. mbid). Reaching it lets us skip the
        # plugins; not reaching it is fine (see the tail).
        is_desired_complete = all(
            updated_track.get(field) for field in TRACK_DESIRED_FIELDS
        )
        if is_desired_complete:
            logger.debug(f"Track {track['id']} already has all desired fields")
            updated_track["enriched"] = EnrichmentStatus.ENRICHED
            await self.db_manager.update_track(
                track["id"], {"enriched": EnrichmentStatus.ENRICHED}
            )
            return

        for plugin in self.plugins:
            if not plugin.can_enrich_track():
                continue

            # Skip remaining plugins if we become fully enriched
            if updated_track.get("enriched") == EnrichmentStatus.ENRICHED:
                logger.debug(
                    f"Track {track['id']} fully enriched, skipping remaining plugins"
                )
                break

            logger.debug(
                f"Enriching track {track['title'] if 'title' in track else track['id']} with {plugin.__class__.__name__}"
            )
            result = await plugin.enrich_track(updated_track)
            if result and "updates" in result:
                # Apply updates to our working copy
                updated_track.update(result["updates"])
                # Track that we had updates
                had_updates = True

                # Stop early once every desired field is filled.
                is_desired_complete = all(
                    updated_track.get(field) for field in TRACK_DESIRED_FIELDS
                )
                if is_desired_complete:
                    logger.debug(f"Track {track['title']} has all desired fields")
                    updated_track["enriched"] = EnrichmentStatus.ENRICHED

        # Status decision (Phase 2e): a track whose required local fields
        # resolved is ENRICHED even without an mbid — "ENRICHED (local-only)".
        # FAILED only when a required local field is still missing.
        if updated_track.get("enriched") != EnrichmentStatus.ENRICHED:
            missing = [f for f in TRACK_REQUIRED_FIELDS if not updated_track.get(f)]
            if not missing:
                updated_track["enriched"] = EnrichmentStatus.ENRICHED  # local-only
            else:
                # file_path identifies the track even when title/artist are missing
                where = updated_track.get("file_path") or track["id"]
                logger.debug(
                    "Track %s failed enrichment - missing: %s",
                    where,
                    ", ".join(missing),
                )
                updated_track["enriched"] = EnrichmentStatus.FAILED
            had_updates = True

        # Only update the database once at the end if we had any updates
        if had_updates:
            await self.db_manager.update_track(track["id"], updated_track)
            if "album_id" in updated_track:
                await self.db_manager.update_album_stats(updated_track["album_id"])


async def _enricher_worker(config, db_manager: AsyncEnricherDb):
    """Background worker task for the enricher"""

    if _enricher_queue is None:
        logger.error("Enricher queue is not initialized; stopping worker")
        _shutdown_event.set()
        return

    enricher_instance = MetadataEnricher(config, db_manager)

    # One-time, *gated* retry of previously-FAILED rows. A plain restart
    # no longer re-hammers MusicBrainz/Deezer for rows that will fail
    # identically: FAILED rows are re-opened only when the enrichment
    # fingerprint changed since the last run — i.e. a plugin was
    # enabled/disabled/reordered, a plugin's ENRICHER_VERSION was bumped
    # (new matcher code), or a match-affecting config field changed (an
    # AcoustID key added, an MB threshold lowered). The first run after
    # this ships has no stored fingerprint, so it resets once and then
    # settles.
    try:
        fingerprint = enricher_instance.compute_fingerprint()
        retried = await db_manager.reset_failed_for_fingerprint(fingerprint)
        if retried is None:
            logger.info(
                "Enrichment fingerprint unchanged; leaving FAILED rows as-is"
            )
        else:
            logger.info(
                "Enrichment setup changed; reset %d previously-FAILED rows for "
                "retry: %d artists, %d albums, %d tracks",
                sum(retried.values()),
                retried["artists"],
                retried["albums"],
                retried["tracks"],
            )
    except Exception as e:
        logger.warning(f"Failed-row retry reset skipped: {e}")

    # Process queue commands
    while True:
        try:
            # Check for commands with timeout
            try:
                command = await asyncio.wait_for(
                    _enricher_queue.get(), timeout=30.0
                )

                logger.debug("Received command from queue: %s", command)

                if command == "stop":
                    logger.info("Stopping enricher task")
                    _shutdown_event.set()
                    break
                elif command == "enrich":
                    logger.debug("Manual enrichment triggered")
                    await enricher_instance.start()
                    if _searcher_nudge_queue is not None:
                        try:
                            _searcher_nudge_queue.put_nowait("nudge")
                        except Exception:
                            pass

            except asyncio.TimeoutError:
                # No command in the timeout window; loop and wait again.
                logger.debug("No enricher commands received; continuing to wait")
                continue

            # Small delay to avoid busy waiting
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info("Enricher worker cancelled.")
            break
        except Exception as e:
            logger.exception(f"Error in enricher worker: {str(e)}")
            await asyncio.sleep(5)  # Sleep longer on errors


def start_enricher(config, db_manager: AsyncEnricherDb) -> Optional[asyncio.Task]:
    """Start the enricher process in a background task"""
    global _enricher_task

    def log_task_result(task):
        try:
            task.result()  # Will raise if task failed
        except Exception as e:
            logger.exception(f"Enricher error: {e}")

    if _enricher_task and not _enricher_task.done():
        logger.warning("Enricher task already running, not starting another")
        return _enricher_task

    _enricher_task = asyncio.create_task(_enricher_worker(config, db_manager))
    _enricher_task.add_done_callback(log_task_result)

    logger.info("Started metadata enricher background task")
    return _enricher_task


async def stop_enricher() -> bool:
    """Stop the enricher task"""
    global _enricher_task
    if _enricher_task and not _enricher_task.done():
        logger.info("Sending stop command to enricher task")
        try:
            if _enricher_queue is None:
                logger.error("Enricher queue is not initialized; cannot send stop")
                return False
            _enricher_queue.put_nowait("stop")

            await asyncio.wait_for(_enricher_task, timeout=10.0)
            return True
        except asyncio.TimeoutError:
            logger.warning("Enricher task did not stop in time, cancelling...")
            _enricher_task.cancel()
            try:
                await asyncio.wait_for(_enricher_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            return True
        except Exception as e:
            logger.exception(f"Error stopping enricher task: {str(e)}")
            return False
    elif _enricher_task and _enricher_task.done():
        logger.info("Enricher task was already done.")
        return True
    logger.info("No active enricher task to stop.")
    return False


# Process orchestration lives in librarian.py, which runs this enricher's
# worker loop and the indexer's in one event loop (Phase 2b). start_enricher /
# stop_enricher above are its API.
