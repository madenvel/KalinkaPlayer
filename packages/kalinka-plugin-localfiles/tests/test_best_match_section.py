"""Tests for the BEST MATCH section rendering in LocalFilesInputModule.

The searcher returns ``best_match`` as an ordered list of ``{"id", "type"}``
dicts (rapidfuzz score, highest first). ``_build_best_match_section`` must
render them as a single flat list in that exact order — no grouping or
sub-sorting by entity type.
"""

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.localfiles import LocalFilesInputModule


class _FakeDb:
    """Returns rows by id, preserving the requested order (like the real db)."""

    def __init__(self):
        self._tracks = {
            "t1": {
                "id": "t1", "album_id": "al1", "artist_id": "ar1",
                "album_title": "Album One", "artist_name": "The Piano Guys",
                "title": "A Thousand Years", "duration": 100,
            },
        }
        self._albums = {
            "al1": {
                "id": "al1", "title": "Album One", "artist_id": "ar1",
                "artist_name": "The Piano Guys", "duration": 100, "track_count": 10,
            },
        }
        self._artists = {"ar1": {"id": "ar1", "name": "The Piano Guys"}}

    def get_tracks_by_ids(self, ids):
        return [self._tracks[i] for i in ids if i in self._tracks]

    def get_albums_by_ids(self, ids):
        return [self._albums[i] for i in ids if i in self._albums]

    def get_artists_by_ids(self, ids):
        return [self._artists[i] for i in ids if i in self._artists]

    # Cover-art lookups: no artwork on disk in these tests.
    def get_album_by_id(self, album_id):
        return None

    def get_artist_by_id(self, artist_id):
        return None


def _module(tmp_path):
    config = LocalFilesConfig(
        music_folders=[str(tmp_path)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    return LocalFilesInputModule(config, _FakeDb())


def test_flat_list_preserves_score_order_across_types(tmp_path):
    module = _module(tmp_path)

    # Deliberately interleaved types — artist, then track, then album.
    section = module._build_best_match_section([
        {"id": "ar1", "type": "artist"},
        {"id": "t1", "type": "track"},
        {"id": "al1", "type": "album"},
    ])

    assert section is not None
    assert section.name == "BEST MATCH"
    assert section.catalog.title == "BEST MATCH"

    rows = section.sections
    assert len(rows) == 3
    # Order matches the input exactly — no regrouping by type.
    assert rows[0].artist is not None and rows[0].artist.name == "The Piano Guys"
    assert rows[1].track is not None and rows[1].name == "A Thousand Years"
    assert rows[2].album is not None and rows[2].album.title == "Album One"


def test_empty_returns_none(tmp_path):
    module = _module(tmp_path)
    assert module._build_best_match_section([]) is None


def test_missing_rows_drop_out_and_collapse_to_none(tmp_path):
    module = _module(tmp_path)
    # An id the db can't resolve is skipped; if nothing resolves -> None.
    assert module._build_best_match_section(
        [{"id": "ghost", "type": "track"}]
    ) is None
