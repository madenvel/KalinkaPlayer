#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/kalinka"
VENV_DIR="$INSTALL_DIR/venv"
WHEELS_DIR="$INSTALL_DIR/wheels"
MANIFESTS_DIR="$INSTALL_DIR/allowed_packages"
CACHE_DIR="/var/cache/kalinka"
STATE_DIR="/var/lib/kalinka"
PENDING_INSTALLS="$STATE_DIR/pending_installs.json"
LAST_INSTALL="$STATE_DIR/last_install.json"

# Under systemd hardening (`ProtectHome=yes`), pip should not rely on $HOME/.cache.
# Use the service cache directory so wheel installs keep cache enabled.
export XDG_CACHE_HOME="$CACHE_DIR"
export PIP_CACHE_DIR="$CACHE_DIR/pip"
mkdir -p "$PIP_CACHE_DIR"
chmod 755 "$CACHE_DIR" "$PIP_CACHE_DIR" || true
# numba's cache dir is set via Environment= in kalinka.service — it
# isn't pre-created here because mkdir under ExecStartPre runs as root
# and would leave the dir root-owned. numba creates it on first JIT
# compile as kalusr (who already owns /var/cache/kalinka thanks to
# CacheDirectory=kalinka in the unit).

# Prefer prebuilt ARM wheels from piwheels (Raspberry-Pi-specific
# mirror) so numpy / librosa / scipy / numba don't compile from source
# on a Pi — that takes 5-15 minutes per package and is the main reason
# install_pending used to blow systemd's start timeout. piwheels only
# returns matches for the cp* + linux_armv*l platform tags it builds
# for; on non-ARM hardware (or for packages it lacks) pip simply
# moves on to PyPI. Override either URL via the systemd unit's
# Environment= directive if you need a different mirror.
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://www.piwheels.org/simple}"
export PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL:-https://pypi.org/simple}"

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

# Apply any pending optional-package install requests. The helper reads
# the kalusr-written request file, intersects it with deb-shipped
# manifests in $MANIFESTS_DIR (root-owned, immutable to kalusr), and
# pip-installs the resolved specs. Failures don't block server startup —
# the helper writes results to $LAST_INSTALL and moves the request file
# aside so the same request isn't retried on every boot.
if [ -f "$PENDING_INSTALLS" ]; then
  echo "[bootstrap] Found pending installs at $PENDING_INSTALLS"
  "$VENV_DIR/bin/python" -m kalinka_server.install_pending \
    "$PENDING_INSTALLS" "$MANIFESTS_DIR" "$LAST_INSTALL" \
    || echo "[bootstrap] install_pending exited non-zero; continuing"
fi
