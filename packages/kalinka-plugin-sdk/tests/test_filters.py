"""The filter document: shapes are fixed, field ids are not."""

import pytest
from pydantic import ValidationError

from kalinka_plugin_sdk.filters import (
    FilterQuery,
    RangeSelector,
    TextSelector,
    UnsupportedFilter,
    ValuesSelector,
)


def test_each_selector_kind_parses_to_its_own_shape():
    query = FilterQuery.model_validate(
        {
            "q": {"contains": "miles"},
            "genre": {"any": ["jazz", "funk"]},
            "year": {"gte": 1990, "lte": 1999},
        }
    )

    assert isinstance(query.root["q"], TextSelector)
    assert isinstance(query.root["genre"], ValuesSelector)
    assert isinstance(query.root["year"], RangeSelector)


def test_field_ids_are_open():
    # Nothing in the SDK knows what "origin" is; a module declaring it is enough.
    query = FilterQuery.model_validate({"origin": {"any": ["US"]}})
    assert query.values("origin").any == ["US"]


def test_document_round_trips():
    document = {"genre": {"all": ["jazz"]}, "year": {"lte": 1979}}
    assert FilterQuery.model_validate(document).model_dump(
        exclude_defaults=True
    ) == document


@pytest.mark.parametrize(
    "document",
    [
        {"genre": {}},  # empty values selector
        {"year": {}},  # empty range selector
        {"genre": {"anny": ["jazz"]}},  # misspelled operation
        {"q": {"contains": "x", "any": ["y"]}},  # two kinds at once
        {"year": {"gte": 2000, "lte": 1990}},  # inverted bounds
    ],
)
def test_malformed_selectors_are_rejected(document):
    with pytest.raises(ValidationError):
        FilterQuery.model_validate(document)


def test_empty_document_is_the_unfiltered_listing():
    query = FilterQuery({})
    assert query.fields() == set()
    assert query.text() == ""
    assert query.values("genre") is None
    assert query.range("year") is None


def test_a_field_that_was_not_offered_is_refused():
    query = FilterQuery.model_validate(
        {"q": {"contains": "x"}, "origin": {"any": ["US"]}}
    )

    with pytest.raises(UnsupportedFilter) as excinfo:
        query.reject_undeclared({"q", "genre"})
    assert excinfo.value.field == "origin"

    query.reject_undeclared({"q", "origin"})


def test_a_listing_that_declares_nothing_refuses_everything():
    query = FilterQuery.model_validate({"q": {"contains": "x"}})

    with pytest.raises(UnsupportedFilter):
        query.reject_undeclared(())
    FilterQuery({}).reject_undeclared(())


def test_accessor_on_the_wrong_kind_is_a_declaration_error():
    query = FilterQuery.model_validate({"genre": {"any": ["jazz"]}})

    with pytest.raises(UnsupportedFilter) as excinfo:
        query.text("genre")
    assert excinfo.value.field == "genre"

    with pytest.raises(UnsupportedFilter):
        query.range("genre")


def test_operations_default_to_absent_not_empty_matches():
    selector = FilterQuery.model_validate({"genre": {"none": ["metal"]}}).values(
        "genre"
    )
    assert selector.none == ["metal"]
    assert selector.any == [] and selector.all == []
