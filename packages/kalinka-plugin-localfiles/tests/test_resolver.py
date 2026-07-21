#!/usr/bin/env python3
"""Claims resolution engine (Phase 2c, §7): higher tier wins; within a tier a
per-field source precedence decides; ties favour the incumbent value.
"""

from kalinka_plugin_localfiles.resolution.resolver import (
    Claim,
    resolve_entity,
    resolve_field,
)


def _c(value, source, tier, field="album_title"):
    return Claim(field=field, value=value, source=source, tier=tier)


def test_higher_tier_wins_regardless_of_source():
    # Observed tag consensus beats an inferred external match — the canonical
    # "fuzzy MusicBrainz can't outrank a unanimous embedded title" case.
    claims = [
        _c("Everybody Hertz", "tag_consensus", "observed"),
        _c("Air", "folder_name", "inferred"),
        _c("Everybody Hertz", "musicbrainz:0d7f", "inferred"),
    ]
    winner = resolve_field(claims)
    assert winner.value == "Everybody Hertz"
    assert winner.source == "tag_consensus"


def test_verified_identifier_corrects_unanimous_tags():
    # A direct-identifier (verified) match may correct even unanimous tags.
    claims = [
        _c("Wrong Title", "tag_consensus", "observed"),
        _c("Right Title", "musicbrainz:abcd", "verified"),
    ]
    assert resolve_field(claims).value == "Right Title"


def test_within_tier_field_precedence_local_first_for_title():
    # Both inferred; for a title, folder_name outranks a fuzzy external match.
    claims = [
        _c("Folder Title", "folder_name", "inferred"),
        _c("MB Title", "musicbrainz:xyz", "inferred"),
    ]
    assert resolve_field(claims).source == "folder_name"


def test_within_tier_field_precedence_external_first_for_country():
    # For origin/era fields the profile flips: external beats library inference.
    claims = [
        _c("IT", "library_inference", "inferred", field="country"),
        _c("FR", "musicbrainz:xyz", "inferred", field="country"),
    ]
    assert resolve_field(claims).value == "FR"


def test_incumbency_breaks_ties():
    # Two equal-tier, equal-precedence rivals (same source base): the value
    # already shown keeps winning so display doesn't churn.
    claims = [
        _c("A", "musicbrainz:1", "inferred", field="genre"),
        _c("B", "musicbrainz:2", "inferred", field="genre"),
    ]
    assert resolve_field(claims, current_value="B").value == "B"
    assert resolve_field(claims, current_value="A").value == "A"


def test_pinned_beats_everything():
    claims = [
        _c("User Choice", "user", "pinned"),
        _c("MB", "musicbrainz:abcd", "verified"),
    ]
    assert resolve_field(claims).source == "user"


def test_unknown_source_does_not_outrank_known():
    claims = [
        _c("Known", "tag_consensus", "inferred"),
        _c("Mystery", "some_new_plugin", "inferred"),
    ]
    assert resolve_field(claims).value == "Known"


def test_empty_claims_resolve_to_none():
    assert resolve_field([]) is None


def test_resolve_entity_returns_one_winner_per_field():
    claims = [
        _c("Title A", "tag_consensus", "observed", field="album_title"),
        _c("Title B", "folder_name", "inferred", field="album_title"),
        _c("2001", "musicbrainz:z", "inferred", field="year"),
    ]
    winners = {w.field: w for w in resolve_entity(claims)}
    assert winners["album_title"].value == "Title A"
    assert winners["year"].value == "2001"
