"""Stable album-id assignment for a re-cluster.

A cluster keeps an existing album id when it holds the plurality (>= half) of
that album's current members — so a re-cluster of an unchanged library is a
semantic no-op and artwork/playlist references survive. Retired ids are
aliased to their successor. Genuinely new clusters get a mint that is stable
across runs for the same membership.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from .planner import ClusterPlan

_SENTINELS = ("unknown_album", "")


def _mint(cluster: ClusterPlan) -> str:
    payload = (cluster.folder + "\0" + "|".join(sorted(cluster.track_ids)))
    return f"album_{hashlib.md5(payload.encode('utf-8')).hexdigest()[:16]}"


def assign_stable_ids(
    clusters: List[ClusterPlan],
    current_album_of: Dict[str, str],
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Return (album_id per cluster, [(old_id, new_id)] aliases).

    ``current_album_of`` maps track_id -> its current album_id.
    """
    track_cluster: Dict[str, int] = {}
    for ci, c in enumerate(clusters):
        for tid in c.track_ids:
            track_cluster[tid] = ci

    # For each old album, how many of its current members landed in each cluster.
    old_counts: Dict[str, Counter] = defaultdict(Counter)
    old_totals: Counter = Counter()
    for tid, old in current_album_of.items():
        ci = track_cluster.get(tid)
        if ci is not None:
            old_counts[old][ci] += 1
            old_totals[old] += 1

    # Each non-sentinel old album is claimed by its plurality cluster, but only
    # if that cluster holds >= half of the album's members (overlap guard).
    # Resolve greedily by overlap so a bigger claim wins a contested cluster.
    candidates: List[Tuple[int, str, int]] = []
    for old, counts in old_counts.items():
        if old in _SENTINELS:
            continue
        ci, n = counts.most_common(1)[0]
        if n / old_totals[old] >= 0.5:
            candidates.append((n, old, ci))
    candidates.sort(key=lambda x: (-x[0], x[1]))

    cluster_claim: Dict[int, str] = {}
    used_old: set = set()
    for n, old, ci in candidates:
        if ci in cluster_claim or old in used_old:
            continue
        cluster_claim[ci] = old
        used_old.add(old)

    ids: List[str] = []
    for ci, c in enumerate(clusters):
        if c.kind == "singles_pool":
            ids.append("unknown_album")
        elif ci in cluster_claim:
            ids.append(cluster_claim[ci])
        else:
            ids.append(_mint(c))

    # Retired old ids (not kept by any cluster) alias to their dominant cluster.
    aliases: List[Tuple[str, str]] = []
    for old, counts in old_counts.items():
        if old in _SENTINELS or old in used_old:
            continue
        ci = counts.most_common(1)[0][0]
        new_id = ids[ci]
        if new_id != old and new_id not in _SENTINELS:
            aliases.append((old, new_id))

    return ids, aliases
