"""Shared vector utilities for embedder and searcher."""

from __future__ import annotations


def encode_embedding(vector) -> bytes:
    import numpy as np

    return vector.astype(np.float32).tobytes()


def normalise(v) -> "object":
    import numpy as np

    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else v
