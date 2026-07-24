"""Tracklist alignment (Phase 3, §6.2).

Score a local album's tracks against a candidate release's tracklist by a
monotonic (order-preserving) sequence alignment — Needleman-Wunsch, not free
assignment. Albums have an inherent order and absent tracks behave like gaps,
not permutations: Hungarian matching would happily map local track 9 to release
position 2 on a title coincidence; monotonicity forbids that.

Pure and requestless. Returns coverage (fraction of local tracks the release
explains) + a track_map (local track -> release disc/pos/recording id) for the
accepted matches; the DB wiring and decision policy live in the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

# Reward space is 0..1. A pairing must clear MATCH_FLOOR to be worth more than
# gapping past both tracks, so dissimilar pairs align to gaps instead of forcing
# a spurious match. GAP is small: bonus/missing tracks are common and expected.
# One threshold serves both roles — a pairing earns positive reward, and counts
# as a match, on the same bar — so the two can never drift apart.
MATCH_FLOOR = 0.5
GAP = -0.1
_TITLE_W = 0.7
_DUR_W = 0.3
_DUR_TOL_S = 15.0


@dataclass(frozen=True)
class Alignment:
    coverage: float           # matched local tracks / total local tracks
    # Mean quality of the *matched* pairs only — deliberately independent of
    # coverage, so it says nothing about how much of the album was explained
    # (one perfect match out of twelve tracks scores 1.0). Callers must gate on
    # coverage AND score, never score alone.
    score: float
    track_map: Dict[str, list]  # local_id -> [disc, pos, rec_id]
    matched: int
    total: int


_PAREN_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")

# A stripped-suffix match can never quite equal a full match, so an exact pair
# still outranks a suffix-tolerant one when both are available.
_STRIPPED_DISCOUNT = 0.95


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().casefold()


def _title_sim(a: Optional[str], b: Optional[str]) -> float:
    """Similarity that tolerates a trailing parenthetical on either side.

    Local rips routinely carry a suffix the provider lacks — "(Lennon-McCartney)",
    "(Remastered 2009)", "(feat. X)" — which drags a true match under the floor
    (measured: 4 Abbey Road tracks at 0.42-0.46). Compare both raw and
    suffix-stripped, keeping the better, discounted reading.
    """
    na, nb = _norm(a), _norm(b)
    full = SequenceMatcher(None, na, nb).ratio()
    sa, sb = _PAREN_RE.sub("", na), _PAREN_RE.sub("", nb)
    if sa == na and sb == nb:
        return full
    stripped = SequenceMatcher(None, sa, sb).ratio()
    return max(full, _STRIPPED_DISCOUNT * stripped)


def _pair_score(local: Dict[str, Any], rel: Dict[str, Any]) -> float:
    """Quality of pairing one local track with one release track (0..1): title
    similarity, refined by duration agreement when both are known."""
    sim = _title_sim(local.get("title"), rel.get("title"))
    # Truthiness is right here: the indexer defaults a missing duration to 0,
    # and a 0-second track carries no timing evidence either way.
    ld, rd = local.get("duration"), rel.get("length_s")
    if ld and rd:
        dur = max(0.0, 1.0 - abs(ld - rd) / _DUR_TOL_S)
        return _TITLE_W * sim + _DUR_W * dur
    return sim


def align_tracklist(
    local_tracks: List[Dict[str, Any]], release_tracks: List[Dict[str, Any]]
) -> Alignment:
    """Align ``local_tracks`` (ordered, each with id/title/duration) against
    ``release_tracks`` (ordered, each with title/length_s/disc/pos/rec_id)."""
    n, m = len(local_tracks), len(release_tracks)
    if n == 0 or m == 0:
        return Alignment(0.0, 0.0, {}, 0, n)

    # dp[i][j] = best alignment reward of first i local vs first j release tracks.
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    bt: List[List[Optional[str]]] = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + GAP
        bt[i][0] = "up"
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + GAP
        bt[0][j] = "left"

    pair: Dict[tuple, float] = {}
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            p = _pair_score(local_tracks[i - 1], release_tracks[j - 1])
            diag = dp[i - 1][j - 1] + (p - MATCH_FLOOR)
            up = dp[i - 1][j] + GAP          # local track i is a bonus/extra
            left = dp[i][j - 1] + GAP        # release track j is missing locally
            best = max(diag, up, left)
            dp[i][j] = best
            if best == diag:
                bt[i][j], pair[(i, j)] = "diag", p
            elif best == up:
                bt[i][j] = "up"
            else:
                bt[i][j] = "left"

    track_map: Dict[str, list] = {}
    matched, score_sum = 0, 0.0
    i, j = n, m
    while i > 0 and j > 0:
        move = bt[i][j]
        if move == "diag":
            if pair[(i, j)] >= MATCH_FLOOR:
                lt, rt = local_tracks[i - 1], release_tracks[j - 1]
                track_map[lt["id"]] = [rt.get("disc"), rt.get("pos"), rt.get("rec_id")]
                matched += 1
                score_sum += pair[(i, j)]
            i, j = i - 1, j - 1
        elif move == "up":
            i -= 1
        else:
            j -= 1

    return Alignment(
        coverage=matched / n,
        score=(score_sum / matched) if matched else 0.0,
        track_map=track_map,
        matched=matched,
        total=n,
    )
