# shellcheck shell=bash
#
# The version the renderer packages carry. Sourced by build_deb.sh and
# build_rpm.sh; run from the package directory.
#
# RENDERER_VERSION wins — the release workflow passes the tag it is building.
# Otherwise it comes from git, so that a package built from newer commits
# installs over the one before it, and the next release installs over that:
#
#   0.1.0                        at kalinka-renderer-v0.1.0 with a clean tree
#   0.1.1~dev7+g1a2b3c4          7 commits later — '~' sorts below the 0.1.1
#                                it leads up to, the count above earlier builds
#   0.1.1~dev7+g1a2b3c4.d260807  built from a tree with uncommitted changes
#
# Before the first tag there is no release to bump past, so the version
# declared in CMakeLists.txt is what the commits lead up to.

TAG_PREFIX="kalinka-renderer-v"

bump_patch() {
    local version=$1
    printf '%s%s' "${version%.*}." "$(( ${version##*.} + 1 ))"
}

renderer_version() {
    if [ -n "${RENDERER_VERSION:-}" ]; then
        printf '%s\n' "$RENDERER_VERSION"
        return
    fi

    local declared tag base count dirty
    declared=$(sed -n 's/^project(kalinka-renderer VERSION \([0-9.]*\).*/\1/p' CMakeLists.txt)

    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        printf '%s\n' "$declared"
        return
    fi

    tag=$(git describe --tags --abbrev=0 --match "$TAG_PREFIX*" 2> /dev/null || true)
    if [ -n "$tag" ]; then
        base=${tag#"$TAG_PREFIX"}
        count=$(git rev-list --count "$tag..HEAD")
    else
        base=$declared
        count=$(git rev-list --count HEAD)
    fi

    dirty=""
    if [ -n "$(git status --porcelain)" ]; then
        dirty=".d$(date +%y%m%d)"
    fi

    if [ "$count" -eq 0 ] && [ -z "$dirty" ]; then
        printf '%s\n' "$base"
        return
    fi
    if [ -n "$tag" ]; then
        base=$(bump_patch "$base")
    fi

    printf '%s~dev%s+g%s%s\n' \
        "$base" "$count" "$(git rev-parse --short HEAD)" "$dirty"
}
