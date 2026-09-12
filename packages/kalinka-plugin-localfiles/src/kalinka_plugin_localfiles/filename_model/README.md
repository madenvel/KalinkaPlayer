# Filename model

Artist, album, title, track number, disc number and year read out of a file's path, for files whose tags cannot supply them. A linear-chain CRF does the reading; `assembler.py` decides what this library does with the result.

Unlike the CLAP encoders, the weights are **bundled in the wheel** and loaded through `importlib.resources`. There is no download at first boot and no `crf-v*` GitHub release to look for.

## Currently shipping

| | |
| --- | --- |
| Weights | `weights/model.crfsuite`, 337,960 bytes |
| SHA-256 | `f4b28c2c29aa3df006581de94905992d26372ab939810f73d527a961e1f924e4` |
| Feature version | 2 |
| Trained | 11 September 2026, seed 20260909 |
| Source | `madenvel/kalinka-training` at `280271c`, `artifacts/filename/` |
| Runtime | `python-crfsuite==0.9.12` (MIT), pinned exactly |
| Model card | `docs/FILENAME_MODEL_CARD.md` in that repo |

`weights/model.training.json` is the training manifest, and `tests/test_filename_model_asset.py` pins the digest, the manifest pairing and the vendored runtime's per-file hashes. See `_vendor/README.md` to re-vendor.

The weights and the feature extractor must version together: a model built for another `FEATURE_VERSION` does not raise, it silently produces worse spans, so the loader compares the manifest against `features.FEATURE_VERSION` and refuses to open a mismatch.

## Measured

Against the embedded tags of a 526-track library, which the parser never saw. Tags are the only independent evidence available; some of them were themselves written from a filename, which flatters both rows equally. The sweep was run once, on the first weights to ship here; later retrains have not been re-swept, so read the table as what replacing the heuristics bought rather than as this model's current score.

| Ordinary paths (406 tracks) | Artist | Title | Track no. |
| --- | --- | --- | --- |
| Previous heuristics | 38.4% | 48.5% | 58.4% |
| This model | **72.2%** | **86.2%** | **76.8%** |

Bulk playlist downloads shaped `NNN-<id>-Artist-Title.mp3` are handled separately, by stripping both leading numbers and dropping the directory context. On that library's 69 such files the heuristics scored 0 for artist and title — the containing folder made them read `Playlist` as the artist — against 62.3% and 50.7% here. The leading number is a playlist position, so no track number is claimed for them at all.

## Vinyl sides

`B1 Goodbye Blue Sky.flac` used to read as one title, which cost The Wall every track and disc number it has. The model now labels the side letter `DISC_NUMBER` and the digit after it `TRACK_NUMBER`, so ordering by `(disc, track)` reproduces the play order. A side is not a disc, and `A`–`H` become 1–8; `numeric_value` in the vendored runtime owns that reading, which is why `assembler._number` asks it rather than calling `int`.

Measured upstream on the 411 adjudicated tracks of a running instance, which hold both the benefit and the cost of the change: title 89.5 → 95.6, track number 92.5 → 99.7, disc number 74.5 → 99.1, all six fields together 91.2 → 92.9. The curated Latin test split holds no side-lettered row and so shows only the cost, 95.40 → 94.93 F1.

## Known limitations

- **An artist named like a side marker is dropped rather than misread.** The tagger learned the shape of `B1`, not the letter set, so it calls the `U` of `U96 - Das Boot` a side; the runtime rejects a lettered disc outside `A`–`H` along with the digits paired to it, and the artist goes with them. `U96 - Das Boot.mp3` yields no artist where the previous weights yielded `U96`. Upstream accepted this for the numbers above.
- **A spaced dash is always a separator.** `2.Красно - желтые дни.flac` is one title, and the model reads `Красно` as an artist. With directory context the assembler puts it back together, since the folder names the real artist; a file sitting directly in a music root has nothing to undo it with and keeps the split. Narrowing this would cost flat `Artist - Title.mp3` libraries, which are a convention this plugin measures against, so it is accepted.
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
