"""MiniLM query encoder (all-MiniLM-L6-v2, ONNX, torch-free).

Tokenize -> attention-masked mean-pool -> L2-normalize, producing the 384-d
vector the Jamendo mood index is built from. Lazy-loaded on first use.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from typing import List, Optional

logger = logging.getLogger(__name__.split(".")[-1])

DIM = 384
MAX_TOKENS = 256
_FILES = ("model.onnx", "tokenizer.json")


def download_file(url: str, dest: str) -> bool:
    """Download url -> dest atomically (via a .part temp). True on success."""
    tmp = dest + ".part"
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        logger.info("downloading %s", url)
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, dest)
        return True
    except Exception as e:
        logger.warning("download failed (%s): %s", url, e)
        if os.path.exists(tmp):
            os.remove(tmp)
        return False


def ensure_model(model_dir: str, base_url: Optional[str]) -> bool:
    """Ensure model.onnx + tokenizer.json are in model_dir, downloading any
    missing file from ``{base_url}/{file}``. True if both are present."""
    model_dir = os.path.expanduser(model_dir)
    for name in _FILES:
        dest = os.path.join(model_dir, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            continue
        if not base_url or not download_file(f"{base_url.rstrip('/')}/{name}", dest):
            return False
    return True


class MiniLmOnnx:
    def __init__(self, model_dir: str):
        self._model_dir = os.path.expanduser(model_dir)
        self._session = None
        self._tokenizer = None
        self._input_names: set = set()

    @property
    def is_loaded(self) -> bool:
        return self._session is not None

    def load(self) -> None:
        if self.is_loaded:
            return
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = os.path.join(self._model_dir, "model.onnx")
        tok_path = os.path.join(self._model_dir, "tokenizer.json")
        if not os.path.exists(model_path) or not os.path.exists(tok_path):
            raise FileNotFoundError(
                f"MiniLM model/tokenizer missing in {self._model_dir}"
            )
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        opts.enable_mem_pattern = False
        self._session = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(tok_path)
        self._tokenizer.enable_truncation(max_length=MAX_TOKENS)
        logger.info("MiniLM ONNX encoder loaded from %s", self._model_dir)

    def unload(self) -> None:
        self._session = None
        self._tokenizer = None
        self._input_names = set()

    def encode_one(self, text: str):
        """Encode a single query string -> (384,) float32 L2-normalized ndarray."""
        import numpy as np

        if not self.is_loaded:
            self.load()
        enc = self._tokenizer.encode(text or "")
        ids = np.asarray([enc.ids], np.int64)
        mask = np.asarray([enc.attention_mask], np.int64)
        feeds = {
            "input_ids": ids,
            "attention_mask": mask,
            "token_type_ids": np.zeros_like(ids),
        }
        feeds = {k: v for k, v in feeds.items() if k in self._input_names}
        hidden = self._session.run(None, feeds)[0]  # (1, seq, 384)
        m = mask[..., None].astype(np.float32)
        pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
        vec = pooled[0]
        norm = float(np.linalg.norm(vec))
        return (vec / norm if norm > 0 else vec).astype(np.float32)
