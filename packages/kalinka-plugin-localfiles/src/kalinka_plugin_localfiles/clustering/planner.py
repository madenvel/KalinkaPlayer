"""Turn real track + evidence rows into a per-folder cluster plan.

Read-only: builds ``TrackFeatures`` from the display row plus its
``track_evidence``, runs the partition engine, and classifies each resulting
group (album / compilation / singles_pool). It proposes assignments; applying
them (album_id reassignment, album_cluster writes) is a later step.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..utils.name_utils import clean_display_name, normalize_for_id
from .classify import VARIOUS_ARTISTS_ID, compilation_title, is_va_folder, strip_disc_suffix
from .engine import partition_folder
from .signals import TrackFeatures

# Tag names for the same concept across ID3 (MP3) and Vorbis (FLAC).
_ALBUM_TAGS = ("album", "TALB")
_ALBUMARTIST_TAGS = ("albumartist", "album artist", "TPE2")
_COMPILATION_TAGS = ("compilation", "TCMP")


def _loads(s: Optional[str]) -> Dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return {}


def _tag(raw: Dict, *keys: str) -> Optional[str]:
    for k in keys:
        if k in raw:
            v = raw[k]
            v = v[0] if isinstance(v, list) else v
            if v not in (None, ""):
                return str(v)
    return None


def _album_display(raw: Dict) -> Optional[str]:
    album = _tag(raw, *_ALBUM_TAGS)
    return clean_display_name(album) if album else None


def build_features(track: Dict, evidence: Optional[Dict]) -> TrackFeatures:
    """Assemble the scorer's per-track features from a track row + evidence."""
    evidence = evidence or {}
    raw = _loads(evidence.get("raw_tags"))
    stream = _loads(evidence.get("stream_info"))
    art_phash = evidence.get("art_phash")
    cue_sheet = evidence.get("cue_sheet")
    import_batch = evidence.get("import_batch")

    album_display = _album_display(raw)
    # Album key: normalized, disc-suffix stripped so "X (Disc 1)"/"X (Disc 2)"
    # share a bucket and are distinguished by disc_number within the cluster.
    album_key = None
    if album_display:
        album_key = normalize_for_id(strip_disc_suffix(album_display)) or None

    albumartist = _tag(raw, *_ALBUMARTIST_TAGS)
    albumartist_key = normalize_for_id(albumartist) if albumartist else None
    compilation = (_tag(raw, *_COMPILATION_TAGS) or "").strip() in ("1", "true", "True")

    stream_key = None
    if stream:
        stream_key = (
            f"{stream.get('codec')}|{stream.get('sample_rate')}|"
            f"{stream.get('bits_per_sample')}"
        )

    return TrackFeatures(
        track_id=track["id"],
        album_key=album_key,
        albumartist_key=albumartist_key,
        artist_key=track.get("artist_id"),
        track_number=track.get("track_number"),
        disc_number=track.get("disc_number"),
        art_phash=art_phash,
        stream_key=stream_key,
        compilation=compilation,
        cue_sheet=cue_sheet,
        import_batch=import_batch,
    )


@dataclass
class ClusterPlan:
    track_ids: List[str]
    kind: str                       # album | compilation | singles_pool
    title: str                      # "" for singles_pool (stays unknown_album)
    anchor_artist_id: str
    grouping_basis: Dict[str, object] = field(default_factory=dict)


@dataclass
class FolderPlan:
    folder: str
    clusters: List[ClusterPlan]
    split: bool = False


def _dominant(values) -> Optional[str]:
    vals = [v for v in values if v]
    return Counter(vals).most_common(1)[0][0] if vals else None


def plan_folder(folder: str, rows: List[Tuple[Dict, Optional[Dict]]]) -> FolderPlan:
    """Plan the clusters for one folder from (track_row, evidence_row) pairs."""
    features = [build_features(t, e) for t, e in rows]
    display_by_id = {
        t["id"]: (t, _album_display(_loads((e or {}).get("raw_tags"))))
        for t, e in rows
    }

    result = partition_folder(features)
    clusters: List[ClusterPlan] = []
    for group in result.groups:
        ids = [f.track_id for f in group]
        member_tracks = [display_by_id[i][0] for i in ids]
        real_artists = {
            t.get("artist_id")
            for t in member_tracks
            if t.get("artist_id") and t.get("artist_id") != "unknown_artist"
        }

        if is_va_folder(len(real_artists), len(ids)):
            comp = compilation_title(folder)
            if comp is None:
                # A generic dump — leave tracks loose (unknown_album), no album.
                clusters.append(
                    ClusterPlan(ids, "singles_pool", "", "unknown_artist",
                                {"reason": "generic_dump"})
                )
                continue
            clusters.append(
                ClusterPlan(ids, "compilation", comp, VARIOUS_ARTISTS_ID,
                            {"reason": "va_compilation"})
            )
            continue

        # A normal album: title from tag consensus, else the folder name so an
        # untagged rip still gets a real album (today it falls to unknown_album).
        titles = [display_by_id[i][1] for i in ids]
        title = _dominant(titles) or os.path.basename(folder.rstrip("/")) or ""
        anchor = _dominant(t.get("artist_id") for t in member_tracks) or "unknown_artist"
        clusters.append(
            ClusterPlan(ids, "album", title, anchor, dict(result.basis))
        )

    return FolderPlan(folder=folder, clusters=clusters, split=result.split)
