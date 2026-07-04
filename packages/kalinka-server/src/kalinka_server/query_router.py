"""Semantic routing from a search query to catalog shelves.

Queries like "recently added to the library" or "new releases on jamendo"
name a *place* in a module's browse tree, not a track. At startup the router
browses every enabled module's root catalog and embeds each shelf card with
the shared text embedder; at query time the query is embedded once and
KNN-matched against that table. Hits are re-emitted as the module's own root
card, prepended above the BEST MATCH / AI legs (additive — routing never
replaces the normal search, so a false hit costs one extra card, not the
results).

False-hit control is layered, cheapest first:

  * **Lexical module anchor** — if the query names a module ("... on
    jamendo"), only that module's shelves compete. Cosine similarity barely
    notices which source a query names; exact string match does.
  * **Decoy anchors** — a static set of counter-intent exemplars (mood /
    genre / similarity phrasings that must go to AI search) is embedded into
    the same table. A shelf must beat the best decoy by a margin: a relative
    comparison, far more stable than an absolute cosine cut-off.
  * **Similarity floor** — a loose absolute minimum below which nothing
    routes regardless of decoys.
  * **Name-lookup veto** — applied by the caller (ai_search assembly): when
    the query is a near-exact match of a BEST MATCH name ("New Order" is a
    band, not the "New Releases" shelf), routed cards are dropped.

The floor/margin defaults are educated guesses pending a measured benchmark
(labelled catalog-intent vs search-intent queries); tune via SearchConfig.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

from kalinka_plugin_sdk.datamodel import BrowseItem, EntityId, EntityType
from kalinka_plugin_sdk.embedding import TextEmbedder
from kalinka_plugin_sdk.inputmodule import InputModule

from .config_model import SearchConfig

logger = logging.getLogger(__name__.split(".")[-1])

# How long one module may take to serve its root catalog during rebuild.
_BROWSE_TIMEOUT_S = 10.0
_ROOT_PAGE_LIMIT = 50
# Preview items fetched per routed shelf when its own preview_config doesn't
# say (matches the home screen's per-shelf preview fetch).
_PREVIEW_LIMIT = 10
# Qualifying shelves scoring more than this below the best one are dropped —
# they cleared the floor on shared vocabulary, not on being what was asked.
_TOP_GAP = 0.15

# Counter-intent exemplars: queries that must fall through to FTS / AI search.
# A shelf only routes if it beats every one of these by the configured margin,
# so the mood/genre/similarity phrasings that dominate real usage never land
# on a shelf just because nothing better matched.
_DECOYS: Tuple[str, ...] = (
    "something calm and melancholic for tonight",
    "upbeat energetic music for working out",
    "songs about love and heartbreak",
    "music similar to my favourite artist",
    "acoustic guitar instrumental pieces",
    "smooth jazz with piano and saxophone",
    "play some classic rock",
    "indie songs with female vocals",
    "relaxing background music for studying",
    "fast aggressive heavy metal",
)


@dataclass
class _Route:
    card: BrowseItem          # the module's own root card, re-emitted on a hit
    module: InputModule       # browsed into at query time to fill the preview
    source: str               # plugin key, e.g. "jamendo" (EntityId.source)
    # module_name() — what assemble_ai_search identifies modules by. NOT the
    # same as the plugin key for every module ("Jamendo" vs "jamendo").
    match_key: str
    display_name: str         # e.g. "Jamendo"
    aliases: Tuple[str, ...]  # lexical anchors for the module-mention filter
    vecs: list                # one vector per text variant; score = max


def _variants(card: BrowseItem, display_name: str) -> List[str]:
    """Text variants embedded per shelf. Kept separate (scored max, not
    concatenated): mean-pooling one long string dilutes every part, while
    separate variants let "recently added" match the bare title and "new
    additions on jamendo" match the module-qualified one."""
    title = (card.catalog.title if card.catalog and card.catalog.title
             else card.name) or ""
    title = title.strip()
    if not title:
        return []
    out = [title, f"{title} on {display_name}"]
    desc = (card.catalog.description or "").strip() if card.catalog else ""
    if desc and desc.lower() != title.lower():
        out.append(f"{title}. {desc}")
    return out


def _mentioned_sources(query: str, routes: Sequence[_Route]) -> Set[str]:
    """Sources whose name appears as a whole word in the query."""
    q = query.lower()
    hits: Set[str] = set()
    for r in routes:
        if any(re.search(rf"\b{re.escape(a)}\b", q) for a in r.aliases):
            hits.add(r.source)
    return hits


class CatalogRouter:
    """Routing table over the enabled modules' root shelves.

    ``rebuild()`` is called once after module setup (a config change restarts
    the server, so the table can't go stale within a run). ``route()`` is safe
    to call any time: before the rebuild finishes — or if the embedder is
    unavailable — it just returns no routes.
    """

    def __init__(self, embedder: Optional[TextEmbedder]):
        self._embedder = embedder
        self._routes: List[_Route] = []
        self._decoy_vecs: list = []
        self._ready = False

    async def rebuild(self, module_pairs: Sequence[Tuple[str, InputModule]]) -> None:
        """Browse each module's root and embed the shelf cards.

        ``module_pairs`` is (module key, interface) for every enabled input
        module. Failures degrade per module; an unavailable embedder turns
        routing off entirely.
        """
        if self._embedder is None or not await self._embedder.available():
            logger.info("text embedder unavailable; catalog routing off")
            return

        routes: List[_Route] = []
        texts: List[str] = []
        spans: List[Tuple[int, int]] = []  # texts[start:end] belong to route i
        for name, module in module_pairs:
            for card in await self._root_cards(name, module):
                display = module.display_name()
                variants = _variants(card, display)
                if not variants:
                    continue
                spans.append((len(texts), len(texts) + len(variants)))
                texts.extend(variants)
                routes.append(_Route(
                    card=card,
                    module=module,
                    source=name,
                    match_key=module.module_name(),
                    display_name=display,
                    aliases=tuple({
                        name.lower(), display.lower(),
                        module.module_name().lower(),
                    }),
                    vecs=[],
                ))
        if not routes:
            logger.info("no root shelves found; catalog routing off")
            return

        try:
            vecs = await self._embedder.embed(texts + list(_DECOYS))
        except Exception as e:
            logger.warning("route table embedding failed (%s); routing off", e)
            return
        for route, (start, end) in zip(routes, spans):
            route.vecs = vecs[start:end]
        # Swap atomically (single event loop): a concurrent route() sees
        # either the old table or the complete new one.
        self._routes = routes
        self._decoy_vecs = vecs[len(texts):]
        self._ready = True
        logger.info("catalog routing table ready: %d shelves from %d modules",
                    len(routes), len(module_pairs))

    async def _root_cards(self, name: str, module: InputModule) -> List[BrowseItem]:
        root = EntityId(id="root", type=EntityType.CATALOG, source=name)
        try:
            listing = await asyncio.wait_for(
                module.browse(root, 0, _ROOT_PAGE_LIMIT), _BROWSE_TIMEOUT_S)
        except Exception as e:
            logger.warning("root browse of %s failed (%s); no routes for it",
                           name, e)
            return []
        return [it for it in listing.items if it.can_browse and it.catalog]

    async def route(
        self,
        query: str,
        allowed_sources: Optional[Set[str]],
        cfg: SearchConfig,
    ) -> List[BrowseItem]:
        """Return the routed shelf cards for ``query``, best first.

        ``allowed_sources`` restricts candidates to the modules the request
        targeted (the /ai_search ``sources`` param); None means all. Never
        raises — routing failures must not sink the search.
        """
        if not self._ready or self._embedder is None:
            return []
        candidates = self._routes
        if allowed_sources is not None:
            candidates = [r for r in candidates if r.match_key in allowed_sources]
        mentioned = _mentioned_sources(query, candidates)
        if mentioned:
            candidates = [r for r in candidates if r.source in mentioned]
        if not candidates:
            return []

        try:
            q = (await self._embedder.embed([query]))[0]
        except Exception as e:
            logger.warning("query embedding failed (%s); no routes", e)
            return []

        # Vectors are L2-normalized, so dot product == cosine similarity.
        decoy_best = max(float(q @ d) for d in self._decoy_vecs)
        floor = cfg.route_min_similarity / 100.0
        margin = cfg.route_decoy_margin / 100.0
        scored = []
        for r in candidates:
            score = max(float(q @ v) for v in r.vecs)
            if score >= floor and score >= decoy_best + margin:
                scored.append((score, r))
        scored.sort(key=lambda sr: -sr[0])
        # Trim shelves far below the winner: "popular on jamendo" scores
        # Popular Tracks 0.85 but drags New Releases along at 0.63 — above
        # the floor yet clearly not what was asked for.
        if scored:
            top = scored[0][0]
            scored = [sr for sr in scored if sr[0] >= top - _TOP_GAP]
        if scored:
            logger.info(
                "routed %r -> %s (decoy best %.2f)", query,
                [(f"{s:.2f}", r.source, r.card.name) for s, r in scored], decoy_best)
        cards = await asyncio.gather(
            *(self._present(r) for _, r in scored[: cfg.route_max_results]))
        return [c for c in cards if c is not None]

    async def _present(self, route: _Route) -> Optional[BrowseItem]:
        """The shelf as a self-contained ai_search section: browse into it for
        the preview items and attach them inline. The search feed renders each
        section's inline ``sections`` (and drops empty ones), so a bare browse
        pointer would never show — unlike the home screen, it does not lazy-load
        previews. Retitled with the source like BEST MATCH; ``can_browse`` stays
        so the header still opens the full shelf. None when the shelf is empty
        (nothing to preview) or the browse fails."""
        preview = route.card.catalog.preview_config if route.card.catalog else None
        limit = preview.items_count if preview and preview.items_count else _PREVIEW_LIMIT
        try:
            listing = await asyncio.wait_for(
                route.module.browse(route.card.id, 0, limit), _BROWSE_TIMEOUT_S)
        except Exception as e:
            logger.warning("preview browse of %s/%s failed (%s); shelf dropped",
                           route.source, route.card.id.id, e)
            return None
        if not listing.items:
            return None
        card = route.card.model_copy(deep=True)
        title = f"{card.name} · {route.display_name}"
        card.name = title
        if card.catalog is not None:
            card.catalog.title = title
        card.sections = list(listing.items)
        return card
