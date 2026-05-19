"""Shared scoring helpers for enricher plugins.

Match scoring is split across plugins (MusicBrainz, AcoustID) that need
the same duration-vs-candidate logic. Keeping it here avoids drift and
makes the thresholds unit-testable in one place.
"""

from typing import Optional


def duration_bonus(
    target_s: Optional[float], candidate_s: Optional[float]
) -> float:
    """Additive score adjustment for how well a candidate recording's
    duration matches the local file. Designed to sit on the same 0-100
    scale as the weighted title/similarity score in the matchers.

    Tiers:
      |dt| ≤ 2s   → +20  (very confident match)
      |dt| ≤ 5s   → +10
      |dt| ≤ 15s  →   0  (not informative either way)
      else        → -50  (almost certainly a different recording —
                          live cut, edit, demo, remix, etc.)

    Returns 0 when either side is missing/unparseable so callers can
    treat the bonus as opt-in.
    """
    if not target_s or not candidate_s:
        return 0.0
    try:
        dt = abs(float(target_s) - float(candidate_s))
    except (TypeError, ValueError):
        return 0.0
    if dt <= 2.0:
        return 20.0
    if dt <= 5.0:
        return 10.0
    if dt <= 15.0:
        return 0.0
    return -50.0


def parse_mb_length_seconds(length) -> Optional[float]:
    """Parse a MusicBrainz `length` value (ms, typically as a string)
    into seconds. Returns None if missing or unparseable.
    """
    if length is None:
        return None
    try:
        return int(length) / 1000.0
    except (TypeError, ValueError):
        return None


def album_duration_bonus(
    target_s: Optional[float], candidate_s: Optional[float]
) -> float:
    """Like ``duration_bonus`` but with album-scale tolerances.

    Albums are ~30-90 minutes; the per-track tolerance of ±2s/±5s is too
    tight for summed durations where small rounding errors compound. We
    also have to be lenient because MB's recording lengths sometimes
    omit gaps/silence between tracks.

    Tiers:
      |dt| ≤ 15s  → +20  (confident match)
      |dt| ≤ 60s  → +10
      |dt| ≤ 180s →   0  (not informative)
      else        → -50  (almost certainly a different release —
                          deluxe edition, bonus disc, reissue)
    """
    if not target_s or not candidate_s:
        return 0.0
    try:
        dt = abs(float(target_s) - float(candidate_s))
    except (TypeError, ValueError):
        return 0.0
    if dt <= 15.0:
        return 20.0
    if dt <= 60.0:
        return 10.0
    if dt <= 180.0:
        return 0.0
    return -50.0


def track_count_bonus(
    target_count: Optional[int], candidate_count: Optional[int]
) -> float:
    """Additive score adjustment for how well a release's track count
    matches the local album's. Sits on the same 0-100 scale as the
    title/similarity score.

    Tiers:
      exact       → +15  (very strong: distinguishes standard / deluxe / hits)
      off by 1    →  +5
      off by ≥ 3  → -20  (different edition)

    Returns 0 when either side is missing.
    """
    if target_count is None or candidate_count is None:
        return 0.0
    try:
        diff = abs(int(target_count) - int(candidate_count))
    except (TypeError, ValueError):
        return 0.0
    if diff == 0:
        return 15.0
    if diff == 1:
        return 5.0
    if diff >= 3:
        return -20.0
    return 0.0


def release_total_length_seconds(mb_release) -> Optional[float]:
    """Sum every recording's ``length`` across all media of a release.

    The release must have been fetched with ``includes=["recordings"]``.
    Returns None if no track lengths were available so callers can
    treat the sum as opt-in.
    """
    if not mb_release:
        return None
    media = mb_release.get("medium-list") or []
    total = 0.0
    saw_any = False
    for medium in media:
        for tr in medium.get("track-list") or []:
            length = tr.get("length") or (tr.get("recording") or {}).get("length")
            secs = parse_mb_length_seconds(length)
            if secs is not None:
                total += secs
                saw_any = True
    return total if saw_any else None


def parse_mb_track_count(release) -> Optional[int]:
    """Extract the total track count from a MusicBrainz release as
    returned by ``search_releases``.

    Tries (in order): top-level ``medium-track-count``, top-level
    ``track-count``, then sums per-medium ``track-count`` from
    ``medium-list``. Returns ``None`` if the count is unknown OR all
    fields parse to zero (no real release has zero tracks, so a zero
    is almost certainly a missing-data artifact that the caller
    should treat as "no signal" rather than "definitely 0 tracks").
    """
    for key in ("medium-track-count", "track-count"):
        v = release.get(key)
        if v is None:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    total = 0
    for medium in release.get("medium-list") or []:
        v = medium.get("track-count")
        if v is None:
            continue
        try:
            total += int(v)
        except (TypeError, ValueError):
            pass
    return total if total > 0 else None
