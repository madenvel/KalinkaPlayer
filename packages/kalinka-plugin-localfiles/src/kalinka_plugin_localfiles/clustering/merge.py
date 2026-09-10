"""Cross-folder disc-sibling merge (§5.3 merge rule).

Sibling folders whose names differ only by a disc/volume suffix (``… CD1`` /
``… CD2`` / ``… CD3`` as separate directories — the case
``album_folder_for_path`` does *not* already collapse) are one multi-disc
album. Bare ``CD1``/``Disc 2`` subdirs are handled upstream by
``album_folder_for_path``; this covers disc suffixes carried in the folder
*name*.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import List

from ..utils.name_utils import normalize_for_id
from .classify import strip_disc_suffix
from .planner import ClusterPlan


def _merge_key(folder: str):
    """(parent dir, disc-suffix-stripped basename) — the shared album key."""
    parent = os.path.dirname(folder)
    base = os.path.basename(folder.rstrip("/"))
    return parent, normalize_for_id(strip_disc_suffix(base))


def _compatible(clusters: List[ClusterPlan]) -> bool:
    """Same album-artist and complementary (disjoint) disc numbers."""
    aa = {c.albumartist_key for c in clusters if c.albumartist_key}
    if len(aa) > 1:
        return False
    seen: set = set()
    for c in clusters:
        if c.disc_numbers & seen:
            return False  # a disc number repeats across siblings
        seen |= c.disc_numbers
    return True


def merge_disc_siblings(clusters: List[ClusterPlan]) -> List[ClusterPlan]:
    """Fuse album clusters that are disc-siblings; pass the rest through."""
    groups = defaultdict(list)
    for c in clusters:
        # Only plain albums merge; compilations/singles-pools are left alone.
        if c.kind == "album" and c.folder:
            groups[_merge_key(c.folder)].append(c)
        else:
            groups[id(c)].append(c)  # unique key -> never grouped

    out: List[ClusterPlan] = []
    for key, members in groups.items():
        # A real disc-sibling group needs >=2 distinct folders under one parent
        # whose stripped basenames match, and compatible disc/artist evidence.
        distinct_folders = {m.folder for m in members}
        if len(members) < 2 or len(distinct_folders) < 2 or not _compatible(members):
            out.extend(members)
            continue
        primary = max(members, key=lambda m: len(m.track_ids))
        track_ids: List[str] = []
        discs: frozenset = frozenset()
        for m in members:
            track_ids.extend(m.track_ids)
            discs |= m.disc_numbers
        out.append(
            ClusterPlan(
                track_ids=track_ids,
                kind="multi_disc",
                title=strip_disc_suffix(primary.title),
                anchor_artist_id=primary.anchor_artist_id,
                grouping_basis={"reason": "disc_sibling_merge",
                                "folders": sorted(distinct_folders)},
                folder=os.path.dirname(primary.folder) or primary.folder,
                disc_numbers=discs,
                albumartist_key=primary.albumartist_key,
                title_source=primary.title_source,
                year=primary.year,
            )
        )
    return out
