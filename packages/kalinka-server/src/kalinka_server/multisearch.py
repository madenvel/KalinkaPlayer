from rapidfuzz import fuzz
import logging

logger = logging.getLogger(__name__.split(".")[-1])


def calculate_fuzzy_score(item_name: str, query: str) -> int:
    """
    Calculate fuzzy similarity score between item name and search query.

    Uses RapidFuzz for fast fuzzy string comparison.

    Args:
        item_name: Name of the item to score
        query: Search query to compare against

    Returns:
        Float score between 0 and 1000 (1000 = perfect match)
    """
    if not item_name or not query:
        return 0

    # Normalize strings for comparison (lowercase, strip whitespace)
    normalized_name = item_name.lower().strip()
    normalized_query = query.lower().strip()

    # Use RapidFuzz ratio for fuzzy comparison
    score = int(fuzz.ratio(normalized_name, normalized_query) * 10)  # Scale to 0-1000

    logger.debug(
        "Calculating fuzzy score for '%s' against query '%s', score: %d",
        normalized_name,
        normalized_query,
        score,
    )

    return score
