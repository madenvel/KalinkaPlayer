import asyncio
import logging
import heapq
from typing import List
from rapidfuzz import fuzz

from src.inputmodule import InputModule, SearchType
from data_model.datamodel import BrowseItemList, EmptyList, BrowseItem

logger = logging.getLogger(__name__)


async def multisearch(
    modules: List[InputModule],
    search_type: SearchType,
    query: str,
    offset: int = 0,
    limit: int = 50,
) -> BrowseItemList:
    """
    Search across multiple input modules and return interleaved results.

    Args:
        modules: List of input modules to search
        search_type: Type of search (album, track, playlist, artist)
        query: Search query string
        offset: Offset for pagination (applied to final combined results)
        limit: Limit for pagination (applied to final combined results)

    Returns:
        BrowseItemList with interleaved results from all successful modules
    """
    if not modules:
        return EmptyList(offset, limit)

    # Calculate how many results we need to fetch from each module
    # We need more than the final limit to handle interleaving and offset
    # Fetch enough to cover offset + limit, distributed across modules
    per_module_limit = max(50, (offset + limit) * 2 // len(modules))

    successful_results = []
    total_count = 0

    # Search all modules concurrently using asyncio
    tasks = [
        _safe_search_module_async(module, search_type, query, 0, per_module_limit)
        for module in modules
    ]

    # Wait for all tasks to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    for i, result in enumerate(results):
        module = modules[i]
        if isinstance(result, Exception):
            logger.warning(
                f"Search failed for module {module.module_name()}: {str(result)}"
            )
            continue

        # Type guard to ensure result is BrowseItemList
        if isinstance(result, BrowseItemList) and result.items:
            successful_results.append(result)
            total_count += result.total
            logger.debug(
                f"Module {module.module_name()} returned {len(result.items)} items"
            )

    if not successful_results:
        logger.info("All modules failed or returned no results")
        return EmptyList(offset, limit)

    # Merge results using fuzzy score-based selection
    merged_items = _merge_results_by_score(successful_results, query)

    # Apply offset and limit to the final combined results
    start_idx = min(offset, len(merged_items))
    end_idx = min(offset + limit, len(merged_items))
    final_items = merged_items[start_idx:end_idx]

    return BrowseItemList(
        offset=offset,
        limit=limit,
        total=total_count,  # Sum of all module totals
        items=final_items,
    )


async def _safe_search_module_async(
    module: InputModule, search_type: SearchType, query: str, offset: int, limit: int
) -> BrowseItemList | None:
    """
    Safely search a single module in an async context, handling any exceptions.

    Since InputModule.search is synchronous, we run it in a thread pool.

    Returns:
        BrowseItemList from the module, or None if search failed
    """
    try:
        loop = asyncio.get_event_loop()
        # Run the blocking search operation in a thread pool
        result = await loop.run_in_executor(
            None, module.search, search_type, query, offset, limit
        )
        return result
    except Exception as e:
        logger.warning(f"Search failed for module {module.module_name()}: {str(e)}")
        return None


def _merge_results_by_score(
    results: List[BrowseItemList], query: str
) -> List[BrowseItem]:
    """
    Merge items from multiple BrowseItemList results using fuzzy score-based selection.

    Uses an optimal k-way merge algorithm with a max-heap (priority queue) for O(log k)
    complexity per item selection, where k is the number of sources.

    Args:
        results: List of BrowseItemList objects to merge
        query: The search query to compare against for scoring

    Returns:
        List of BrowseItem objects sorted by fuzzy match score (best first)
    """
    if not results:
        return []

    # Create iterators for each result list
    iterators = [iter(result.items) for result in results]

    # Initialize heap with first item from each non-empty iterator
    # Note: Python's heapq is a min-heap, so we negate scores to simulate max-heap
    heap = []

    for source_idx, iterator in enumerate(iterators):
        try:
            item = next(iterator)
            score = _calculate_fuzzy_score(item.name, query)
            # Use negative score for max-heap behavior, and source_idx for tie-breaking
            heapq.heappush(heap, (-score, source_idx, item, iterator))
        except StopIteration:
            # Iterator is empty, skip it
            continue

    merged = []

    # K-way merge using heap
    while heap:
        # Pop the item with the highest score (most negative value)
        neg_score, source_idx, best_item, iterator = heapq.heappop(heap)

        # Add the best item to merged results
        merged.append(best_item)

        # Try to get the next item from the same source
        try:
            next_item = next(iterator)
            score = _calculate_fuzzy_score(next_item.name, query)
            # Push the next item back to the heap
            heapq.heappush(heap, (-score, source_idx, next_item, iterator))
        except StopIteration:
            # This source is exhausted, don't add anything back to heap
            pass

    return merged


def _calculate_fuzzy_score(item_name: str, query: str) -> float:
    """
    Calculate fuzzy similarity score between item name and search query.

    Uses RapidFuzz for fast fuzzy string comparison.

    Args:
        item_name: Name of the item to score
        query: Search query to compare against

    Returns:
        Float score between 0.0 and 1.0 (1.0 = perfect match)
    """
    if not item_name or not query:
        return 0.0

    # Normalize strings for comparison (lowercase, strip whitespace)
    normalized_name = item_name.lower().strip()
    normalized_query = query.lower().strip()

    # Use RapidFuzz ratio for fuzzy comparison
    # ratio() is equivalent to difflib.SequenceMatcher.ratio() but much faster
    return fuzz.ratio(normalized_name, normalized_query) / 100.0
