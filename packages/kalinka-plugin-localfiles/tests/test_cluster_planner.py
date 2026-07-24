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


def _track(tid, artist_id="artist_a", track_number=1, disc_number=None,
           artist_name=None):
    return {"id": tid, "artist_id": artist_id, "track_number": track_number,
            "disc_number": disc_number, "artist_name": artist_name}


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


def test_plan_strips_artist_prefix_from_folder_title():
    # Untagged rip in "The Beatles - Abbey Road": the folder-derived album
    # title keeps only "Abbey Road" once the artist prefix is stripped.
    rows = [
        (_track(f"t{i}", artist_id="beatles", track_number=i,
                artist_name="The Beatles"), _ev())
        for i in range(1, 6)
    ]
    plan = plan_folder("/music/The Beatles - Abbey Road", rows)
    assert plan.clusters[0].title == "Abbey Road"


def test_plan_keeps_eponymous_album_title():
    # An album actually named after the artist isn't stripped to empty.
    rows = [
        (_track(f"t{i}", artist_id="metallica", track_number=i,
                artist_name="Metallica"), _ev())
        for i in range(1, 6)
    ]
    plan = plan_folder("/music/Metallica", rows)
    assert plan.clusters[0].title == "Metallica"


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


def test_plan_flat_va_dump_detaches_to_singles():
    # A flat playlist folder (Jamendo-style): many artists, no shared album
    # tag, each track its own cover art. Must NOT fragment into per-art albums
    # + V/A umbrellas — the whole folder detaches to unknown_album so tracks
    # surface as singles under their real artist.
    rows = [
        (_track(f"t{i}", artist_id=f"artist_{i}", track_number=i),
         _ev(art=f"phash_{i}"))               # distinct per-track art
        for i in range(1, 13)                 # 12 tracks, 12 artists, no album
    ]
    plan = plan_folder(
        "/music/Playlist - Urban - 500604904 --- Jamendo - MP3", rows
    )
    assert len(plan.clusters) == 1
    c = plan.clusters[0]
    assert c.kind == "singles_pool"
    assert c.anchor_artist_id == "unknown_artist"
    assert c.grouping_basis["reason"] == "va_dump_folder"
    assert len(c.track_ids) == 12             # every track detached, none lost


def test_plan_shared_album_tag_survives_many_artists():
    # A real V/A compilation with a shared album tag is NOT a dump even though
    # every track is a different artist — the shared tag protects it.
    rows = [
        (_track(f"t{i}", artist_id=f"artist_{i}", track_number=i),
         _ev(album="Now That's What I Call Music 50"))
        for i in range(1, 13)
    ]
    plan = plan_folder("/music/Now 50", rows)
    assert len(plan.clusters) == 1
    assert plan.clusters[0].kind == "compilation"


def test_multidisc_in_one_folder_strips_disc_suffix_from_title():
    # Both discs tagged "… (Disc N)" in one folder collapse to one album whose
    # title drops the disc marker.
    rows = []
    for i in range(1, 6):
        rows.append((_track(f"a{i}", track_number=i, disc_number=1),
                     _ev(album="Best Of MJ (Disk 1)", albumartist="MJ")))
    for i in range(1, 6):
        rows.append((_track(f"b{i}", track_number=i, disc_number=2),
                     _ev(album="Best Of MJ (Disk 2)", albumartist="MJ")))
    plan = plan_folder("/music/Best Of MJ (2CD)", rows)
    assert len(plan.clusters) == 1
    c = plan.clusters[0]
    assert c.title == "Best Of MJ"      # (Disk N) stripped
    assert c.kind == "multi_disc"
    assert len(c.track_ids) == 10


def test_standalone_volume_keeps_its_name():
    # A single "Vol. 2" release (one title variant) must NOT be stripped.
    rows = [
        (_track(f"t{i}", track_number=i), _ev(album="Greatest Hits Vol. 2",
                                              albumartist="Band"))
        for i in range(1, 7)
    ]
    plan = plan_folder("/music/Greatest Hits Vol 2", rows)
    assert len(plan.clusters) == 1
    assert plan.clusters[0].title == "Greatest Hits Vol. 2"
    assert plan.clusters[0].kind == "album"


def test_plan_empty_folder():
    plan = plan_folder("/music/empty", [])
    assert plan.clusters == [] and not plan.split


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
