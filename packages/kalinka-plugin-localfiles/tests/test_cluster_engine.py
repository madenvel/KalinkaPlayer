#!/usr/bin/env python3
"""
Tests for Phase-1 1c: the folder-partition engine. One folder is one album by
default; a split needs compelling evidence; minority outliers are absorbed.
Covers the proposal's worked examples.
"""

from kalinka_plugin_localfiles.clustering.engine import partition_folder
from kalinka_plugin_localfiles.clustering.signals import TrackFeatures


def _album(prefix, n, start=1, **common):
    return [
        TrackFeatures(track_id=f"{prefix}{i}", track_number=i, **common)
        for i in range(start, start + n)
    ]


def _ids(groups):
    return [sorted(t.track_id for t in g) for g in groups]


def test_coherent_folder_is_one_album():
    tracks = _album("t", 10, album_key="x", albumartist_key="aa",
                    art_phash="0000000000000000")
    res = partition_folder(tracks)
    assert not res.split
    assert len(res.groups) == 1
    assert len(res.groups[0]) == 10


def test_untagged_folder_is_one_album():
    # No album tags at all (a bare rip) -> one cluster, not fragmented.
    tracks = _album("t", 6, album_key=None, art_phash="0000000000000000")
    res = partition_folder(tracks)
    assert not res.split and len(res.groups) == 1


def test_two_complete_albums_in_one_folder_split():
    # 1..12 twice, distinct tags + distinct art -> two clusters.
    a = _album("a", 12, album_key="alpha", albumartist_key="one",
               art_phash="0000000000000000")
    b = _album("b", 12, album_key="beta", albumartist_key="two",
               art_phash="ffffffffffffffff")
    res = partition_folder(a + b)
    assert res.split
    assert len(res.groups) == 2
    groups = _ids(res.groups)
    assert sorted([f"a{i}" for i in range(1, 13)]) in groups
    assert sorted([f"b{i}" for i in range(1, 13)]) in groups


def test_one_mistagged_track_stays_one_album():
    # 11 tracks "Album X" + 1 stray tagged "Album Y" (no own sequence).
    tracks = _album("t", 11, album_key="x", albumartist_key="aa")
    tracks.append(TrackFeatures(track_id="t12", track_number=12,
                                album_key="y", albumartist_key="aa"))
    res = partition_folder(tracks)
    assert not res.split
    assert len(res.groups) == 1
    assert len(res.groups[0]) == 12  # the stray absorbed


def test_tag_variants_same_album_merge():
    # "Everybody Hertz" vs "Air - Everybody Hertz": different keys, same art +
    # albumartist, one joint run. Must NOT split (the fragmentation bug).
    a = _album("a", 6, album_key="everybody hertz", albumartist_key="air",
               art_phash="0000000000000000")
    b = _album("b", 5, start=7, album_key="air everybody hertz",
               albumartist_key="air", art_phash="0000000000000000")
    res = partition_folder(a + b)
    assert not res.split
    assert len(res.groups) == 1


def test_single_artist_many_singles_not_over_merged():
    # The Netsky over-merge risk: one folder, one artist, many tiny distinct
    # releases each with its own cover. Different art -> they stay separate.
    # Covers chosen with pairwise Hamming >= 32 (clearly different).
    covers = [
        "0000000000000000",
        "ffffffff00000000",
        "0000ffffffff0000",
        "00000000ffffffff",
    ]
    groups_in = []
    for k in range(4):
        groups_in += [
            TrackFeatures(track_id=f"r{k}_{i}", track_number=i,
                          album_key=f"single{k}", albumartist_key="netsky",
                          art_phash=covers[k])
            for i in (1, 2)
        ]
    res = partition_folder(groups_in)
    # Distinct covers are strong split evidence even for 2-track groups.
    assert res.split
    assert len(res.groups) == 4


def test_singleton_folder():
    res = partition_folder([TrackFeatures(track_id="only", track_number=1)])
    assert not res.split and len(res.groups) == 1
