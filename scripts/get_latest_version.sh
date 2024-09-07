#!/bin/bash

# Get the most recent tag that matches the 'release-*.*.*' pattern
LATEST_TAG=$(git describe --tags --match "release-*.*.*" --abbrev=0 2>/dev/null)

# If a release tag is found, remove 'release-' prefix and echo the version number; otherwise, output a message
if [ -n "$LATEST_TAG" ]; then
    VERSION_NUMBER=${LATEST_TAG#release-}
    echo "$VERSION_NUMBER"
else
    echo "0.0.0"
fi