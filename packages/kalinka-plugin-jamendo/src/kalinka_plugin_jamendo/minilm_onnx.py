"""MiniLM sentence-embedding encoder (ONNX, torch-free) for query encoding.

`all-MiniLM-L6-v2` (BERT, 384-d) run via onnxruntime + the HF `tokenizers` fast
tokenizer; mean-pools token embeddings (attention-mask weighted) and
L2-normalizes. This is the exact encoder used offline to build the Jamendo
text index (kalinka-training `minilm_onnx.py`/`jamendomaxcaps_embed.py`) — query
and corpus MUST stay byte-identical, so keep the pooling here in sync with that.

Lazy-loaded and idle-unloaded by the caller so the ~90 MB model only sits in RAM
while searches are happening (the player's CLAP model already dominates memory).
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


def ensure_model(model_dir: str, base_url: Optional[str]) -> bool:
    """Ensure model.onnx + tokenizer.json exist in model_dir.

    Downloads from ``{base_url}/{file}`` if missing and a base_url is given.
    Returns True if both files are present afterwards.
    """
    model_dir = os.path.expanduser(model_dir)
    os.makedirs(model_dir, exist_ok=True)
    for name in _FILES:
        dest = os.path.join(model_dir, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            continue
        if not base_url:
            return False
        url = f"{base_url.rstrip('/')}/{name}"
        tmp = dest + ".part"
        try:
            logger.info("Downloading MiniLM file %s from %s", name, url)
            urllib.request.urlretrieve(url, tmp)
            os.replace(tmp, dest)
        except Exception as e:
            logger.warning("MiniLM download failed for %s: %s", name, e)
            if os.path.exists(tmp):
                os.remove(tmp)
            return False
    return all(
        os.path.exists(os.path.join(model_dir, f)) for f in _FILES
    )


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
