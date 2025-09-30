#!/bin/bash
# Build script for Kalinka Plugin SDK

set -e

echo "Building Kalinka Plugin SDK..."

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build/ dist/ src/*.egg-info/

# Build the Python package
echo "Building Python package..."
python3 -m pip install --upgrade build >/dev/null 2>&1 || echo "Warning: Could not upgrade build tools"
python3 -m build

echo "Build complete!"
echo "Distribution files are in the 'dist/' directory"

# List the created files
echo "Created files:"
ls -la dist/

# Optional: Create Debian package if requested
if [ "$1" = "--debian" ] || [ "$1" = "--deb" ]; then
    echo "Building Debian package..."
    
    # Ensure we have a wheel to work with
    if [ ! -f dist/*.whl ]; then
        echo "Error: No wheel found in dist/. Building wheel first..."
        python3 -m build --wheel
    fi
    
    # Use the wheel for Debian packaging
    make WHEEL_PATH="$(ls dist/*.whl | head -1)" build-deb
    echo "Debian package created: kalinka-plugin-sdk-*.deb"
fi