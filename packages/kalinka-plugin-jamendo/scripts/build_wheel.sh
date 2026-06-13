#!/usr/bin/env bash
set -euo pipefail

PLUGIN_SLUG="kalinka-plugin-jamendo"

echo "Building wheel for ${PLUGIN_SLUG} using setuptools_scm for version detection"

# Install required build tools
python3 -m pip install --upgrade build setuptools-scm

# Build the wheel (setuptools_scm handles version detection and _version.py)
python3 -m build --wheel
