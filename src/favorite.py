"""
K-way merge functionality for combining favorite lists from multiple input modules.

This module provides efficient merging of sorted favorite lists using a min-heap
based k-way merge algorithm. The merge maintains timestamp ordering while supporting
on-demand data fetching and pagination.

Example usage:
    ```python
    from src.favorite import combined_favorite_list
    from src.inputmodule import SearchType

    # Assuming you have a list of input modules
    modules = [spotify_module, qobuz_module, local_module]

    # Merge their track favorites (now async)
    result = await combined_favorite_list(
        modules=modules,
        type=SearchType.track,
        filter="",
        offset=0,
        limit=50
    )

    # The result contains merged favorites sorted by timestamp
    for item in result.items:
        print(f"{item.name} from {item.id.source} at {item.timestamp}")
    ```
"""

import heapq
import asyncio
from typing import List, Iterator, Optional, Sequence, Awaitable
from dataclasses import dataclass

from data_model.datamodel import BrowseItem, BrowseItemList
from src.inputmodule import InputModule, SearchType


@dataclass
class ModuleIterator:
    """Wrapper for input module with pagination state"""

    module: InputModule
    type: SearchType
    filter: str
    current_offset: int = 0
    limit: int = 50
    exhausted: bool = False
    current_items: Optional[List[BrowseItem]] = None
    current_index: int = 0

    def __post_init__(self):
        self.current_items = []

    def has_next(self) -> bool:
        """Check if there are more items available"""
        if self.current_items and self.current_index < len(self.current_items):
            return True
        return not self.exhausted

    async def get_next(self) -> Optional[BrowseItem]:
        """Get the next item, fetching more data if needed"""
        # If we have items in current batch and haven't reached the end
        if self.current_items and self.current_index < len(self.current_items):
            item = self.current_items[self.current_index]
            self.current_index += 1
            return item

        # If we're exhausted, no more items
        if self.exhausted:
            return None

        # Fetch next batch
        try:
            # Run the synchronous list_favorite call in a thread pool
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.module.list_favorite(
                    type=self.type,
                    filter=self.filter,
                    offset=self.current_offset,
                    limit=self.limit,
                ),
            )

            if not result.items or len(result.items) == 0:
                self.exhausted = True
                return None

            self.current_items = result.items
            self.current_index = 0
            self.current_offset += len(result.items)

            # If we got fewer items than requested, we've reached the end
            if len(result.items) < self.limit:
                self.exhausted = True

            # Return the first item from the new batch
            if self.current_items:
                item = self.current_items[self.current_index]
                self.current_index += 1
                return item

        except Exception:
            self.exhausted = True

        return None

    async def peek(self) -> Optional[BrowseItem]:
        """Peek at the next item without consuming it"""
        if self.current_items and self.current_index < len(self.current_items):
            return self.current_items[self.current_index]

        if self.exhausted:
            return None

        # Need to fetch more data to peek
        next_item = await self.get_next()
        if next_item:
            # Put it back by decreasing the index
            self.current_index -= 1
            return next_item

        return None


@dataclass
class HeapItem:
    """Wrapper for heap items with timestamp comparison"""

    timestamp: int
    item: BrowseItem
    iterator_id: int

    def __lt__(self, other):
        # For min-heap: smaller timestamps come first
        return self.timestamp > other.timestamp


async def combined_favorite_list(
    modules: Sequence[InputModule],
    type: SearchType,
    filter: str = "",
    offset: int = 0,
    limit: int = 50,
    batch_size: int = 50,
) -> BrowseItemList:
    """
    Merge favorite lists from multiple input modules using k-way merge algorithm.

    This function efficiently combines sorted favorite lists from multiple sources
    while maintaining timestamp ordering. It fetches data on-demand to satisfy the
    requested limit, making it efficient for large datasets.

    Args:
        modules: Sequence of InputModule instances to merge favorites from
        type: Type of favorites to list (album, track, playlist, artist)
        filter: Filter string for favorite items
        offset: Starting offset for the merged result (0-based)
        limit: Maximum number of items to return
        batch_size: Batch size for fetching data from each module (affects memory usage)

    Returns:
        BrowseItemList containing merged and sorted favorites

    Raises:
        ValueError: If limit or offset are negative

    Note:
        - Items without timestamps are treated as having timestamp=0
        - The total count in the result is an estimate to avoid exhausting all data
        - For exact total count, use merge_favorite_lists_exact_total()
    """
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if offset < 0:
        raise ValueError("offset must be non-negative")

    if not modules or limit == 0:
        return BrowseItemList(offset=offset, limit=limit, total=0, items=[])

    # Create iterators for each module
    iterators = []
    for i, module in enumerate(modules):
        try:
            iterator = ModuleIterator(
                module=module,
                type=type,
                filter=filter,
                limit=min(
                    batch_size, 100
                ),  # Cap batch size to prevent excessive memory usage
            )
            iterators.append(iterator)
        except Exception:
            # Skip modules that fail to initialize
            continue

    if not iterators:
        return BrowseItemList(offset=offset, limit=limit, total=0, items=[])

    # Initialize min-heap with first item from each iterator
    heap = []
    for i, iterator in enumerate(iterators):
        try:
            item = await iterator.get_next()
            if item:
                # Use timestamp if available, otherwise use 0
                timestamp = item.timestamp if item.timestamp is not None else 0
                heapq.heappush(heap, HeapItem(timestamp, item, i))
        except Exception:
            # Skip iterators that fail to produce items
            continue

    # Collect merged items
    merged_items = []
    items_processed = 0

    while heap and len(merged_items) < limit:
        # Get the item with smallest timestamp
        heap_item = heapq.heappop(heap)

        # If we haven't reached the desired offset, skip this item
        if items_processed < offset:
            items_processed += 1
        else:
            merged_items.append(heap_item.item)

        # Get next item from the same iterator
        iterator = iterators[heap_item.iterator_id]
        try:
            next_item = await iterator.get_next()

            if next_item:
                timestamp = (
                    next_item.timestamp if next_item.timestamp is not None else 0
                )
                heapq.heappush(
                    heap, HeapItem(timestamp, next_item, heap_item.iterator_id)
                )
        except Exception:
            # Skip if iterator fails
            continue

        # If we haven't reached offset yet, continue
        if items_processed < offset:
            continue
        else:
            items_processed += 1

    # Calculate total count (approximation based on what we've seen)
    # Note: Getting exact total would require exhausting all iterators
    total_estimate = offset + len(merged_items)

    # If any iterator still has items, we know there are more
    try:
        if any(iterator.has_next() for iterator in iterators) or heap:
            total_estimate = max(total_estimate, offset + limit + 1)
    except Exception:
        # If we can't check, assume there might be more
        total_estimate = max(total_estimate, offset + limit + 1)

    return BrowseItemList(
        offset=offset, limit=limit, total=total_estimate, items=merged_items
    )


def merge_favorite_lists_exact_total(
    modules: Sequence[InputModule],
    type: SearchType,
    filter: str = "",
    offset: int = 0,
    limit: int = 50,
    batch_size: int = 50,
) -> BrowseItemList:
    """
    Merge favorite lists with exact total count calculation.

    This version will exhaust all iterators to provide an exact total count,
    but is less efficient for large datasets.

    Args:
        modules: List of InputModule instances to merge favorites from
        type: Type of favorites to list (album, track, playlist, artist)
        filter: Filter string for favorite items
        offset: Starting offset for the merged result
        limit: Maximum number of items to return
        batch_size: Batch size for fetching data from each module

    Returns:
        BrowseItemList containing merged and sorted favorites with exact total
    """
    if not modules:
        return BrowseItemList(offset=offset, limit=limit, total=0, items=[])

    # Create iterators for each module
    iterators = []
    for i, module in enumerate(modules):
        iterator = ModuleIterator(
            module=module, type=type, filter=filter, limit=batch_size
        )
        iterators.append(iterator)

    # Initialize min-heap with first item from each iterator
    heap = []
    for i, iterator in enumerate(iterators):
        item = iterator.get_next()
        if item:
            timestamp = item.timestamp if item.timestamp is not None else 0
            heapq.heappush(heap, HeapItem(timestamp, item, i))

    # Collect all items in sorted order
    all_items = []

    while heap:
        # Get the item with smallest timestamp
        heap_item = heapq.heappop(heap)
        all_items.append(heap_item.item)

        # Get next item from the same iterator
        iterator = iterators[heap_item.iterator_id]
        next_item = iterator.get_next()

        if next_item:
            timestamp = next_item.timestamp if next_item.timestamp is not None else 0
            heapq.heappush(heap, HeapItem(timestamp, next_item, heap_item.iterator_id))

    # Apply offset and limit to get the requested slice
    total_count = len(all_items)
    start_idx = min(offset, total_count)
    end_idx = min(offset + limit, total_count)
    result_items = all_items[start_idx:end_idx]

    return BrowseItemList(
        offset=offset, limit=limit, total=total_count, items=result_items
    )


def merge_all_favorites(
    modules: Sequence[InputModule],
    filter: str = "",
    offset: int = 0,
    limit: int = 50,
    batch_size: int = 50,
    include_types: Optional[Sequence[SearchType]] = None,
) -> BrowseItemList:
    """
    Merge all types of favorites from multiple input modules.

    This is a convenience function that merges tracks, albums, playlists, and artists
    from all modules into a single sorted list.

    Args:
        modules: Sequence of InputModule instances to merge favorites from
        filter: Filter string for favorite items
        offset: Starting offset for the merged result
        limit: Maximum number of items to return
        batch_size: Batch size for fetching data from each module
        include_types: Types to include, defaults to all types if None

    Returns:
        BrowseItemList containing merged and sorted favorites of all types
    """
    if include_types is None:
        include_types = [
            SearchType.track,
            SearchType.album,
            SearchType.playlist,
            SearchType.artist,
        ]

    if limit < 0:
        raise ValueError("limit must be non-negative")
    if offset < 0:
        raise ValueError("offset must be non-negative")

    if not modules or not include_types or limit == 0:
        return BrowseItemList(offset=offset, limit=limit, total=0, items=[])

    # Create iterators for each module-type combination
    iterators = []
    iterator_id = 0

    for module in modules:
        for fav_type in include_types:
            try:
                iterator = ModuleIterator(
                    module=module,
                    type=fav_type,
                    filter=filter,
                    limit=min(batch_size, 100),
                )
                iterators.append(iterator)
                iterator_id += 1
            except Exception:
                # Skip modules/types that fail to initialize
                continue

    if not iterators:
        return BrowseItemList(offset=offset, limit=limit, total=0, items=[])

    # Initialize min-heap with first item from each iterator
    heap = []
    for i, iterator in enumerate(iterators):
        try:
            item = iterator.get_next()
            if item:
                timestamp = item.timestamp if item.timestamp is not None else 0
                heapq.heappush(heap, HeapItem(timestamp, item, i))
        except Exception:
            continue

    # Collect merged items
    merged_items = []
    items_processed = 0

    while heap and len(merged_items) < limit:
        # Get the item with smallest timestamp
        heap_item = heapq.heappop(heap)

        # If we haven't reached the desired offset, skip this item
        if items_processed < offset:
            items_processed += 1
        else:
            merged_items.append(heap_item.item)

        # Get next item from the same iterator
        iterator = iterators[heap_item.iterator_id]
        try:
            next_item = iterator.get_next()

            if next_item:
                timestamp = (
                    next_item.timestamp if next_item.timestamp is not None else 0
                )
                heapq.heappush(
                    heap, HeapItem(timestamp, next_item, heap_item.iterator_id)
                )
        except Exception:
            continue

        if items_processed < offset:
            continue
        else:
            items_processed += 1

    # Calculate total estimate
    total_estimate = offset + len(merged_items)

    try:
        if any(iterator.has_next() for iterator in iterators) or heap:
            total_estimate = max(total_estimate, offset + limit + 1)
    except Exception:
        total_estimate = max(total_estimate, offset + limit + 1)

    return BrowseItemList(
        offset=offset, limit=limit, total=total_estimate, items=merged_items
    )
