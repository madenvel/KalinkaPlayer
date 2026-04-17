#!/usr/bin/env python3
"""Export CLAP model (laion/clap-htsat-unfused) to ONNX format.

Produces three artifacts in the output directory:
  - clap_audio_encoder.onnx   (waveform -> 512-dim embedding)
  - clap_text_encoder.onnx    (token IDs + mask -> 512-dim embedding)
  - clap_tokenizer.json        (RoBERTa BPE tokenizer)

Requires: torch, laion-clap, transformers, onnx, onnxruntime
Run on a dev machine (NOT the RPi).

Usage:
    python scripts/export_clap_onnx.py [--output-dir ./clap_onnx] [--ckpt path/to/630k-audioset-best.pt]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn


# ---------------------------------------------------------------------------
# Wrapper modules for clean ONNX export (no dict I/O)
# ---------------------------------------------------------------------------

class AudioEncoderForExport(nn.Module):
    """Wraps HTSAT audio branch + projection for ONNX export.

    Input:  waveform  (batch, 480000)  float32 at 48 kHz
    Output: embedding (batch, 512)     float32 (pre-normalisation)
    """

    def __init__(self, audio_branch, audio_projection):
        super().__init__()
        self.spec = audio_branch.spectrogram_extractor
        self.logmel = audio_branch.logmel_extractor
        self.bn0 = audio_branch.bn0

        # Swin transformer body (forward_features)
        self.patch_embed = audio_branch.patch_embed
        self.ape = audio_branch.ape
        if self.ape:
            self.absolute_pos_embed = audio_branch.absolute_pos_embed
        self.pos_drop = audio_branch.pos_drop
        self.layers = audio_branch.layers
        self.norm = audio_branch.norm
        self.avgpool = audio_branch.avgpool

        # reshape_wav2img constants
        self.spec_size = audio_branch.spec_size
        self.freq_ratio = audio_branch.freq_ratio
        self.patch_stride = audio_branch.patch_stride
        self.depths = audio_branch.depths

        self.audio_projection = audio_projection

    def _reshape_wav2img(self, x: torch.Tensor) -> torch.Tensor:
        B, C, T, F = x.shape
        target_T = int(self.spec_size * self.freq_ratio)
        target_F = self.spec_size // self.freq_ratio
        if T < target_T:
            x = nn.functional.interpolate(
                x, (target_T, x.shape[3]), mode="bicubic", align_corners=True
            )
        if F < target_F:
            x = nn.functional.interpolate(
                x, (x.shape[2], target_F), mode="bicubic", align_corners=True
            )
        x = x.permute(0, 1, 3, 2).contiguous()
        x = x.reshape(x.shape[0], x.shape[1], x.shape[2], self.freq_ratio, x.shape[3] // self.freq_ratio)
        x = x.permute(0, 1, 3, 2, 4).contiguous()
        x = x.reshape(x.shape[0], x.shape[1], x.shape[2] * x.shape[3], x.shape[4])
        return x

    def _forward_features(self, x: torch.Tensor) -> torch.Tensor:
        """Returns the ``embedding`` (latent_output) vector only."""
        frames_num = x.shape[2]
        x = self.patch_embed(x)
        if self.ape:
            x = x + self.absolute_pos_embed
        x = self.pos_drop(x)
        for layer in self.layers:
            x, _attn = layer(x)
        x = self.norm(x)

        B, N, C = x.shape
        SF = frames_num // (2 ** (len(self.depths) - 1)) // self.patch_stride[0]
        ST = frames_num // (2 ** (len(self.depths) - 1)) // self.patch_stride[1]
        x = x.permute(0, 2, 1).contiguous().reshape(B, C, SF, ST)
        B, C, F, T = x.shape
        c_freq_bin = F // self.freq_ratio
        x = x.reshape(B, C, F // c_freq_bin, c_freq_bin, T)
        x = x.permute(0, 1, 3, 2, 4).contiguous().reshape(B, C, c_freq_bin, -1)

        latent_output = self.avgpool(torch.flatten(x, 2))
        latent_output = torch.flatten(latent_output, 1)
        return latent_output

    def forward(self, waveform: torch.Tensor) -> torch.Tensor:
        x = self.spec(waveform)        # (B, 1, T, freq_bins)
        x = self.logmel(x)             # (B, 1, T, mel_bins)
        x = x.transpose(1, 3)
        x = self.bn0(x)
        x = x.transpose(1, 3)
        x = self._reshape_wav2img(x)
        embedding = self._forward_features(x)
        return self.audio_projection(embedding)


class TextEncoderForExport(nn.Module):
    """Wraps RoBERTa text branch + projection for ONNX export.

    Input:  input_ids      (batch, 77)  int64
            attention_mask  (batch, 77)  int64
    Output: embedding      (batch, 512) float32 (pre-normalisation)
    """

    def __init__(self, text_branch, text_projection):
        super().__init__()
        self.text_branch = text_branch
        self.text_projection = text_projection

    def forward(
        self, input_ids: torch.Tensor, attention_mask: torch.Tensor
    ) -> torch.Tensor:
        x = self.text_branch(
            input_ids=input_ids, attention_mask=attention_mask
        ).pooler_output
        return self.text_projection(x)


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def load_clap(ckpt_path: str | None):
    """Load the full CLAP model with checkpoint."""
    import laion_clap

    clap = laion_clap.CLAP_Module(enable_fusion=False)
    if ckpt_path:
        clap.load_ckpt(ckpt_path)
    else:
        clap.load_ckpt()  # auto-downloads 630k-audioset-best.pt
    clap.model.eval()
    return clap


def export_audio_encoder(clap, out_dir: Path) -> Path:
    dest = out_dir / "clap_audio_encoder.onnx"
    wrapper = AudioEncoderForExport(
        clap.model.audio_branch, clap.model.audio_projection
    )
    wrapper.eval()

    dummy = torch.randn(1, 480000)
    with torch.no_grad():
        torch.onnx.export(
            wrapper,
            (dummy,),
            str(dest),
            opset_version=17,
            input_names=["waveform"],
            output_names=["embedding"],
            dynamic_axes={"waveform": {0: "batch"}, "embedding": {0: "batch"}},
            dynamo=False,
        )
    print(f"Audio encoder exported to {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def export_text_encoder(clap, out_dir: Path) -> Path:
    dest = out_dir / "clap_text_encoder.onnx"
    wrapper = TextEncoderForExport(
        clap.model.text_branch, clap.model.text_projection
    )
    wrapper.eval()

    dummy_ids = torch.randint(0, 50265, (1, 77), dtype=torch.long)
    dummy_mask = torch.ones(1, 77, dtype=torch.long)
    with torch.no_grad():
        torch.onnx.export(
            wrapper,
            (dummy_ids, dummy_mask),
            str(dest),
            opset_version=17,
            input_names=["input_ids", "attention_mask"],
            output_names=["embedding"],
            dynamic_axes={
                "input_ids": {0: "batch"},
                "attention_mask": {0: "batch"},
                "embedding": {0: "batch"},
            },
            dynamo=False,
        )
    print(f"Text encoder exported to {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def export_tokenizer(clap, out_dir: Path) -> Path:
    dest = out_dir / "clap_tokenizer.json"
    # RobertaTokenizerFast exposes the rust backend via .backend_tokenizer
    fast_tok = clap.tokenize
    if not hasattr(fast_tok, "backend_tokenizer"):
        from transformers import RobertaTokenizerFast
        fast_tok = RobertaTokenizerFast.from_pretrained("roberta-base")
    fast_tok.backend_tokenizer.save(str(dest))
    print(f"Tokenizer exported to {dest}")
    return dest


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(clap, out_dir: Path) -> None:
    """Compare PyTorch and ONNX outputs for audio and text embeddings."""
    import onnxruntime as ort
    from laion_clap.training.data import int16_to_float32, float32_to_int16

    print("\n--- Validation ---")

    # Audio validation with a synthetic waveform
    waveform = np.random.randn(480000).astype(np.float32) * 0.1
    waveform = int16_to_float32(float32_to_int16(waveform))

    # PyTorch path
    with torch.no_grad():
        wav_t = torch.from_numpy(waveform).float().unsqueeze(0)
        audio_wrapper = AudioEncoderForExport(
            clap.model.audio_branch, clap.model.audio_projection
        ).eval()
        pt_audio = audio_wrapper(wav_t).numpy()[0]

    # ONNX path
    sess_audio = ort.InferenceSession(
        str(out_dir / "clap_audio_encoder.onnx"),
        providers=["CPUExecutionProvider"],
    )
    onnx_audio = sess_audio.run(
        None, {"waveform": waveform[np.newaxis, :]}
    )[0][0]

    cos_audio = np.dot(pt_audio, onnx_audio) / (
        np.linalg.norm(pt_audio) * np.linalg.norm(onnx_audio) + 1e-12
    )
    print(f"Audio cosine similarity: {cos_audio:.6f}")

    # Text validation
    test_texts = ["energetic rock music", "calm piano jazz"]
    with torch.no_grad():
        tok_out = clap.tokenizer(test_texts)
        input_ids_np = tok_out["input_ids"].numpy()
        mask_np = tok_out["attention_mask"].numpy()

        text_wrapper = TextEncoderForExport(
            clap.model.text_branch, clap.model.text_projection
        ).eval()
        pt_text = text_wrapper(tok_out["input_ids"], tok_out["attention_mask"]).numpy()

    sess_text = ort.InferenceSession(
        str(out_dir / "clap_text_encoder.onnx"),
        providers=["CPUExecutionProvider"],
    )
    onnx_text = sess_text.run(
        None, {"input_ids": input_ids_np, "attention_mask": mask_np}
    )[0]

    for i, txt in enumerate(test_texts):
        cos_t = np.dot(pt_text[i], onnx_text[i]) / (
            np.linalg.norm(pt_text[i]) * np.linalg.norm(onnx_text[i]) + 1e-12
        )
        print(f"Text cosine similarity ({txt!r}): {cos_t:.6f}")

    if cos_audio < 0.999:
        print("WARNING: audio cosine similarity below 0.999 -- investigate!")
        sys.exit(1)
    print("\nValidation passed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Export CLAP to ONNX")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./clap_onnx",
        help="Directory for exported ONNX files",
    )
    parser.add_argument(
        "--ckpt",
        type=str,
        default=None,
        help="Path to CLAP checkpoint (auto-downloads 630k-audioset-best.pt if omitted)",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip numerical validation step",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading CLAP model...")
    clap = load_clap(args.ckpt)

    export_audio_encoder(clap, out_dir)
    export_text_encoder(clap, out_dir)
    export_tokenizer(clap, out_dir)

    if not args.skip_validation:
        validate(clap, out_dir)

    print(f"\nDone. ONNX files are in {out_dir}/")
    print("Copy clap_audio_encoder.onnx, clap_text_encoder.onnx, and clap_tokenizer.json")
    print("to the model_dir on your RPi (default: /var/lib/kalinka/models/).")


if __name__ == "__main__":
    main()
