#!/usr/bin/env python3
"""The plugin->enricher claim contract is built in one place (inferred_claims),
not hand-assembled per plugin."""

from kalinka_plugin_localfiles.enricher.enricher_plugin import inferred_claims


def test_builds_inferred_claims_for_nonempty_fields():
    claims = inferred_claims(
        "musicbrainz:abc", {"name": "The Beatles", "country": "GB", "area": None}
    )
    assert claims == [
        {"field": "name", "value": "The Beatles", "source": "musicbrainz:abc",
         "tier": "inferred"},
        {"field": "country", "value": "GB", "source": "musicbrainz:abc",
         "tier": "inferred"},
    ]


def test_drops_empty_values():
    # None and "" are skipped; 0 / falsy-but-meaningful ints aren't expected here
    # (years arrive as truthy ints), so plain falsiness is the right filter.
    assert inferred_claims("deezer:1", {"genre": None}) == []
    assert inferred_claims("deezer:1", {"genre": ""}) == []
    assert inferred_claims("mb:1", {"year": 1969}) == [
        {"field": "year", "value": 1969, "source": "mb:1", "tier": "inferred"}
    ]
