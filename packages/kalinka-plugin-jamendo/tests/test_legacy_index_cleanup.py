"""_remove_legacy_indexes drops superseded index assets, but never the live one."""

import os

from kalinka_plugin_jamendo.mood_search import JamendoMoodIndex


def _make(index_path: str) -> JamendoMoodIndex:
    d = os.path.dirname(index_path)
    return JamendoMoodIndex(index_path, None, os.path.join(d, "minilm"), None)


def test_removes_stale_legacy_index(tmp_path):
    current = tmp_path / "jamendo_index_v2.sqlite"
    legacy = tmp_path / "jamendo_index.sqlite"
    current.write_text("current")
    legacy.write_text("stale")

    _make(str(current))._remove_legacy_indexes()

    assert current.exists()        # live index untouched
    assert not legacy.exists()     # superseded sibling removed


def test_does_not_delete_current_even_if_legacy_named(tmp_path):
    # A user pinned to the old filename must not have it deleted out from under.
    current = tmp_path / "jamendo_index.sqlite"
    current.write_text("current")

    _make(str(current))._remove_legacy_indexes()

    assert current.exists()


def test_noop_when_no_legacy_present(tmp_path):
    current = tmp_path / "jamendo_index_v2.sqlite"
    current.write_text("current")

    _make(str(current))._remove_legacy_indexes()  # must not raise

    assert current.exists()
