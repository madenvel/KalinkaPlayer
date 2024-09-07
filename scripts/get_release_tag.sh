#!/bin/bash

# Get the current commit hash (shortened to 7 characters)
CURRENT_HASH=$(git rev-parse --short HEAD)

# Check if HEAD has a tag that follows the 'release-*.*.*' pattern
RELEASE_TAG=$(git describe --exact-match --tags HEAD 2>/dev/null | grep -E '^release-[0-9]+\.[0-9]+\.[0-9]+$')

# If a release tag is found, echo it; otherwise, echo the current commit hash
if [ -n "$RELEASE_TAG" ]; then
    VERSION_NUMBER=${RELEASE_TAG#release-}
    echo "$VERSION_NUMBER"
else
    echo "$CURRENT_HASH"
fi
