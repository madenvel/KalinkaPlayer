# Vendored `filename_parser`

Copied verbatim from `madenvel/kalinka-training` at commit `f8e1b57`, path
`filename_parser/`. **Never edit anything under `filename_parser/` here** —
`filename_parser.sha256` pins every file and `tests/test_filename_model_asset.py`
fails if one drifts. Local behaviour belongs in `../assembler.py`.

Only the four runtime modules are taken. The rest of the upstream package
(`data.py`, `train.py`, `synthetic.py`, `harvest.py`, `mbpool.py`, `tags.py`,
`evaluate.py`, `benchmark.py`, `baseline.py`) is training-time only, and
`tags.py` shells out to `ffprobe`, so none of it may reach the device.

The runtime import closure is `filename_parser` → `.common`, `.features`,
`.model` → `pycrfsuite`, and nothing else.

## Re-vendoring

```bash
SRC=…/kalinka-training
cp $SRC/filename_parser/{__init__,common,features,model}.py filename_parser/
cp $SRC/artifacts/filename/model.crfsuite      ../weights/
cp $SRC/artifacts/filename/model.training.json ../weights/
(cd filename_parser && sha256sum __init__.py common.py features.py model.py) > filename_parser.sha256
```

Then update the expected weights digest in `tests/test_filename_model_asset.py`
and the table in `../README.md`. `features.FEATURE_VERSION` and the manifest's
`feature_version` must agree or the model refuses to load — that is the guard
against a half-finished re-vendor.

`model.py` carries a `DEFAULT_MODEL` path that points into the training repo's
layout and is wrong here. It is inert: nothing in this package calls
`parse_filename` or `_default_parser`, which are the only things that read it.
`parser.py` resolves the weights itself.
