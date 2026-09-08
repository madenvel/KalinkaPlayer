"""Turning what a client asked to play into tracks the queue can hold.

A container is expanded by browsing it, and every track that comes back is
resolved through the source that owns *it* — not through the container's. The
two are the same source for an album, and need not be for a collection, whose
rows come from wherever they were collected. Resolving by the container would
ask one source for another's ids.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Dict, List, Sequence

from kalinka_plugin_sdk.datamodel import EntityId, EntityType
from kalinka_plugin_sdk.inputmodule import InputModule, TrackInfo

from .browse_source import BrowseSource

logger = logging.getLogger(__name__.split(".")[-1])

# A container's tracks are taken in one page; beyond this a client pages itself.
CONTAINER_LIMIT = 5000


async def track_infos_for(
    ids: Sequence[str],
    browse_source_for: Callable[[EntityId], BrowseSource],
    module_for: Callable[[str], InputModule],
) -> List[TrackInfo]:
    """The tracks behind ``ids``, in the order they were asked for.

    Ids may name tracks or containers, and a container may hold tracks from
    several sources. Each owning source is asked once for the distinct ids it
    owns; a track it does not return is left out rather than faked.

    Raises:
        Whatever a source's browse or lookup raises — a partial queue is worse
        than a failed add, since the gap is silent.
    """
    wanted: List[EntityId] = []
    for raw in ids:
        entity_id = EntityId.from_string(raw)
        if entity_id.type == EntityType.TRACK:
            wanted.append(entity_id)
            continue
        listing = await browse_source_for(entity_id).browse(
            entity_id, offset=0, limit=CONTAINER_LIMIT
        )
        wanted.extend(
            item.id for item in listing.items if item.id.type == EntityType.TRACK
        )

    by_source: Dict[str, List[str]] = defaultdict(list)
    for entity_id in wanted:
        by_source[entity_id.source].append(entity_id.id)

    resolved: Dict[str, TrackInfo] = {}
    for source, local_ids in by_source.items():
        infos = await module_for(source).get_track_info(list(dict.fromkeys(local_ids)))
        for info in infos:
            resolved[info.id.to_string] = info

    tracks = [
        resolved[entity_id.to_string]
        for entity_id in wanted
        if entity_id.to_string in resolved
    ]
    if len(tracks) != len(wanted):
        logger.warning(
            "Queue add: %d of %d tracks could not be resolved",
            len(wanted) - len(tracks),
            len(wanted),
        )
    return tracks
