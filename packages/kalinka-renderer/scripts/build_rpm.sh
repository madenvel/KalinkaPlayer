#!/bin/bash
# Build the kalinka-renderer RPM for the current arch on the running Fedora
# release (the %{?dist} tag encodes it, e.g. .fc45). Release build; symbols
# are split into the -debuginfo subpackage automatically, so the main RPM
# ships a stripped binary.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PKG_DIR"

# shellcheck source=version.sh
. "$SCRIPT_DIR/version.sh"
VERSION=$(renderer_version)
if [ -z "$VERSION" ]; then
    echo "Error: could not determine the renderer version." >&2
    exit 1
fi
echo "Building version: $VERSION"

TOPDIR="$PKG_DIR/build-rpm"

# rpmbuild --build-in-place litters the source dir with find-debuginfo lists
# and its cmake build tree. Cleared going in as well as coming out: that tree
# caches the source path, the path carries the version, and one left behind by
# an interrupted build fails the next one at a different version.
clean_source_litter() {
    rm -rf "$PKG_DIR/redhat-linux-build"
    rm -f "$PKG_DIR"/debugfiles.list "$PKG_DIR"/debuglinks.list \
        "$PKG_DIR"/debugsourcefiles.list "$PKG_DIR"/debugsources.list \
        "$PKG_DIR"/elfbins.list
}

rm -rf "$TOPDIR"
clean_source_litter

rpmbuild -bb --build-in-place \
    --define "renderer_version $VERSION" \
    --define "_topdir $TOPDIR" \
    rpm/kalinka-renderer.spec

RPM_PATH=$(find "$TOPDIR/RPMS" -name "kalinka-renderer-$VERSION*.rpm" ! -name '*debuginfo*' ! -name '*debugsource*' | head -1)
if [ -z "$RPM_PATH" ]; then
    echo "Error: rpmbuild produced no package." >&2
    exit 1
fi
cp "$RPM_PATH" "$PKG_DIR/"
clean_source_litter

echo "Successfully built: $(basename "$RPM_PATH")"
ls -lh "$PKG_DIR/$(basename "$RPM_PATH")"
