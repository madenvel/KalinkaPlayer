"""Artwork files on disk, in the three sizes every consumer expects.

One writer for the downloaded-cover layout (``<artwork>/<type>/<id>_<size>.jpg``)
shared by the indexer's embedded-art extraction and every enrichment source
that fetches covers, so the sizes and naming cannot drift apart.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Union

from PIL import Image

logger = logging.getLogger(__name__.split(".")[-1])

_SIZES = (("thumbnail", 50), ("small", 230), ("large", 600))


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
        img = Image.open(io.BytesIO(image_data))
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
    except Exception as e:  # noqa: BLE001 - callers treat art as best-effort
        logger.error(f"Error saving artwork for {entity_type} {entity_id}: {e}")
        return False
