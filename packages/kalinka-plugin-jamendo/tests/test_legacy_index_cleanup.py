"""_remove_legacy_indexes drops superseded index assets, but never the live one."""

from kalinka_plugin_jamendo.mood_search import JamendoMoodIndex


def _make(index_path: str) -> JamendoMoodIndex:
    return JamendoMoodIndex(index_path, None, embedder=None)


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


def test_removes_all_older_versions_generically(tmp_path):
    # A future v3 must clean BOTH v1 and v2 without a hardcoded list, plus any
    # leftover .part temp — but keep the live index and unrelated files.
    current = tmp_path / "jamendo_index_v3.sqlite"
    v1 = tmp_path / "jamendo_index.sqlite"
    v2 = tmp_path / "jamendo_index_v2.sqlite"
    part = tmp_path / "jamendo_index_v2.sqlite.part"
    minilm = tmp_path / "minilm"           # sibling dir, must survive
    custom = tmp_path / "my_custom_index.sqlite"  # user file, must survive
    for p in (current, v1, v2, part, custom):
        p.write_text("x")
    minilm.mkdir()

    _make(str(current))._remove_legacy_indexes()

    assert current.exists()
    assert custom.exists()
    assert minilm.is_dir()
    assert not v1.exists()
    assert not v2.exists()
    assert not part.exists()
