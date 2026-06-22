"""Shared vector utilities for embedder and searcher.

CLAP embeddings are unit L2-normalised vectors stored as symmetric int8
(4x smaller than float32, ~no recall loss): q = round(clip(v, -CAP, CAP) * 127/CAP).
The scale is fixed, not calibrated per corpus: a query is quantized on its own
and must land on the same integer grid as the stored vectors. sqlite-vec runs
KNN on int8 directly, so vectors are never dequantized for search.
"""

from __future__ import annotations

# Per-component clip bound, with headroom over the observed absmax (~0.223);
# components beyond CAP saturate at ±127. Changing CAP/dtype/dim changes the
# stored byte format — bump CLAP_EMBED_FORMAT_VERSION when it does.
CLAP_INT8_CAP = 0.25
CLAP_INT8_SCALE = 127.0 / CLAP_INT8_CAP

# Stored CLAP vector format, recorded in PRAGMA user_version. On a mismatch the
# schema layer rebuilds the typed vec0 tables and clears embedding blobs so the
# embedder recomputes them. 0/unset = legacy float32; 2 = int8 symmetric.
CLAP_EMBED_FORMAT_VERSION = 2


def encode_embedding(vector) -> bytes:
    """Quantize a (normalised) float embedding to int8 bytes for storage."""
    import numpy as np

    v = np.asarray(vector, dtype=np.float32)
    q = np.clip(np.round(v * CLAP_INT8_SCALE), -127.0, 127.0).astype(np.int8)
    return q.tobytes()


def decode_embedding(blob: bytes):
    """Dequantize stored int8 bytes to a float32 vector for mean-pooling
    track vectors into album/artist aggregates."""
    import numpy as np

    return np.frombuffer(blob, dtype=np.int8).astype(np.float32) / CLAP_INT8_SCALE


def normalise(v) -> "object":
    import numpy as np

    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else v
