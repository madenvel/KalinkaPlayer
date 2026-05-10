"""Optional pip packages this plugin can install on demand.

Static allow-list. Pinned versions live here, not in install requests.
Importing this module must not pull heavy ML deps — it's loaded by the
deb build script to emit /opt/kalinka/allowed_packages/localfiles.json
and by the running server to register the catalog.

Run as a module to print the manifest JSON:

    python -m kalinka_plugin_localfiles.optional_packages
"""

from __future__ import annotations

import json
import sys

from kalinka_plugin_sdk import OptionalPackageSpec


PLUGIN_ID = "localfiles"
MANIFEST_SCHEMA_VERSION = 1


# Triggered-by paths use the same dotted form as PUT /server/config keys,
# so the UI can correlate "this setting requires this package" without
# extra mapping.
OPTIONAL_PACKAGES: dict[str, OptionalPackageSpec] = {
    "numpy": OptionalPackageSpec(
        pip_spec="numpy==1.26.4",
        description=(
            "Numerical core required by AI tag prediction (searcher) and "
            "CLAP audio embedding (embedder)."
        ),
        triggered_by=(
            "input_modules.localfiles.searcher.enabled",
            "input_modules.localfiles.embedder.enabled",
        ),
    ),
    "onnxruntime": OptionalPackageSpec(
        pip_spec="onnxruntime==1.24.4",
        description=(
            "ONNX inference runtime for the CLAP audio/text encoder used "
            "by AI search."
        ),
        triggered_by=("input_modules.localfiles.embedder.enabled",),
    ),
    "librosa": OptionalPackageSpec(
        pip_spec="librosa==0.11.0",
        description=(
            "Audio loading and resampling for the CLAP audio embedder."
        ),
        triggered_by=("input_modules.localfiles.embedder.enabled",),
    ),
    "tokenizers": OptionalPackageSpec(
        pip_spec="tokenizers==0.22.2",
        description="HuggingFace tokenizers used by the CLAP text encoder.",
        triggered_by=("input_modules.localfiles.embedder.enabled",),
    ),
    "essentia-tensorflow": OptionalPackageSpec(
        pip_spec="essentia-tensorflow==2.1b6.dev1389",
        description=(
            "Essentia + TensorFlow build used by tag prediction "
            "(genre/mood/danceability). Large download (~500 MB) "
            "and first-time install may take several minutes on a Pi."
        ),
        import_name="essentia",
        triggered_by=(
            "input_modules.localfiles.searcher.tags.enabled",
        ),
    ),
}


def manifest() -> dict:
    """Build the JSON-serialisable manifest payload."""
    return {
        "plugin": PLUGIN_ID,
        "schema": MANIFEST_SCHEMA_VERSION,
        "packages": {
            key: spec.model_dump() for key, spec in OPTIONAL_PACKAGES.items()
        },
    }


if __name__ == "__main__":
    json.dump(manifest(), sys.stdout, indent=2)
    sys.stdout.write("\n")
