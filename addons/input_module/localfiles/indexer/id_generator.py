import hashlib
import uuid


def generate_artist_id(artist_name: str) -> str:
    """
    Generate a stable ID for an artist.

    Args:
        artist_name: The name of the artist

    Returns:
        A stable ID string for the artist
    """
    if not artist_name or artist_name == "Unknown Artist":
        return "unknown_artist"

    # Create a hash from the artist name for a stable ID
    hash_obj = hashlib.md5(artist_name.lower().encode("utf-8"))
    return f"artist_{hash_obj.hexdigest()[:16]}"


def generate_album_id(album_title: str, artist_id: str) -> str:
    """
    Generate a stable ID for an album.

    Args:
        album_title: The title of the album
        artist_id: The ID of the artist

    Returns:
        A stable ID string for the album
    """
    if not album_title or album_title == "Unknown Album":
        return "unknown_album"

    # Create a hash from the album title and artist ID for a stable ID
    hash_input = f"{album_title.lower()}{artist_id}"
    hash_obj = hashlib.md5(hash_input.encode("utf-8"))
    return f"album_{hash_obj.hexdigest()[:16]}"


def generate_track_id(file_path: str) -> str:
    """
    Generate a stable ID for a track.

    Args:
        file_path: The file path of the track

    Returns:
        A stable ID string for the track
    """
    # Create a hash from the file path for a stable ID
    hash_obj = hashlib.md5(file_path.encode("utf-8"))
    return f"track_{hash_obj.hexdigest()[:16]}"
