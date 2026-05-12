#!/bin/bash

set -e

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ARCH=$(dpkg --print-architecture)

# The deb is pinned to Python 3.11 (control.in declares python3.11 as a
# Depends). The wheel must therefore be built with python3.11 too so its
# cpXY tag matches what the venv on the Pi will use. If you're building
# on a host with a different default python3, run this script under
# `python3.11 -m build` or set up python3.11 as the build interpreter.
echo "Building wheel with native extensions..."

if ! command -v python3 > /dev/null || [ ! -f setup.py ]; then
    echo "Error: Python build tools not found."
    exit 1
fi

python3 -m pip install --upgrade build || true
rm -rf dist/*.whl

python3 -m build --wheel

WHEEL_PATH=$(ls dist/*.whl 2>/dev/null | head -1)

if [ -z "$WHEEL_PATH" ] || [ ! -f "$WHEEL_PATH" ]; then
    echo "Error: No wheel could be built."
    exit 1
fi

echo "Getting version directly from source..."

WHEEL_VERSION=$(grep "__version__ = version =" "$SCRIPT_DIR/../src/kalinka_server/_version.py" | sed -n "s/.*['\"]\\([^'\"]*\\)['\"].*/\\1/p")

if [ -z "$WHEEL_VERSION" ]; then
    echo "Error: Could not determine package version."
    exit 1
fi


WHEEL_BASENAME=$(basename "$WHEEL_PATH")
TARGET_DIR="kalinka-server-$WHEEL_VERSION"
TARGET_FILE="$TARGET_DIR.$ARCH.deb"

echo "Using wheel: $WHEEL_BASENAME"
echo "Building Debian package with version: $WHEEL_VERSION"
echo "Target directory: $TARGET_DIR"
echo "Target file: $TARGET_FILE"

mkdir -p "$TARGET_DIR"
cp -r DEBIAN "$TARGET_DIR"

sed "s/@ARCH@/$ARCH/; s/@VERSION@/$WHEEL_VERSION/" DEBIAN/control.in > "$TARGET_DIR/DEBIAN/control"
rm "$TARGET_DIR/DEBIAN/control.in"

mkdir -p "$TARGET_DIR/opt/kalinka/wheels"
mkdir -p "$TARGET_DIR/etc/systemd/system/"
mkdir -p "$TARGET_DIR/usr/lib/tmpfiles.d/"

cp "$WHEEL_PATH" "$TARGET_DIR/opt/kalinka/wheels/"
cp scripts/bootstrap.sh "$TARGET_DIR/opt/kalinka/bootstrap.sh"
chmod 755 "$TARGET_DIR/opt/kalinka/bootstrap.sh"
cp scripts/kalinka.service "$TARGET_DIR/etc/systemd/system/"
cp scripts/kalinka-restart.path "$TARGET_DIR/etc/systemd/system/"
cp scripts/kalinka-restart.service "$TARGET_DIR/etc/systemd/system/"
cp scripts/kalinka.tmpfiles.conf "$TARGET_DIR/usr/lib/tmpfiles.d/kalinka.conf"
cp ../../README.md "$TARGET_DIR/opt/kalinka/"
cp LICENSE "$TARGET_DIR/opt/kalinka/"

dpkg-deb --root-owner-group --build "$TARGET_DIR"
mv "$TARGET_DIR.deb" "$TARGET_FILE"
rm -rf "$TARGET_DIR"

echo "Successfully built: $TARGET_FILE"