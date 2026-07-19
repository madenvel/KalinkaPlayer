#!/usr/bin/env python3
"""
Tests for Phase-1 1b: the membership signal primitives and scorer.

These are pure functions — no DB. They encode the §5.2 evidence table: a
positive score favours one album, negative favours a split.
"""

from kalinka_plugin_localfiles.clustering.signals import (
    TrackFeatures,
    hamming_hex,
    pair_signal_score,
    sequence_analysis,
)


def _tracks(n, **common):
    """n tracks numbered 1..n sharing the given feature values."""
    out = []
    for i in range(1, n + 1):
        out.append(TrackFeatures(track_id=f"t{i}", track_number=i, **common))
    return out


def test_hamming_hex():
    assert hamming_hex("0000000000000000", "0000000000000000") == 0
    assert hamming_hex("0000000000000000", "0000000000000001") == 1
    assert hamming_hex("00000000000000ff", "0000000000000000") == 8


def test_sequence_analysis_plausible_run():
    s = sequence_analysis([1, 2, 3, 4, 5, 6])
    assert s.is_plausible and not s.has_collision


def test_sequence_analysis_collision():
    s = sequence_analysis([1, 2, 3, 4, 4])
    assert s.has_collision and not s.is_plausible


def test_sequence_analysis_too_short_or_sparse():
    assert not sequence_analysis([1, 2]).is_plausible          # too few
    assert not sequence_analysis([1, 2, 3, 30]).is_plausible   # sparse coverage
    assert not sequence_analysis([]).is_plausible


def test_coherent_folder_scores_positive():
    # One album: same tag, same albumartist, one art group, clean 1..6 run.
    a = _tracks(3, album_key="x", albumartist_key="aa",
                art_phash="0000000000000000", stream_key="flac|44100|16")
    b = [TrackFeatures(track_id=f"t{i}", track_number=i, album_key="x",
                       albumartist_key="aa", art_phash="0000000000000000",
                       stream_key="flac|44100|16") for i in range(4, 7)]
    score, basis = pair_signal_score(a, b)
    assert score > 0
    assert "same_album_tag" in basis and "same_art" in basis
    assert "same_albumartist" in basis and "compatible_track_numbers" in basis


def test_two_albums_same_folder_scores_negative():
    # Both subgroups are complete 1..5 runs, different tags, different art.
    a = [TrackFeatures(track_id=f"a{i}", track_number=i, album_key="alpha",
                       albumartist_key="one", art_phash="0000000000000000")
         for i in range(1, 6)]
    b = [TrackFeatures(track_id=f"b{i}", track_number=i, album_key="beta",
                       albumartist_key="two", art_phash="ffffffffffffffff")
         for i in range(1, 6)]
    score, basis = pair_signal_score(a, b)
    assert score < 0
    assert "different_album_tags_both_sequences" in basis
    assert "different_art" in basis
    assert "different_albumartist" in basis
    assert "track_number_collision" in basis  # two "track 1..5" runs collide


def test_mistagged_outlier_does_not_force_split():
    # 5 tracks album X + a single stray tagged Y at position 6 (no own run).
    a = _tracks(5, album_key="x", albumartist_key="aa")
    stray = [TrackFeatures(track_id="t6", track_number=6, album_key="y",
                           albumartist_key="aa")]
    score, basis = pair_signal_score(a, stray)
    # Different tags but the stray is not its own sequence -> no strong split
    # signal; albumartist agrees and the joint run is clean.
    assert "different_album_tags_both_sequences" not in basis
    assert score >= 0


def test_art_relation_thresholds():
    same = pair_signal_score(
        [TrackFeatures("a", art_phash="0000000000000000")],
        [TrackFeatures("b", art_phash="0000000000000003")],  # dist 2
    )[1]
    assert "same_art" in same
    diff = pair_signal_score(
        [TrackFeatures("a", art_phash="0000000000000000")],
        [TrackFeatures("b", art_phash="ffffffffff000000")],  # far
    )[1]
    assert "different_art" in diff


def test_cue_sheet_is_strong_cohesion():
    a = [TrackFeatures("a1", track_number=1, cue_sheet="/m/x.cue",
                       album_key="x")]
    b = [TrackFeatures("b1", track_number=2, cue_sheet="/m/x.cue",
                       album_key="y")]  # differing tags, but one cue
    score, basis = pair_signal_score(a, b)
    assert "same_cue_sheet" in basis
    assert score > 0
