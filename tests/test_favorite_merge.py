"""
Test module for k-way merge favorite lists functionality.
"""

import pytest
import asyncio
from typing import Optional
from unittest.mock import Mock

from data_model.datamodel import BrowseItem, BrowseItemList, EntityId, EntityType
from src.inputmodule import InputModule, SearchType
from src.favorite import combined_favorite_list


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


def mock_module(name: str, ts: int):
    """Create a mock InputModule for testing."""
    module = Mock(spec=InputModule)
    module.module_name.return_value = name
    module.list_favorite.return_value = BrowseItemList(
        offset=0,
        limit=10,
        total=2,
        items=[
            create_browse_item(
                name="Track " + str(ts + 1), timestamp=ts + 1, source=name
            ),
            create_browse_item(name="Track " + str(ts), timestamp=ts, source=name),
        ],
    )
    return module


@pytest.fixture
def mock_modules():
    """Fixture to create a list of mock InputModules."""
    return [
        mock_module("Module1", 100),
        mock_module("Module2", 101),
    ]


@pytest.mark.asyncio
async def test_merge_favorite_lists(mock_modules):
    """Test merging favorite lists from multiple modules."""

    result = await combined_favorite_list(
        modules=mock_modules,
        type=SearchType.track,
        filter="",
        offset=0,
        limit=10,
    )

    assert isinstance(result, BrowseItemList)
    assert len(result.items) == 4  # 2 from each module

    assert result.items[0].name == "Track 102"
    assert result.items[0].timestamp == 102  # Latest timestamp from Module2
    assert result.items[0].id.source == "Module2"
    assert result.items[1].name == "Track 101"
    assert result.items[1].timestamp == 101  # Latest timestamp from Module1
    assert result.items[1].id.source == "Module1"
    assert result.items[2].name == "Track 101"
    assert result.items[2].timestamp == 101  # Latest timestamp from Module2
    assert result.items[2].id.source == "Module2"
    assert result.items[3].name == "Track 100"
    assert result.items[3].timestamp == 100  # Latest timestamp from Module1
    assert result.items[3].id.source == "Module1"


@pytest.mark.asyncio
async def test_merge_favorite_lists_one_empty(mock_modules):
    """Test merging favorite lists with one empty module."""

    # Create an empty module
    empty_module = Mock(spec=InputModule)
    empty_module.module_name.return_value = "EmptyModule"
    empty_module.list_favorite.return_value = BrowseItemList(
        offset=0, limit=10, total=0, items=[]
    )

    # Add the empty module to the list
    modules_with_empty = mock_modules + [empty_module]

    result = await combined_favorite_list(
        modules=modules_with_empty,
        type=SearchType.track,
        filter="",
        offset=0,
        limit=10,
    )

    assert isinstance(result, BrowseItemList)
    assert len(result.items) == 4  # Should still return items from the other modules


@pytest.mark.asyncio
async def test_merge_favorite_lists_all_empty(mock_modules):
    """Test merging favorite lists with all modules empty."""

    # Create a list of empty modules
    empty_modules = [Mock(spec=InputModule) for _ in range(3)]
    for module in empty_modules:
        module.module_name.return_value = "EmptyModule"
        module.list_favorite.return_value = BrowseItemList(
            offset=0, limit=10, total=0, items=[]
        )

    result = await combined_favorite_list(
        modules=empty_modules,
        type=SearchType.track,
        filter="",
        offset=0,
        limit=10,
    )

    assert isinstance(result, BrowseItemList)
    assert len(result.items) == 0  # Should return an empty list
