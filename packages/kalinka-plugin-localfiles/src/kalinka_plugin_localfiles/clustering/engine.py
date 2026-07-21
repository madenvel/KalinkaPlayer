"""Folder-partition engine (§5.3).

Decides how many local albums a folder holds. The default is one; a split
happens only on compelling evidence (a folder that partitions into substantial
subgroups which the signal scorer judges as genuinely distinct). Minority
tag/art outliers are absorbed rather than split off.

Pure over ``TrackFeatures`` — no DB, no config.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .signals import TrackFeatures, pair_signal_score


@dataclass
class PartitionResult:
    groups: List[List[TrackFeatures]]
    split: bool = False
    # Why a split happened / why not — for grouping_basis.
    basis: Dict[str, object] = field(default_factory=dict)


def partition_folder(tracks: List[TrackFeatures]) -> PartitionResult:
    """Partition one folder's tracks into album clusters.

    The album tag is the only axis that *proposes* a split — tracks bucket by
    normalized album tag (untagged tracks share one bucket). Buckets are then
    agglomeratively merged while the best pair still scores >= 0, so a split
    survives only between buckets the signal scorer judges genuinely distinct
    (different art, colliding track numbers with distinct complete runs,
    different album-artist, disjoint disc sequences). Tag-only variance scores
    non-negative and merges, and a mistagged outlier with no distinct art or
    own sequence is absorbed into the majority.
    """
    n = len(tracks)
    if n <= 1:
        return PartitionResult(groups=[list(tracks)])

    buckets: Dict[Optional[str], List[TrackFeatures]] = defaultdict(list)
    for t in tracks:
        buckets[t.album_key].append(t)

    groups = [list(g) for g in buckets.values()]
    if len(groups) == 1:
        return PartitionResult(groups=groups)  # one tag (or all untagged)

    while len(groups) > 1:
        best = None  # (score, i, j)
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                s = pair_signal_score(groups[i], groups[j])[0]
                if best is None or s > best[0]:
                    best = (s, i, j)
        if best is None or best[0] < 0:
            break  # every remaining pair is compelling split evidence
        _, i, j = best
        groups[i].extend(groups[j])
        del groups[j]

    split = len(groups) > 1
    return PartitionResult(
        groups=groups,
        split=split,
        basis={
            "reason": "multi_album_split" if split else "single_album",
            "tag_buckets": len(buckets),
            "final_groups": len(groups),
        },
    )
