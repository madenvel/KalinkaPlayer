from typing import Optional
import pytest

from unittest.mock import Mock

from data_model.datamodel import BrowseItem, BrowseItemList, EntityId, EntityType
from src.inputmodule import InputModule, SearchType
from src.multisearch import multisearch


def create_browse_item(
    name: str, timestamp: Optional[int] = None, source: str = "test"
) -> BrowseItem:
    """Helper to create a BrowseItem for testing"""
    entity_id = EntityId(
        id=name.lower().replace(" ", "_"), type=EntityType.TRACK, source=source
    )

    return BrowseItem(
        id=entity_id,
        name=name,
        url="",
        can_browse=True,
        can_add=True,
        timestamp=timestamp,
    )


def mock_module(name, ts: int, items: list[BrowseItem]):
    module = Mock(spec=InputModule)
    module.module_name.return_value = name
    module.search.return_value = BrowseItemList(
        offset=0, limit=10, total=2, items=items
    )
    return module


@pytest.mark.asyncio
async def test_search_merge():
    """Test merging search results from multiple modules."""

    items1 = [
        create_browse_item(name="blablabla", timestamp=101, source="Module1"),
        create_browse_item(name="babla", timestamp=100, source="Module1"),
    ]
    items2 = [
        create_browse_item(name="blabl", timestamp=102, source="Module2"),
        create_browse_item(name="bl", timestamp=99, source="Module2"),
    ]

    module1 = mock_module("Module1", 100, items1)
    module2 = mock_module("Module2", 101, items2)

    merged_items = await multisearch([module1, module2], SearchType.track, "bla", 0, 10)

    assert len(merged_items.items) == 4
    assert merged_items.items[0].name == "blabl"  # Highest timestamp first
    assert merged_items.items[1].name == "bl"
    assert merged_items.items[2].name == "blablabla"
    assert merged_items.items[3].name == "babla"


@pytest.mark.asyncio
async def test_search_empty_modules():
    """Test search with no modules."""
    result = await multisearch([], SearchType.track, "query", 0, 10)
    assert isinstance(result, BrowseItemList)
    assert result.items == []
    assert result.total == 0
    assert result.offset == 0
    assert result.limit == 10


@pytest.mark.asyncio
async def test_search_with_no_results():
    """Test search with modules that return no results."""
    module1 = mock_module("Module1", 100, [])
    module2 = mock_module("Module2", 101, [])

    result = await multisearch([module1, module2], SearchType.track, "query", 0, 10)
    assert isinstance(result, BrowseItemList)
    assert result.items == []
    assert result.total == 0
    assert result.offset == 0
    assert result.limit == 10
