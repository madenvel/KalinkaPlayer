#!/usr/bin/env python3
"""Tests for Phase-1 1g stable-id assignment (overlap reattach + aliasing)."""

from kalinka_plugin_localfiles.clustering.identity import assign_stable_ids
from kalinka_plugin_localfiles.clustering.planner import ClusterPlan


def _album(ids, folder="/m/A", kind="album"):
    return ClusterPlan(track_ids=list(ids), kind=kind, title="A",
                       anchor_artist_id="artist_a", folder=folder)


def test_single_cluster_keeps_plurality_id():
    # 3 tracks in old album X, 1 in Y -> cluster keeps X, aliases Y->X.
    clusters = [_album(["t1", "t2", "t3", "t4"])]
    current = {"t1": "X", "t2": "X", "t3": "X", "t4": "Y"}
    ids, aliases = assign_stable_ids(clusters, current)
    assert ids == ["X"]
    assert ("Y", "X") in aliases


def test_untagged_cluster_gets_minted_id():
    clusters = [_album(["t1", "t2"])]
    current = {"t1": "unknown_album", "t2": "unknown_album"}
    ids, aliases = assign_stable_ids(clusters, current)
    assert ids[0].startswith("album_")
    assert aliases == []              # unknown_album is never aliased


def test_singles_pool_maps_to_unknown_album():
    clusters = [_album(["t1", "t2"], kind="singles_pool")]
    ids, _ = assign_stable_ids(clusters, {"t1": "X", "t2": "X"})
    assert ids == ["unknown_album"]


def test_two_clusters_do_not_both_claim_same_old_id():
    # Old album Z split into two new clusters; only the larger keeps Z.
    a = _album(["a1", "a2", "a3"], folder="/m/A")
    b = _album(["b1", "b2"], folder="/m/B")
    current = {t: "Z" for t in ["a1", "a2", "a3", "b1", "b2"]}
    ids, aliases = assign_stable_ids([a, b], current)
    assert ids.count("Z") == 1        # Z used at most once
    assert ids[0] == "Z"              # larger overlap keeps it
    assert ids[1].startswith("album_")


def test_idempotent_on_second_run():
    # Run 1 over old ids, then run 2 with tracks pointing at the run-1 ids must
    # reproduce the same assignment (semantic no-op).
    clusters = [_album(["t1", "t2", "t3", "t4"])]
    current = {"t1": "X", "t2": "X", "t3": "X", "t4": "Y"}
    ids1, _ = assign_stable_ids(clusters, current)
    current2 = {t: ids1[0] for t in ["t1", "t2", "t3", "t4"]}
    ids2, aliases2 = assign_stable_ids(clusters, current2)
    assert ids2 == ids1
    assert aliases2 == []


def test_minted_id_stable_for_same_membership():
    c1 = _album(["t2", "t1"])       # order-independent
    c2 = _album(["t1", "t2"])
    id1, _ = assign_stable_ids([c1], {"t1": "unknown_album", "t2": "unknown_album"})
    id2, _ = assign_stable_ids([c2], {"t1": "unknown_album", "t2": "unknown_album"})
    assert id1 == id2
