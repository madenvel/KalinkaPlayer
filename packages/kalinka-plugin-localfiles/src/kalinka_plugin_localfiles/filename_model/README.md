# Filename model

Artist, album, title, track number, disc number and year read out of a file's path, for files whose tags cannot supply them. A linear-chain CRF does the reading; `assembler.py` decides what this library does with the result.

Unlike the CLAP encoders, the weights are **bundled in the wheel** and loaded through `importlib.resources`. There is no download at first boot and no `crf-v*` GitHub release to look for.

## Currently shipping

| | |
| --- | --- |
| Weights | `weights/model.crfsuite`, 334,148 bytes |
| SHA-256 | `04377d6f69f73901ca246fa4651072e25fa6dd2c4573d807a71e55d9a12c0f80` |
| Feature version | 2 |
| Trained | 10 September 2026, seed 20260909 |
| Source | `madenvel/kalinka-training` at `f8e1b57`, `artifacts/filename/` |
| Runtime | `python-crfsuite==0.9.12` (MIT), pinned exactly |
| Model card | `docs/FILENAME_MODEL_CARD.md` in that repo |

`weights/model.training.json` is the training manifest, and `tests/test_filename_model_asset.py` pins the digest, the manifest pairing and the vendored runtime's per-file hashes. See `_vendor/README.md` to re-vendor.

The weights and the feature extractor must version together: a model built for another `FEATURE_VERSION` does not raise, it silently produces worse spans, so the loader compares the manifest against `features.FEATURE_VERSION` and refuses to open a mismatch.

## Measured

Against the embedded tags of a 526-track library, which the parser never saw. Tags are the only independent evidence available; some of them were themselves written from a filename, which flatters both rows equally.

| Ordinary paths (406 tracks) | Artist | Title | Track no. |
| --- | --- | --- | --- |
| Previous heuristics | 38.4% | 48.5% | 58.4% |
| This model | **72.2%** | **86.2%** | **76.8%** |

Bulk playlist downloads shaped `NNN-<id>-Artist-Title.mp3` are handled separately, by stripping both leading numbers and dropping the directory context. On that library's 69 such files the heuristics scored 0 for artist and title — the containing folder made them read `Playlist` as the artist — against 62.3% and 50.7% here. The leading number is a playlist position, so no track number is claimed for them at all.

## Known limitations

- **A generic container directory reads as an artist** when nothing better is named: `Albums/1995 - Made In Heaven…/04 - Mother Love.flac` yields the artist `Albums`. The previous heuristics failed the same way, yielding `1995`.
- Album titles agree with the album tag only ~45% of the time, because a folder name frequently is not the album's name. Raising the confidence floor does not help; it only mints fewer albums.
- Hebrew is the weakest script the model was measured on (55.4 F1) against ~95 for Latin and 92–96 for Han and Japanese.
- Every training label is weak supervision derived from library metadata, so reported accuracy is agreement, not verified truth. The model card is explicit about this.
- Raspberry Pi throughput is unverified; the figures in the model card come from the training host.

## Testing another model

```bash
KALINKA_FILENAME_MODEL=/path/to/other.crfsuite  # plus other.training.json beside it
```

The override skips the bundled weights entirely, so a new model can be tried without reinstalling. Its manifest is honoured when present and its absence only logs, since an override is a deliberate developer action.
