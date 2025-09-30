#!/bin/bash

# Development environment setup script for KalinkaPlayer
# This script sets up all packages in development mode

set -e

echo "Setting up KalinkaPlayer development environment..."

# Navigate to project root
cd "$(dirname "$0")"

# Install kalinka-plugin-sdk in development mode
echo "Installing kalinka-plugin-sdk in development mode..."
cd packages/kalinka-plugin-sdk
pip install -e .

# Build and install kalinka-server in development mode
echo "Building native_player module..."
cd ../kalinka-server/src/native_player
python setup.py build_ext --inplace

echo "Installing kalinka-server in development mode..."
cd ../..
pip install -e .

# Return to project root
cd ../..

echo "Development environment setup complete!"
echo ""
echo "You can now:"
echo "  - Run the server: python -m kalinka_server"
echo "  - Import packages in your IDE/linter"
echo "  - Make changes to any package and they'll be immediately available"
echo ""
echo "To start the server:"
echo "  cd packages/kalinka-server"
echo "  python -m kalinka_server --config /path/to/kalinka_conf.cfg"