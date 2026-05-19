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


def generate_album_id(album_title: str, album_folder: str) -> str:
    """
    Generate a stable ID for an album.

    The folder is part of the key so two physical copies of the same album
    (e.g. 16/44 and 24/96 rips in sibling directories) get distinct IDs,
    while a track mistagged with a different artist inside the album folder
    still collapses into the same album. Disc subdirs (``CD1`` / ``Disc 2``)
    are stripped from the folder before hashing — see
    ``album_folder_for_path``.

    Args:
        album_title: The title of the album
        album_folder: The album's folder on disk (already disc-stripped).
            Tracks belonging to the same album must produce the same folder.

    Returns:
        A stable ID string for the album
    """
    if not album_title or album_title == "Unknown Album":
        return "unknown_album"

    title_key = normalize_for_id(album_title)
    if not title_key:
        return "unknown_album"
    folder_key = album_folder or ""
    hash_obj = hashlib.md5(f"{folder_key}\0{title_key}".encode("utf-8"))
    return f"album_{hash_obj.hexdigest()[:16]}"
