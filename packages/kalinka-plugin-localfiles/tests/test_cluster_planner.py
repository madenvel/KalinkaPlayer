#!/usr/bin/env python3
"""
Tests for Phase-1 1d planner: build_features (from track row + evidence) and
plan_folder (engine + V/A classification). Read-only — no DB writes.
"""

import json

from kalinka_plugin_localfiles.clustering.planner import (
    build_features,
    plan_folder,
)


def _ev(album=None, albumartist=None, compilation=None, art=None,
        codec="flac", sr=44100, bd=16, vorbis=True):
    raw = {}
    if vorbis:
        if album:
            raw["album"] = [album]
        if albumartist:
            raw["albumartist"] = [albumartist]
        if compilation:
            raw["compilation"] = [compilation]
    else:
        if album:
            raw["TALB"] = album
        if albumartist:
            raw["TPE2"] = albumartist
        if compilation:
            raw["TCMP"] = compilation
    return {
        "raw_tags": json.dumps(raw),
        "stream_info": json.dumps({"codec": codec, "sample_rate": sr,
                                   "bits_per_sample": bd}),
        "art_phash": art,
        "cue_sheet": None,
        "import_batch": None,
    }


def _track(tid, artist_id="artist_a", track_number=1, disc_number=None):
    return {"id": tid, "artist_id": artist_id, "track_number": track_number,
            "disc_number": disc_number}


def test_build_features_reads_albumartist_and_disc_suffix():
    f = build_features(
        _track("t1"),
        _ev(album="Some Album (Disc 2)", albumartist="The Band", compilation="1"),
    )
    assert f.album_key == "some album"           # disc suffix stripped
    assert f.albumartist_key == "the band"
    assert f.compilation is True
    assert f.stream_key == "flac|44100|16"


def test_build_features_id3_vocabulary():
    f = build_features(
        _track("t1"), _ev(album="X", albumartist="AA", vorbis=False)
    )
    assert f.album_key == "x"
    assert f.albumartist_key == "aa"


def test_plan_untagged_folder_gets_folder_name_title():
    rows = [(_track(f"t{i}", track_number=i), _ev()) for i in range(1, 6)]
    plan = plan_folder("/music/Some Album", rows)
    assert not plan.split
    assert len(plan.clusters) == 1
    c = plan.clusters[0]
    assert c.kind == "album"
    assert c.title == "Some Album"        # folder name, not unknown_album
    assert len(c.track_ids) == 5


def test_plan_coherent_album_uses_tag_title():
    rows = [
        (_track(f"t{i}", track_number=i), _ev(album="Real Title",
                                              albumartist="Band"))
        for i in range(1, 9)
    ]
    plan = plan_folder("/music/folder", rows)
    assert len(plan.clusters) == 1
    assert plan.clusters[0].title == "Real Title"
    assert plan.clusters[0].kind == "album"


def test_plan_va_folder_is_compilation():
    # 6 tracks, 6 distinct artists, clean album-named folder.
    rows = [
        (_track(f"t{i}", artist_id=f"artist_{i}", track_number=i),
         _ev(album=f"track by {i}"))
        for i in range(1, 7)
    ]
    plan = plan_folder("/music/VA - Summer Hits", rows)
    assert len(plan.clusters) == 1
    c = plan.clusters[0]
    assert c.kind == "compilation"
    assert c.title == "Summer Hits"
    assert c.anchor_artist_id == "various_artists"


def test_plan_generic_dump_is_singles_pool():
    rows = [
        (_track(f"t{i}", artist_id=f"artist_{i}", track_number=i), _ev())
        for i in range(1, 7)
    ]
    plan = plan_folder("/music/90s Mixes", rows)
    assert len(plan.clusters) == 1
    c = plan.clusters[0]
    assert c.kind == "singles_pool"
    assert c.title == ""
    assert c.anchor_artist_id == "unknown_artist"


def test_plan_two_albums_split():
    a = [(_track(f"a{i}", artist_id="one", track_number=i),
          _ev(album="Alpha", albumartist="one", art="0000000000000000"))
         for i in range(1, 7)]
    b = [(_track(f"b{i}", artist_id="two", track_number=i),
          _ev(album="Beta", albumartist="two", art="ffffffffffffffff"))
         for i in range(1, 7)]
    plan = plan_folder("/music/mixed", a + b)
    assert plan.split
    assert len(plan.clusters) == 2
    titles = {c.title for c in plan.clusters}
    assert titles == {"Alpha", "Beta"}
