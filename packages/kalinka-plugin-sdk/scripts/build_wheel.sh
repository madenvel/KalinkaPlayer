#!/bin/bash
# Build wheel package for Kalinka Plugin SDK

set -e

echo "Building Kalinka Plugin SDK wheel..."

cd "$(dirname "$0")/.."

# Clean previous builds
rm -rf build/ dist/ src/*.egg-info/

# Build the wheel
python3 -m pip install --upgrade build >/dev/null 2>&1 || echo "Warning: Could not upgrade build tools"
python3 -m build --wheel

echo "Wheel built successfully!"
echo "Created:"
ls -la dist/