#!/usr/bin/env bash
#
# install-release.sh — install a published Kalinka backend release from GitHub.
#
# Downloads the .deb assets for a `kalinka-v*` GitHub release and installs them
# with apt so system dependencies (libasound, libcurl, FLAC, …) are pulled in
# automatically. It picks the server package matching THIS machine's
# architecture (arm64 on a Raspberry Pi, amd64 on a PC) and installs all the
# arch-independent plugin + SDK packages alongside it.
#
# Usage:
#   ./install-release.sh                 # install the latest kalinka-v* release
#   ./install-release.sh 3.2.0           # install a specific version
#   ./install-release.sh kalinka-v3.2.0  # same, full tag form
#
# Env:
#   KALINKA_REPO   owner/repo to pull releases from (default: madenvel/KalinkaPlayer)
#   GITHUB_TOKEN   optional, only to avoid the 60-req/hr anonymous API limit
#   NO_APT_UPDATE  set to 1 to skip `apt-get update` before installing
#
# The download runs as your user; only the install step uses sudo. Running the
# whole script under sudo is fine too.
set -euo pipefail

REPO="${KALINKA_REPO:-madenvel/KalinkaPlayer}"
API="https://api.github.com/repos/${REPO}/releases"
REQUEST="${1:-}"   # optional version or tag

have() { command -v "$1" >/dev/null 2>&1; }

die() { echo "error: $*" >&2; exit 1; }

# --- pick a privilege escalator for the install step --------------------------
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if have sudo; then SUDO="sudo"; else
    die "not root and 'sudo' not found — re-run as root or install sudo"
  fi
fi

# --- detect target architecture ----------------------------------------------
if have dpkg; then
  ARCH="$(dpkg --print-architecture)"
else
  case "$(uname -m)" in
    aarch64|arm64) ARCH=arm64 ;;
    x86_64|amd64)  ARCH=amd64 ;;
    *) die "unsupported architecture: $(uname -m) (need arm64 or amd64)" ;;
  esac
fi
[ "$ARCH" = arm64 ] || [ "$ARCH" = amd64 ] || die "unsupported architecture: $ARCH"

have python3 || die "python3 is required (it is also a runtime dependency of the server)"

# --- fetch helpers (curl or wget) ---------------------------------------------
AUTH=()
[ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer ${GITHUB_TOKEN}")

fetch() {  # fetch <url> -> stdout
  local url="$1"
  if have curl; then curl -fsSL "${AUTH[@]}" "$url"
  elif have wget; then wget -qO- "$url"
  else die "need 'curl' or 'wget' to download"; fi
}

download() {  # download <url> <dest>
  local url="$1" dest="$2"
  if have curl; then curl -fSL "${AUTH[@]}" -o "$dest" "$url"
  else wget -O "$dest" "$url"; fi
}

# --- resolve the release ------------------------------------------------------
if [ -n "$REQUEST" ]; then
  case "$REQUEST" in
    kalinka-v*) TAG="$REQUEST" ;;
    v*)         TAG="kalinka-$REQUEST" ;;
    *)          TAG="kalinka-v$REQUEST" ;;
  esac
  echo ">> Looking up release $TAG in $REPO ..."
  json="$(fetch "$API/tags/$TAG")" || die "release '$TAG' not found in $REPO"
else
  echo ">> Looking up the latest kalinka-v* release in $REPO ..."
  json="$(fetch "$API?per_page=30")" || die "could not query releases for $REPO"
fi

# Parse the release JSON: print the tag on line 1, then one asset URL per line
# for the server deb matching $ARCH plus every arch-independent (_all) deb.
# The parser is written to a file (not piped via `python3 -`) so the JSON can be
# fed on stdin without colliding with the program source.
TMPDIR_DL=""
PARSER="$(mktemp --suffix=.py)"
cleanup() { rm -rf "$PARSER" ${TMPDIR_DL:+"$TMPDIR_DL"}; }
trap cleanup EXIT
cat > "$PARSER" <<'PY'
import sys, json
arch = sys.argv[1]
data = json.load(sys.stdin)
rel = None
if isinstance(data, list):
    for r in data:                     # newest first; skip drafts/prereleases
        if r.get("draft") or r.get("prerelease"):
            continue
        if str(r.get("tag_name", "")).startswith("kalinka-v"):
            rel = r
            break
elif isinstance(data, dict) and data.get("assets") is not None:
    rel = data
if not rel:
    sys.stderr.write("no matching kalinka-v* release found\n")
    sys.exit(1)
server_suffix = ".%s.deb" % arch
urls = [a["browser_download_url"] for a in rel.get("assets", [])
        if a["name"].endswith("_all.deb") or a["name"].endswith(server_suffix)]
if not any(u.endswith(server_suffix) for u in urls):
    sys.stderr.write("release %s has no server package for arch '%s'\n"
                     % (rel.get("tag_name"), arch))
    sys.exit(1)
print(rel["tag_name"])
for u in urls:
    print(u)
PY
parsed="$(printf '%s' "$json" | python3 "$PARSER" "$ARCH")" \
  || die "failed to parse release metadata"

TAG="$(printf '%s\n' "$parsed" | sed -n '1p')"
mapfile -t URLS < <(printf '%s\n' "$parsed" | sed '1d')
[ "${#URLS[@]}" -gt 0 ] || die "no installable .deb assets found for $TAG ($ARCH)"

echo ">> Release: $TAG   architecture: $ARCH   packages: ${#URLS[@]}"

# --- download into a temp dir -------------------------------------------------
TMPDIR_DL="$(mktemp -d)"

for url in "${URLS[@]}"; do
  name="${url##*/}"
  echo "   downloading $name"
  download "$url" "$TMPDIR_DL/$name"
done

# --- install ------------------------------------------------------------------
if [ "${NO_APT_UPDATE:-0}" != "1" ]; then
  echo ">> apt-get update"
  $SUDO apt-get update
fi

echo ">> Installing ${#URLS[@]} package(s) with apt ..."
# apt resolves install order among the bundle packages (server depends on the
# SDK) and pulls system dependencies from the configured repos. A leading ./ or
# absolute path tells apt these are local files, not repo package names.
if ! $SUDO apt-get install -y "$TMPDIR_DL"/*.deb; then
  echo ">> apt-get install failed; falling back to dpkg -i + apt-get -f install"
  $SUDO dpkg -i "$TMPDIR_DL"/*.deb || true
  $SUDO apt-get -f install -y
fi

# --- report -------------------------------------------------------------------
echo
echo ">> Installed:"
if have dpkg-query; then
  for pkg in kalinka-server kalinka-plugin-sdk kalinka-plugin-localfiles \
             kalinka-plugin-musiccast kalinka-plugin-jamendo \
             kalinka-plugin-dummydevice; do
    dpkg-query -W -f='   %-32n %v\n' "$pkg" 2>/dev/null || true
  done
fi

echo
echo ">> Done. $TAG installed for $ARCH."
