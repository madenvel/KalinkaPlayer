import hashlib


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
