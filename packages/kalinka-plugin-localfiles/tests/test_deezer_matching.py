#!/usr/bin/env python3
"""How an album is found on Deezer, and how a candidate is judged.

Three shipped defects met here. The structured query sent its values
unquoted, which Deezer answers with nothing at all for any multi-word name.
The fallback query then searched on the title alone, so the artist could not
be among the results it went on to demand an artist match from. And ranking
was on the title score alone, so a covers act with an identical title
outranked the real release and was then rejected by the artist test — losing
the correct match that was sitting in the same response.
"""

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.enricher.deezer_plugin import DeezerPlugin


class FakeResponse:
    def __init__(self, payload, status_code=200, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


class RecordingClient:
    """Answers each search from a queue and remembers the query strings.

    Anything that is not a search is the cover download, which succeeds.
    """

    def __init__(self, *payloads):
        self._payloads = list(payloads)
        self.queries = []

    async def get(self, url, params=None):
        if params and "q" in params:
            self.queries.append(params["q"])
            payload = self._payloads.pop(0) if self._payloads else {"data": []}
            return FakeResponse(payload)
        return FakeResponse({}, content=b"\xff\xd8jpeg-ish")


class _NoClaims:
    async def get_claims(self, entity_type, entity_id, field):
        return []

    async def get_artist_by_id(self, artist_id):
        return None


def _plugin(tmp_path) -> DeezerPlugin:
    config = LocalFilesConfig(
        db_path=str(tmp_path / "db.sqlite"),
        artwork_path=str(tmp_path / "artwork"),
    )
    return DeezerPlugin(config, db_manager=_NoClaims())


def _album(title, artist, cover="https://cdn/cover.jpg"):
    entry = {"title": title, "artist": {"name": artist}}
    if cover:
        entry["cover_xl"] = cover
    return entry


ABBEY_ROAD = {"id": "album_1", "title": "Abbey Road", "artist_name": "The Beatles"}


class TestTheQueriesSent:
    @pytest.mark.asyncio
    async def test_the_structured_query_quotes_its_values(self, tmp_path):
        """Deezer returns nothing at all for an unquoted multi-word value, so
        this shape has never matched a real artist."""
        p = _plugin(tmp_path)
        p.async_client = RecordingClient({"data": []}, {"data": []})

        await p.enrich_album(dict(ABBEY_ROAD))

        assert p.async_client.queries[0] == 'artist:"The Beatles" album:"Abbey Road"'

    @pytest.mark.asyncio
    async def test_the_fallback_query_names_the_artist(self, tmp_path):
        """Searching the title alone returns other people's records, and the
        artist test then rejects every one of them."""
        p = _plugin(tmp_path)
        p.async_client = RecordingClient({"data": []}, {"data": []})

        await p.enrich_album(dict(ABBEY_ROAD))

        assert p.async_client.queries[1] == "The Beatles Abbey Road"

    @pytest.mark.asyncio
    async def test_a_match_on_the_first_query_stops_there(self, tmp_path):
        p = _plugin(tmp_path)
        p.async_client = RecordingClient(
            {"data": [_album("Abbey Road", "The Beatles")]}
        )
        p._save_images = lambda *a: True

        assert await p.enrich_album(dict(ABBEY_ROAD)) is not None
        assert len(p.async_client.queries) == 1


class TestJudgingCandidates:
    def test_the_real_artist_beats_a_higher_scoring_cover_act(self, tmp_path):
        """The Abbey Road case: the tribute band's title is a perfect match
        and the Beatles' is not, because theirs says "(Remastered)"."""
        p = _plugin(tmp_path)
        candidates = [
            _album("Abbey Road (Remastered)", "The Beatles"),
            _album("Abbey Road", "The Beatles Complete On Ukulele"),
            _album("Abbey Road", "Rock4"),
        ]

        match = p._find_best_album_match(candidates, "Abbey Road", "The Beatles")

        assert match is not None
        assert match[0]["artist"]["name"] == "The Beatles"

    def test_a_sequel_in_roman_numerals_is_the_same_record(self, tmp_path):
        p = _plugin(tmp_path)
        candidates = [_album("Fairytale II", "Zero-Project")]

        match = p._find_best_album_match(candidates, "Fairytale 2", "zero-project")

        assert match is not None and match[0]["title"] == "Fairytale II"

    def test_a_truncated_artist_tag_still_matches(self, tmp_path):
        """Legacy fixed-width tag fields cut names off mid-word."""
        p = _plugin(tmp_path)
        candidates = [_album("Твои письма", "Иванушки International")]

        match = p._find_best_album_match(
            candidates, "Твои письма", "Иванушки Int"
        )

        assert match is not None

    def test_the_right_title_by_the_wrong_artist_is_refused(self, tmp_path):
        p = _plugin(tmp_path)
        candidates = [_album("Abbey Road", "Booker T. & The MG's")]

        assert p._find_best_album_match(candidates, "Abbey Road", "The Beatles") is None

    def test_the_right_artist_with_an_unrelated_title_is_refused(self, tmp_path):
        p = _plugin(tmp_path)
        candidates = [_album("Let It Be", "The Beatles")]

        assert p._find_best_album_match(candidates, "Abbey Road", "The Beatles") is None

    def test_a_candidate_without_a_title_is_ignored(self, tmp_path):
        p = _plugin(tmp_path)
        candidates = [
            {"artist": {"name": "The Beatles"}},
            _album("Abbey Road", "The Beatles"),
        ]

        match = p._find_best_album_match(candidates, "Abbey Road", "The Beatles")

        assert match is not None and match[0]["title"] == "Abbey Road"

    def test_nothing_at_all_is_no_match(self, tmp_path):
        p = _plugin(tmp_path)
        assert p._find_best_album_match([], "Abbey Road", "The Beatles") is None
