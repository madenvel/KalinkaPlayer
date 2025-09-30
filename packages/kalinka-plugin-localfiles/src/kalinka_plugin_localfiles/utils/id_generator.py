import hashlib
import uuid


def generate_playlist_id(name: str, created_by: str) -> str:
    """
    Generate a stable ID for a playlist.

    Args:
        name: The name of the playlist
        created_by: Creator identifier (can be system name)

    Returns:
        A stable ID string for the playlist
    """
    # Create a hash from the name and creator for a stable ID
    hash_input = f"{name.lower()}{created_by}"
    hash_obj = hashlib.md5(hash_input.encode("utf-8"))
    return f"playlist_{hash_obj.hexdigest()[:16]}"


def generate_playlist_track_id() -> str:
    """
    Generate a unique ID for a playlist track entry.
    This allows the same track to appear multiple times in a playlist.

    Returns:
        A unique ID string for the playlist track entry
    """
    return f"ptrack_{uuid.uuid4().hex[:16]}"
