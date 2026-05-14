"""ONNX Runtime wrapper for CLAP (laion/clap-htsat-unfused).

Replaces the PyTorch-based ``laion_clap`` library with a lightweight
ONNX Runtime backend, reducing the embedder process memory footprint
from ~1.7 GB to ~300-400 MB.

Expected model files in *model_dir*:
  - clap_audio_encoder.onnx
  - clap_text_encoder.onnx
  - clap_tokenizer.json

Generate these with ``scripts/export_clap_onnx.py``.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__.split(".")[-1])

# ---------------------------------------------------------------------------
# Model download URLs — update after hosting ONNX files
# ---------------------------------------------------------------------------

_RELEASE_BASE = (
    "https://github.com/madenvel/KalinkaPlayer/releases/download/clap-onnx-v1"
)

_MODEL_URLS: dict[str, str] = {
    "clap_audio_encoder": f"{_RELEASE_BASE}/clap_audio_encoder.onnx",
    "clap_audio_encoder_data": f"{_RELEASE_BASE}/clap_audio_encoder.onnx.data",
    "clap_text_encoder": f"{_RELEASE_BASE}/clap_text_encoder.onnx",
    "clap_tokenizer": f"{_RELEASE_BASE}/clap_tokenizer.json",
}

_MODEL_FILENAMES: dict[str, str] = {
    "clap_audio_encoder": "clap_audio_encoder.onnx",
    "clap_audio_encoder_data": "clap_audio_encoder.onnx.data",
    "clap_text_encoder": "clap_text_encoder.onnx",
    "clap_tokenizer": "clap_tokenizer.json",
}

# Audio constants matching laion_clap (non-fusion, HTSAT-tiny)
_SAMPLE_RATE = 48_000
_MAX_SAMPLES = 480_000  # 10 seconds at 48 kHz
_TOKEN_MAX_LEN = 77


def _ensure_model_file(name: str, model_dir: str) -> Optional[str]:
    """Return path to a model file, downloading if necessary.

    Writes to ``<dest>.part`` first and atomic-renames on success.
    Without that, an interrupted ``urlretrieve`` leaves a truncated
    file at ``dest``; on the next call ``os.path.isfile(dest)`` short-
    circuits and onnxruntime fails to parse the partial ONNX — and
    the download never retries because the loader's failure is not
    interpreted as "redownload". Stale ``.part`` from a previous
    interrupted run is removed before the new attempt starts.
    """
    filename = _MODEL_FILENAMES.get(name)
    if not filename:
        logger.error("Unknown model file: %s", name)
        return None

    # Create the model directory tree up front, before checking for
    # existing files. Without this, a fresh install on a host where
    # /var/lib/kalinka/models doesn't exist yet would race
    # `os.path.isfile(dest)` returning False (because the dir is
    # absent) into the download branch, which then has to create the
    # dir anyway — and if `url` is None for some other code path,
    # the directory never gets created and a manual file-copy is
    # impossible without sudo.
    try:
        os.makedirs(model_dir, exist_ok=True)
    except OSError as e:
        logger.error("Cannot create model dir %s: %s", model_dir, e)
        return None

    dest = os.path.join(model_dir, filename)
    if os.path.isfile(dest):
        return dest

    url = _MODEL_URLS.get(name)
    if not url:
        logger.error(
            "Model file '%s' not found at %s and no download URL configured. "
            "Run scripts/export_clap_onnx.py and copy files to %s.",
            filename,
            dest,
            model_dir,
        )
        return None

    tmp = dest + ".part"
    # Clear stale .part from a previous interrupted run (e.g. SIGKILL
    # mid-download). Cannot be a partial-but-resumable file because
    # urllib.request.urlretrieve doesn't support range requests.
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass

    logger.info("Downloading '%s' from %s ...", name, url)
    try:
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, dest)
    except Exception as e:
        logger.error("Failed to download '%s': %s", name, e)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        return None

    logger.info("Saved '%s' to %s", name, dest)
    return dest


# ---------------------------------------------------------------------------
# Audio preprocessing (replaces laion_clap waveform handling)
# ---------------------------------------------------------------------------

def _quantize(waveform: np.ndarray) -> np.ndarray:
    """Replicate laion_clap's float32 -> int16 -> float32 quantisation."""
    clipped = np.clip(waveform, -1.0, 1.0)
    as_int16 = (clipped * 32767.0).astype(np.int16)
    return (as_int16 / 32767.0).astype(np.float32)


def _pad_or_crop(waveform: np.ndarray, max_len: int = _MAX_SAMPLES) -> np.ndarray:
    """Repeat-pad short audio, crop long audio (deterministic first-crop)."""
    length = len(waveform)
    if length >= max_len:
        return waveform[:max_len]
    # repeat-pad: tile then zero-pad remainder
    n_repeat = max_len // length
    padded = np.tile(waveform, n_repeat)
    remainder = max_len - len(padded)
    if remainder > 0:
        padded = np.concatenate([padded, np.zeros(remainder, dtype=np.float32)])
    return padded


def _load_audio(file_path: str) -> Optional[np.ndarray]:
    """Load and preprocess audio to (480000,) float32 at 48 kHz."""
    try:
        import librosa

        # The HTSAT-unfused checkpoint only consumes a fixed 10-second
        # window, so we limit librosa to that span instead of decoding
        # the whole file (a 10-min FLAC ≈ 110 MB float32, the main OOM
        # contributor on the 4 GB Pi).
        #
        # Offset past the intro for tracks long enough to afford it:
        # fade-ins / silence / spoken intros aren't characteristic of
        # the song and degrade retrieval quality. get_duration with
        # path= is a header-only probe (no decode) via the soundfile
        # backend, so the cost is negligible.
        try:
            duration_s = librosa.get_duration(path=file_path)
        except Exception:
            duration_s = 0.0
        offset = 15.0 if duration_s >= 30.0 else 0.0

        waveform, _ = librosa.load(
            file_path,
            sr=_SAMPLE_RATE,
            mono=True,
            offset=offset,
            duration=10.0,
        )
        waveform = _quantize(waveform)
        waveform = _pad_or_crop(waveform)
        return waveform
    except Exception as e:
        logger.warning("Failed to load audio %s: %s", file_path, e)
        return None


# ---------------------------------------------------------------------------
# ClapOnnxModel
# ---------------------------------------------------------------------------

class ClapOnnxModel:
    """ONNX Runtime-based CLAP model for audio and text embedding.

    Drop-in replacement for laion_clap with the same lifecycle:
    load() -> get_audio_embedding / get_text_embedding -> unload().
    """

    def __init__(self, model_dir: str, ckpt_path: str = ""):
        # ckpt_path overrides model_dir if it points to a directory with ONNX files
        self._model_dir = ckpt_path if (ckpt_path and os.path.isdir(ckpt_path)) else model_dir
        self._audio_session = None
        self._text_session = None
        self._tokenizer = None

    @property
    def is_loaded(self) -> bool:
        return self._audio_session is not None

    def load(self) -> None:
        """Download (if needed) and create ONNX inference sessions."""
        if self.is_loaded:
            return

        import onnxruntime as ort

        audio_path = _ensure_model_file("clap_audio_encoder", self._model_dir)
        _ensure_model_file("clap_audio_encoder_data", self._model_dir)
        text_path = _ensure_model_file("clap_text_encoder", self._model_dir)
        tok_path = _ensure_model_file("clap_tokenizer", self._model_dir)

        if not audio_path or not text_path or not tok_path:
            raise FileNotFoundError(
                f"CLAP ONNX model files missing from {self._model_dir}. "
                "Run scripts/export_clap_onnx.py on a dev machine first."
            )

        sess_opts = ort.SessionOptions()
        sess_opts.inter_op_num_threads = 1
        sess_opts.intra_op_num_threads = 2
        # ORT's CPU memory arena grows to the high-water mark of
        # intermediate tensors and never returns pages to the OS, which
        # on a 4 GB Pi accumulates into OOM territory over a few hundred
        # tracks. Disabling the arena costs ~5-10% inference latency
        # but keeps the RSS flat.
        sess_opts.enable_cpu_mem_arena = False
        sess_opts.enable_mem_pattern = False
        providers = ["CPUExecutionProvider"]

        self._audio_session = ort.InferenceSession(
            audio_path, sess_options=sess_opts, providers=providers
        )
        logger.info("CLAP audio ONNX session loaded")

        self._text_session = ort.InferenceSession(
            text_path, sess_options=sess_opts, providers=providers
        )
        logger.info("CLAP text ONNX session loaded")

        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(tok_path)
        self._tokenizer.enable_truncation(max_length=_TOKEN_MAX_LEN)
        self._tokenizer.enable_padding(
            length=_TOKEN_MAX_LEN, pad_id=1, pad_token="<pad>"
        )
        logger.info("CLAP tokenizer loaded")

    def unload(self) -> None:
        """Release ONNX sessions and tokenizer."""
        self._audio_session = None
        self._text_session = None
        self._tokenizer = None

    def get_audio_embedding(self, file_path: str) -> Optional[np.ndarray]:
        """Compute 512-dim audio embedding. Returns None on failure."""
        if not self.is_loaded:
            return None
        waveform = _load_audio(file_path)
        if waveform is None:
            return None
        try:
            result = self._audio_session.run(
                None, {"waveform": waveform[np.newaxis, :]}
            )
            return result[0][0].astype(np.float32)
        except Exception as e:
            logger.warning("ONNX audio inference failed for %s: %s", file_path, e)
            return None

    def get_text_embedding(self, text: str) -> Optional[np.ndarray]:
        """Compute 512-dim text embedding. Returns None on failure."""
        if not self.is_loaded:
            return None
        try:
            encoded = self._tokenizer.encode(text)
            input_ids = np.array([encoded.ids], dtype=np.int64)
            attention_mask = np.array([encoded.attention_mask], dtype=np.int64)
            result = self._text_session.run(
                None,
                {"input_ids": input_ids, "attention_mask": attention_mask},
            )
            return result[0][0].astype(np.float32)
        except Exception as e:
            logger.warning("ONNX text inference failed: %s", e)
            return None
