import os
import logging
from pathlib import Path
from typing import List, Optional
from PIL import Image

logger = logging.getLogger(__name__.split(".")[-1])


def create_playlist_cover_collage(
    album_ids: List[str], artwork_path: Path, playlist_id: str
) -> Optional[str]:
    """
    Create a playlist cover image by making a collage of album images.

    Args:
        album_ids: List of album IDs to use for the collage (up to 4)
        artwork_path: Base path for artwork
        playlist_id: ID of the playlist

    Returns:
        True if the collage was created successfully, False otherwise
    """
    # If there are no album IDs, return False
    if not album_ids:
        logger.warning("No album IDs provided for playlist cover collage")
        return None

    # Create the playlist directory if it doesn't exist
    playlist_dir = os.path.join(artwork_path, "playlist")
    os.makedirs(playlist_dir, exist_ok=True)

    # If there's only one album ID, just copy the image
    if len(album_ids) == 1:
        return _copy_single_album_image(album_ids[0], artwork_path, playlist_id)

    # Get the image paths for each album
    image_paths = []
    for album_id in album_ids[:4]:  # Limit to 4 images
        album_image_path = os.path.join(artwork_path, f"album/{album_id}_large.jpg")
        if os.path.exists(album_image_path):
            image_paths.append(album_image_path)
        else:
            logger.warning(f"Album image not found: {album_image_path}")

    # If we don't have any valid images, return False
    if not image_paths:
        logger.warning("No valid album images found for playlist cover collage")
        return None

    # If we only have one image after all, just copy it
    if len(image_paths) == 1:
        return _copy_single_album_image(album_ids[0], artwork_path, playlist_id)

    try:
        # Open all images and make sure they're the same size
        images = []
        for path in image_paths:
            img = Image.open(path)
            images.append(img)

        # Get the size of the first image
        img_size = images[0].width

        # Create a new image with 2x2 grid
        grid_size = 2
        collage = Image.new("RGB", (img_size, img_size))

        # Place images in the grid
        positions = [
            (0, 0),  # Top left
            (img_size // 2, 0),  # Top right
            (0, img_size // 2),  # Bottom left
            (img_size // 2, img_size // 2),  # Bottom right
        ]

        # Resize images to fit in the grid
        cell_size = img_size // 2
        for i, img in enumerate(images[:4]):
            img = img.resize((cell_size, cell_size))
            collage.paste(img, positions[i])

        # Save the collage in 3 sizes
        large_path = os.path.join(playlist_dir, f"{playlist_id}_large.jpg")
        small_path = os.path.join(playlist_dir, f"{playlist_id}_small.jpg")
        thumbnail_path = os.path.join(playlist_dir, f"{playlist_id}_thumbnail.jpg")

        # Save large image (original size)
        collage.save(large_path, "JPEG", quality=90)

        # Save small image (300x300)
        small_img = collage.resize((300, 300), Image.Resampling.LANCZOS)
        small_img.save(small_path, "JPEG", quality=85)

        # Save thumbnail (150x150)
        thumb_img = collage.resize((150, 150), Image.Resampling.LANCZOS)
        thumb_img.save(thumbnail_path, "JPEG", quality=85)

        return playlist_id
    except Exception as e:
        logger.error(f"Error creating playlist cover collage: {str(e)}")
        return None


def _copy_single_album_image(
    album_id: str, artwork_path: Path, playlist_id: str
) -> Optional[str]:
    """
    Copy a single album image to use as playlist cover.

    Args:
        album_id: ID of the album to copy the image from
        artwork_path: Base path for artwork
        playlist_id: ID of the playlist

    Returns:
        True if the image was copied successfully, False otherwise
    """
    try:
        # Create the playlist directory if it doesn't exist
        playlist_dir = artwork_path / "playlist"
        playlist_dir.mkdir(parents=True, exist_ok=True)

        # Define paths
        source_large = artwork_path / f"album/{album_id}_large.jpg"
        source_small = artwork_path / f"album/{album_id}_small.jpg"
        source_thumbnail = artwork_path / f"album/{album_id}_thumbnail.jpg"

        dest_large = playlist_dir / f"{playlist_id}_large.jpg"
        dest_small = playlist_dir / f"{playlist_id}_small.jpg"
        dest_thumbnail = playlist_dir / f"{playlist_id}_thumbnail.jpg"

        # Check if source images exist
        if not source_large.exists():
            logger.warning(f"Source album image not found: {source_large}")
            return None

        # Copy images
        large_img = Image.open(source_large)
        large_img.save(dest_large, "JPEG", quality=90)

        if os.path.exists(source_small):
            small_img = Image.open(source_small)
            small_img.save(dest_small, "JPEG", quality=85)
        else:
            # Create small image from large
            small_img = large_img.resize((300, 300), Image.Resampling.LANCZOS)
            small_img.save(dest_small, "JPEG", quality=85)

        if os.path.exists(source_thumbnail):
            thumb_img = Image.open(source_thumbnail)
            thumb_img.save(dest_thumbnail, "JPEG", quality=85)
        else:
            # Create thumbnail from large
            thumb_img = large_img.resize((150, 150), Image.Resampling.LANCZOS)
            thumb_img.save(dest_thumbnail, "JPEG", quality=85)

        return playlist_id
    except Exception as e:
        logger.error(f"Error copying album image for playlist: {str(e)}")
        return None
