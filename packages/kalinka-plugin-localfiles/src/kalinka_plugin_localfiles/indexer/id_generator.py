import hashlib

from ..utils.name_utils import normalize_for_id


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

    key = normalize_for_id(artist_name)
    if not key:
        return "unknown_artist"
    hash_obj = hashlib.md5(key.encode("utf-8"))
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

    key = normalize_for_id(album_title)
    if not key:
        return "unknown_album"
    hash_obj = hashlib.md5(f"{key}{artist_id}".encode("utf-8"))
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
