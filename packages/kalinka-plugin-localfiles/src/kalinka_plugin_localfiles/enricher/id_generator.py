"""Compatibility shim. The canonical implementations live in
``kalinka_plugin_localfiles.utils.id_generator`` — this module is
preserved as an import path so existing callers (and tests asserting
indexer/enricher symmetry) keep working without modification.
"""

from ..utils.id_generator import (  # noqa: F401
    generate_album_id,
    generate_artist_id,
)
