#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/kalinka"
VENV_DIR="$INSTALL_DIR/venv"
WHEELS_DIR="$INSTALL_DIR/wheels"

# Create venv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
  echo "[bootstrap] Creating Python venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
  chown -R root:root "$VENV_DIR"
  chmod -R go-w "$VENV_DIR"
fi

# Install all wheels found in the wheels directory
shopt -s nullglob
wheels=( "$WHEELS_DIR"/*.whl )
if [ ${#wheels[@]} -gt 0 ]; then
  echo "[bootstrap] Installing wheels: ${wheels[*]}"
  "$VENV_DIR/bin/pip" install --quiet --upgrade "${wheels[@]}"
else
  echo "[bootstrap] No wheels found in $WHEELS_DIR"
fi
