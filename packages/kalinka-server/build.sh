#!/bin/bash
# Build script for Kalinka Player

set -e

echo "Building Kalinka Player..."

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build/ dist/ *.egg-info/

# Build the native player extension
echo "Building native player..."
cd src/native_player
make clean
make
cd ..

# Build the Python package
echo "Building Python package..."
python3 -m build

echo "Build complete!"
echo "Distribution files are in the 'dist/' directory"

# Optional: Create Debian package if requested
if [ "$1" = "--debian" ] || [ "$1" = "--deb" ]; then
    echo "Building Debian package..."
    
    # Ensure we have a wheel to work with
    if [ ! -f dist/*.whl ]; then
        echo "Error: No wheel found in dist/. Building wheel first..."
        python3 -m build --wheel
    fi
    
    # Use the wheel for Debian packaging
    make WHEEL_PATH="$(ls dist/*.whl | head -1)" debian-from-wheel
    echo "Debian package created: kalinka-player-*.deb"
fi
