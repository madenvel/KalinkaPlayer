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
