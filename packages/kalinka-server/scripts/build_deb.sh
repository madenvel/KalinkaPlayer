#!/bin/bash

set -e

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ARCH=$(dpkg --print-architecture)
PYTHON_VERSION=$(python3 -c "import sys; print('{}.{}'.format(*sys.version_info[:2]))")
PYTHON_VERSION_UPPER=$(python3 -c "import sys; print('{}.{}'.format(sys.version_info[0], sys.version_info[1] + 1))")

echo "Building wheel with native extensions..."

if ! command -v python3 > /dev/null || [ ! -f setup.py ]; then
    echo "Error: Python build tools not found."
    exit 1
fi

python3 -m pip install --upgrade build || true
rm -rf dist/*.whl

python3 -m build --wheel

WHEEL_PATH=$(ls dist/*.whl 2>/dev/null | sort -V | tail -1 || true)

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


# Identify the platform we're building on (e.g. debian-13, ubuntu-24.04,
# fedora-44). The server deb is platform-specific: it carries a compiled
# extension linked against this distro's library sonames, so a Debian-built
# deb and an Ubuntu-built deb are not interchangeable even at the same arch.
# Encode the platform in the filename so the right artifact is obvious.
# (The arch-independent _all plugin/SDK debs are pure Python and stay
# unsuffixed — they install on any platform.)
PLATFORM="unknown"
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    PLATFORM="${ID:-unknown}-${VERSION_ID:-${VERSION_CODENAME:-unknown}}"
fi

WHEEL_BASENAME=$(basename "$WHEEL_PATH")
TARGET_DIR="kalinka-server-$WHEEL_VERSION"
TARGET_FILE="$TARGET_DIR.$PLATFORM.$ARCH.deb"

echo "Using wheel: $WHEEL_BASENAME"
echo "Building Debian package with version: $WHEEL_VERSION"
echo "Target directory: $TARGET_DIR"
echo "Target file: $TARGET_FILE"

mkdir -p "$TARGET_DIR"
cp -r DEBIAN "$TARGET_DIR"

# Resolve the native extension's shared-library dependencies.
#
# Prefer dpkg-shlibdeps run against the compiled .so: it maps the libraries
# the extension actually links to the packages that provide them on *this*
# Debian release, so the Depends are correct whether we build on Bookworm,
# Trixie, etc. (their sonames differ, e.g. libfmt9 vs libfmt10). Fall back to
# a static, hand-maintained list when dpkg-shlibdeps isn't available — e.g.
# local builds on a non-Debian distro — so those keep working unchanged.
STATIC_LIBDEPS="libcurlpp0, libcurl4, libflac++10, libflac12, libasound2, libspdlog1.10, libfmt9, libstdc++6, libc6, libgcc-s1, libnghttp2-14, libidn2-0, libssl3, libgssapi-krb5-2, zlib1g, libogg0, libunistring2, libkrb5-3, libk5crypto3, libcom-err2, libkrb5support0, libkeyutils1, libselinux1, libpcre2-8-0"
SHLIBDEPS=""
if command -v dpkg-shlibdeps > /dev/null 2>&1 && command -v unzip > /dev/null 2>&1; then
    SHLIB_WORK=$(mktemp -d)
    if unzip -o -q "$(readlink -f "$WHEEL_PATH")" '*.so' -d "$SHLIB_WORK" 2>/dev/null; then
        SHLIB_SO=$(find "$SHLIB_WORK" -name '*.so' | head -1)
        if [ -n "$SHLIB_SO" ]; then
            # dpkg-shlibdeps insists on a debian/control in the working dir.
            mkdir -p "$SHLIB_WORK/debian"
            printf 'Source: kalinka-server\n\nPackage: kalinka-server\nArchitecture: any\nDepends: ${shlibs:Depends}\n' > "$SHLIB_WORK/debian/control"
            SHLIBDEPS=$( cd "$SHLIB_WORK" && dpkg-shlibdeps -O --ignore-missing-info "$SHLIB_SO" 2>/dev/null | sed -n 's/^shlibs:Depends=//p' )
        fi
    fi
    rm -rf "$SHLIB_WORK"
fi
if [ -n "$SHLIBDEPS" ]; then
    echo "Library deps (dpkg-shlibdeps): $SHLIBDEPS"
else
    echo "dpkg-shlibdeps unavailable; using static library deps"
    SHLIBDEPS="$STATIC_LIBDEPS"
fi

sed "s/@ARCH@/$ARCH/; s/@VERSION@/$WHEEL_VERSION/; s/@PYTHON_VERSION@/$PYTHON_VERSION/g; s/@PYTHON_VERSION_UPPER@/$PYTHON_VERSION_UPPER/g" DEBIAN/control.in \
    | sed "s|@SHLIBDEPS@|$SHLIBDEPS|" > "$TARGET_DIR/DEBIAN/control"
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
cp scripts/kalinka-upgrade.path "$TARGET_DIR/etc/systemd/system/"
cp scripts/kalinka-upgrade.service "$TARGET_DIR/etc/systemd/system/"
# In-place upgrade support: the root-side kalinka-upgrade.service runs this
# copy of the repo's installer when the server requests an upgrade.
cp ../../scripts/install-release.sh "$TARGET_DIR/opt/kalinka/install-release.sh"
chmod 755 "$TARGET_DIR/opt/kalinka/install-release.sh"
cp scripts/kalinka.tmpfiles.conf "$TARGET_DIR/usr/lib/tmpfiles.d/kalinka.conf"
cp ../../README.md "$TARGET_DIR/opt/kalinka/"
cp LICENSE "$TARGET_DIR/opt/kalinka/"

dpkg-deb --root-owner-group --build "$TARGET_DIR"
mv "$TARGET_DIR.deb" "$TARGET_FILE"
rm -rf "$TARGET_DIR"

echo "Successfully built: $TARGET_FILE"