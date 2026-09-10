#!/usr/bin/env python3
"""Comparing a local name against an external catalogue's spelling of it.

Two things get in the way. A catalogue lists one record under edition-
qualified names ("Abbey Road (Remastered)") and spells sequels in roman
numerals where a tag used digits. And a legacy fixed-width tag field cuts a
name off mid-word — "Иванушки Int" — which a symmetric ratio scores at 0.71
because the missing tail counts against it, though a prefix is strong
evidence rather than weak.

This is comparison only; nothing here is ever stored or displayed.
"""

import pytest

from kalinka_plugin_localfiles.enricher.enricher_plugin import best_search_name
from kalinka_plugin_localfiles.utils.name_utils import (
    fold_for_provider_match,
    name_similarity,
    truncated_name_similarity,
)

THRESHOLD = 0.8


class TestTheFold:
    @pytest.mark.parametrize(
        "title",
        [
            "Abbey Road (Remastered)",
            "Abbey Road [2009 Remaster]",
            "Abbey Road (Super Deluxe Edition)",
            "Abbey Road (Mono Version)",
        ],
    )
    def test_edition_qualifiers_are_folded_away(self, title):
        assert fold_for_provider_match(title) == "abbey road"

    def test_a_descriptive_parenthetical_is_kept(self):
        """Only edition wording is noise; the rest can be the actual title."""
        assert fold_for_provider_match("Fairytale (From \"Shrek\")") == (
            'fairytale (from "shrek")'
        )

    def test_a_trailing_roman_numeral_becomes_a_digit(self):
        assert fold_for_provider_match("Fairytale II") == "fairytale 2"
        assert fold_for_provider_match("Chapter IX") == "chapter 9"

    @pytest.mark.parametrize("title", ["X", "V", "II"])
    def test_a_title_that_is_only_a_numeral_survives(self, title):
        """Ed Sheeran's "X" is an album, not a tenth volume of something."""
        assert fold_for_provider_match(title) == title.casefold()

    def test_nothing_folds_to_nothing(self):
        assert fold_for_provider_match("") == ""
        assert fold_for_provider_match(None) == ""


class TestSymmetricSimilarity:
    def test_an_edition_suffix_no_longer_costs_a_match(self):
        assert name_similarity("Abbey Road", "Abbey Road (Remastered)") == 1.0

    def test_a_sequel_matches_across_numeral_styles(self):
        assert name_similarity("Fairytale 2", "Fairytale II") == 1.0

    def test_a_sequel_is_not_its_predecessor(self):
        """The reason album titles are not scored truncation-tolerantly: a
        prefix here is as likely a different record in a series."""
        assert name_similarity("Fairytale", "Fairytale II") < 1.0

    def test_a_truncated_name_scores_badly(self):
        assert name_similarity("Иванушки Int", "Иванушки International") < THRESHOLD

    def test_unrelated_names_do_not_match(self):
        assert name_similarity("Abbey Road", "Let It Be") < THRESHOLD

    def test_an_empty_name_matches_nothing(self):
        assert name_similarity("", "Abbey Road") == 0.0
        assert name_similarity("Abbey Road", "") == 0.0


class TestTruncationTolerance:
    def test_a_name_cut_mid_word_matches_the_whole_one(self):
        assert truncated_name_similarity(
            "Иванушки Int", "Иванушки International"
        ) >= THRESHOLD

    def test_a_short_fragment_does_not_match_everything_it_begins(self):
        """"Int" is a prefix of a great many names; six characters is the
        floor at which a prefix stops being a coincidence."""
        assert truncated_name_similarity("Int", "Iron Maiden") < THRESHOLD
        assert truncated_name_similarity("The", "The Beatles") < THRESHOLD

    def test_too_little_of_the_name_surviving_is_not_evidence(self):
        assert truncated_name_similarity(
            "Tangerin", "Tangerine Dream Orchestral Collection Volume Two"
        ) < THRESHOLD

    def test_a_name_that_merely_contains_another_is_not_a_truncation(self):
        assert truncated_name_similarity("Maiden", "Iron Maiden") < THRESHOLD

    def test_it_still_agrees_with_the_symmetric_score_otherwise(self):
        assert truncated_name_similarity("Abbey Road", "Let It Be") < THRESHOLD
        assert truncated_name_similarity("The Beatles", "The Beatles") == 1.0


class _Claims:
    def __init__(self, claims):
        self._claims = claims

    async def get_claims(self, entity_type, entity_id, field):
        return self._claims


class TestTheNameToSearchWith:
    @pytest.mark.asyncio
    async def test_an_external_claim_supplies_the_whole_name(self):
        """Resolution keeps the observed tag for display; a provider should
        still be asked with the name a source that identified this artist
        recorded."""
        db = _Claims(
            [
                {
                    "value": "Иванушки International",
                    "source": "musicbrainz:cb65b11d",
                    "tier": "inferred",
                }
            ]
        )
        assert await best_search_name(
            db, "artist", "artist_1", "name", "Иванушки Int"
        ) == "Иванушки International"

    @pytest.mark.asyncio
    async def test_the_strongest_claim_wins(self):
        db = _Claims(
            [
                {"value": "Weak", "source": "deezer:1", "tier": "inferred"},
                {"value": "Strong", "source": "acoustid:2", "tier": "verified"},
            ]
        )
        assert await best_search_name(db, "artist", "a", "name", "Local") == "Strong"

    @pytest.mark.asyncio
    async def test_the_librarys_own_reading_is_not_an_improvement(self):
        """A tag or a folder name is where the truncated value came from, so
        it can never be the better name to search with."""
        db = _Claims(
            [
                {
                    "value": "Иванушки Int",
                    "source": "tag_consensus",
                    "tier": "observed",
                },
                {"value": "Ivanushki", "source": "folder_name", "tier": "guessed"},
            ]
        )
        assert await best_search_name(
            db, "artist", "a", "name", "Иванушки Int"
        ) == "Иванушки Int"

    @pytest.mark.asyncio
    async def test_no_claims_at_all_falls_back_to_the_row(self):
        db = _Claims([])
        assert await best_search_name(db, "artist", "a", "name", "Local") == "Local"

    @pytest.mark.asyncio
    async def test_a_claim_with_no_value_is_not_used(self):
        db = _Claims([{"value": "", "source": "musicbrainz:1", "tier": "inferred"}])
        assert await best_search_name(db, "artist", "a", "name", "Local") == "Local"
