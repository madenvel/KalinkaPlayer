#!/usr/bin/env python3
"""Phase 3, slice 3b: monotonic tracklist alignment.

Order-preserving alignment of a local album's tracks against a candidate
release's tracklist, yielding coverage + a track_map. Pure, no DB.
"""

from kalinka_plugin_localfiles.enricher.tracklist_align import (
    Alignment,
    align_tracklist,
)


def _local(*titles, durations=None):
    durations = durations or [None] * len(titles)
    return [
        {"id": f"t{i}", "title": t, "duration": d}
        for i, (t, d) in enumerate(zip(titles, durations))
    ]


def _release(*titles, lengths=None, disc=1):
    lengths = lengths or [None] * len(titles)
    return [
        {"title": t, "length_s": s, "disc": disc, "pos": i + 1, "rec_id": f"r{i}"}
        for i, (t, s) in enumerate(zip(titles, lengths))
    ]


def test_perfect_alignment():
    local = _local("Come Together", "Something", "Maxwell's Hammer")
    rel = _release("Come Together", "Something", "Maxwell's Hammer")
    a = align_tracklist(local, rel)
    assert a.coverage == 1.0
    assert a.matched == 3
    assert set(a.track_map) == {"t0", "t1", "t2"}
    assert a.track_map["t0"] == [1, 1, "r0"]


def test_recases_and_minor_diffs_still_match():
    local = _local("come together", "SOMETHING")
    rel = _release("Come Together", "Something")
    assert align_tracklist(local, rel).coverage == 1.0


def test_missing_track_in_local_keeps_full_coverage():
    # Release has an extra track the local album doesn't; every local track
    # still maps (coverage is over LOCAL tracks).
    local = _local("A", "C")
    rel = _release("A", "B", "C")
    a = align_tracklist(local, rel)
    assert a.coverage == 1.0
    assert a.matched == 2


def test_bonus_local_track_lowers_coverage():
    # Local has a bonus track absent from the release -> unmatched -> coverage<1.
    local = _local("A", "B", "Hidden Bonus Track XYZ")
    rel = _release("A", "B")
    a = align_tracklist(local, rel)
    assert a.matched == 2
    assert a.coverage == 2 / 3
    assert "t2" not in a.track_map


def test_monotonic_rejects_reordered_coincidence():
    # A swapped pair cannot both match under order preservation.
    local = _local("Alpha", "Beta")
    rel = _release("Beta", "Alpha")
    a = align_tracklist(local, rel)
    assert a.matched < 2


def test_duration_disambiguates_same_title():
    # Two same-titled release tracks; duration picks the right one and keeps
    # monotonic order.
    local = _local("Intro", "Song", durations=[10, 200])
    rel = _release("Song", "Song", lengths=[12, 201])
    a = align_tracklist(local, rel)
    # local "Song" (200s) should map to the 201s release track (pos 2).
    assert a.track_map["t1"][1] == 2


def test_empty_inputs():
    assert align_tracklist([], _release("A")) == Alignment(0.0, 0.0, {}, 0, 0)
    assert align_tracklist(_local("A"), []).coverage == 0.0


def test_dissimilar_pairs_do_not_match():
    local = _local("Completely Different One", "Another Unrelated")
    rel = _release("Zzz Qqq", "Www Vvv")
    assert align_tracklist(local, rel).matched == 0


def test_trailing_parenthetical_does_not_sink_a_true_match():
    """Local rips carry suffixes the provider lacks — "(Lennon-McCartney)",
    "(Remastered 2009)". Measured on real MB data: 4 Abbey Road tracks scored
    0.42-0.46 (under the floor) before suffix-tolerant comparison."""
    local = _local("Because (Lennon-McCartney)", "Sun King (Lennon-McCartney)")
    rel = _release("Because", "Sun King")
    a = align_tracklist(local, rel)
    assert a.coverage == 1.0


def test_exact_match_still_outranks_stripped():
    # "Song" pairs with the exact "Song", not the suffixed one, under monotonicity.
    local = _local("Song")
    rel = _release("Song (Live)", "Song")
    assert align_tracklist(local, rel).track_map["t0"][1] == 2


def test_stripping_does_not_create_false_matches_across_albums():
    local = _local("Alpha (Remastered)", "Beta (Remastered)")
    rel = _release("Completely Other", "Nothing Alike")
    assert align_tracklist(local, rel).matched == 0
