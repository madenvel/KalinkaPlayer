"""Artwork files on disk, in the three sizes every consumer expects.

One writer for the downloaded-cover layout (``<artwork>/<type>/<id>_<size>.jpg``)
shared by the indexer's embedded-art extraction and every enrichment source
that fetches covers, so the sizes and naming cannot drift apart.

Two ways in, because callers hold covers in two forms: a source that fetched
one over HTTP has bytes, while a sleeve scan found beside the audio has a
path — and opening that by path lets a large one be decoded without first
being held in memory whole.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Union

from PIL import Image

logger = logging.getLogger(__name__.split(".")[-1])

_SIZES = (("thumbnail", 50), ("small", 230), ("large", 600))
_LARGEST = max(size for _, size in _SIZES)


def save_artwork_images(
    artwork_path: Union[str, os.PathLike],
    image_data: bytes,
    entity_id: str,
    entity_type: str,
) -> bool:
    """Decode ``image_data`` and save it as thumbnail/small/large JPEGs.

    Returns False (and logs) on any failure — a broken image must not fail
    the enrichment or indexing pass that found it.
    """
    try:
        with Image.open(io.BytesIO(image_data)) as img:
            return _save_resized(img, artwork_path, entity_id, entity_type)
    except Exception as e:  # noqa: BLE001 - callers treat art as best-effort
        logger.error(f"Error saving artwork for {entity_type} {entity_id}: {e}")
        return False


def save_artwork_from_path(
    artwork_path: Union[str, os.PathLike],
    source_path: Union[str, os.PathLike],
    entity_id: str,
    entity_type: str,
) -> bool:
    """As :func:`save_artwork_images`, for a cover that is already a file.

    A sleeve scan can be far larger than anything downloaded, so the JPEG
    decoder is asked for a reduced scale up front: nothing here needs more
    than the largest stored size, and a 3000px scan then costs a sixteenth
    of the memory. ``draft`` is a no-op for formats that cannot do it.
    """
    try:
        with Image.open(source_path) as img:
            img.draft("RGB", (_LARGEST, _LARGEST))
            return _save_resized(img, artwork_path, entity_id, entity_type)
    except Exception as e:  # noqa: BLE001 - callers treat art as best-effort
        logger.error(f"Error saving artwork for {entity_type} {entity_id}: {e}")
        return False


def _save_resized(
    img: Image.Image,
    artwork_path: Union[str, os.PathLike],
    entity_id: str,
    entity_type: str,
) -> bool:
    """Write one decoded image out in every size. Raises; callers report."""
    dir_path = os.path.join(artwork_path, entity_type)
    os.makedirs(dir_path, exist_ok=True)

    if img.mode != "RGB":
        img = img.convert("RGB")

    for suffix, size in _SIZES:
        copy = img.copy()
        copy.thumbnail((size, size), Image.Resampling.LANCZOS)
        copy.save(
            os.path.join(dir_path, f"{entity_id}_{suffix}.jpg"),
            "JPEG",
            quality=90,
        )
    return True
