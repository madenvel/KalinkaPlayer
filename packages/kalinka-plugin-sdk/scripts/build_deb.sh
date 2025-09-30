#!/bin/bash
# Build Debian package for Kalinka Plugin SDK

set -e

echo "Building Kalinka Plugin SDK Debian package..."

# Clean previous builds
make clean

# Build the wheel and Debian package
make build-deb

echo "Debian package built successfully!"