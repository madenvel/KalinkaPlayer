#!/bin/bash
# Regenerate the committed speaker-test tones in src/kalinka_server/assets/tones.
#
# 440 Hz at -12 dBFS, the amplitude the renderer's own tone:// generator uses —
# ffmpeg's sine starts at -18 dBFS, hence volume=2. 3 s is a whole number of
# cycles, so both ends land on a zero crossing and need no fade. -bitexact keeps
# the bytes reproducible.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/../src/kalinka_server/assets/tones"

if ! command -v ffmpeg > /dev/null; then
    echo "Error: ffmpeg is required." >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

generate() {
    local channel="$1" pan="$2"
    ffmpeg -hide_banner -loglevel error -y \
        -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=3" \
        -af "volume=2,pan=stereo|$pan" \
        -sample_fmt s16 -c:a flac \
        -map_metadata -1 -fflags +bitexact \
        "$OUT_DIR/$channel.flac"
    echo "Wrote $OUT_DIR/$channel.flac"
}

generate left "c0=c0|c1=0*c0"
generate right "c0=0*c0|c1=c0"
generate both "c0=c0|c1=c0"
