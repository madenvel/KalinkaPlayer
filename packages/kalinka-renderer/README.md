# kalinka-renderer

Standalone native renderer for Kalinka Core (MVP: discovery + Hello/Welcome
registration only — no audio yet). Design: `docs/native-renderer-design.md`.

## Build

Dependencies (dev headers): protobuf (+ compiler), Boost (headers), spdlog.
mDNS discovery is self-contained (vendored public-domain `third_party/mdns`),
so no avahi/Bonjour daemon or headers are needed.

- Fedora: `sudo dnf install cmake protobuf-devel protobuf-compiler protobuf-lite-devel boost-devel spdlog-devel`
- Debian/Ubuntu: `sudo apt install cmake libprotobuf-dev protobuf-compiler libboost-dev libspdlog-dev`

From the repo root:

```sh
make renderer-build
# or directly:
cmake -S packages/kalinka-renderer -B packages/kalinka-renderer/build
cmake --build packages/kalinka-renderer/build -j
```

## Run

```sh
# Discover Cores via mDNS and register with each:
./build/kalinka-renderer --name "Living Room"

# Fixed endpoint (skips discovery), e.g. against a dev server:
./build/kalinka-renderer --server 127.0.0.1:8000

# Background daemon (logs to $KALINKA_PREFIX/var/log/kalinka/renderer.log):
KALINKA_PREFIX=$HOME/kalinka ./build/kalinka-renderer --daemon
```

Stop with SIGINT/SIGTERM — the renderer sends a protocol Goodbye and closes
cleanly. The stable renderer id persists at
`$KALINKA_PREFIX/var/lib/kalinka-renderer/renderer_id`.
