# Kalinka Player Installation Guide

**Installing Kalinka to use it?** One command does it, and the walkthrough that follows — first run, adding music, troubleshooting — lives in [README.md](README.md#installation):

```bash
curl -fsSL https://kalinkaplayer.com/install.sh | sudo bash
```

This file covers the two paths that command doesn't: running from a source checkout, and building the packages yourself.

## Development Installation

### Prerequisites
- Python 3.11 or newer (production runs 3.13)
- Git
- Build tools (gcc, make, etc.)
- ALSA development libraries

### Development Setup

From a source checkout you can run the server without root or systemd — it uses a per-user "fakeroot" under `$KALINKA_PREFIX` (default `~/kalinka`) for its config, state, logs and music folder.

1. Clone the repository:
```bash
git clone https://github.com/madenvel/KalinkaPlayer.git
cd KalinkaPlayer
```

2. Install the system build prerequisites (Python, plus the C++ toolchain for the renderer):
```bash
sudo apt install python3 python3-venv python3-dev g++ cmake protobuf-compiler \
  libprotobuf-dev libboost-dev libasound2-dev libflac-dev libflac++-dev \
  libcurlpp-dev libspdlog-dev libfmt-dev pkg-config build-essential
```

3. One-step setup — creates a venv at `.venv` (or reuses an already-active `$VIRTUAL_ENV`), installs the SDK, server and all plugins editable, and seeds the fakeroot plus a default config. It refuses anything older than Python 3.11; point it at a specific interpreter with `make dev-setup PYTHON=/path/to/python3.13`:
```bash
make dev-setup
```

4. Run the server in the foreground (Ctrl-C to stop; logs tee'd to `~/kalinka/var/log/kalinka/server.log`):
```bash
make dev-run            # add ARGS=--debug for verbose logging
```

Audio plays in the renderer, a separate C++ program in `packages/kalinka-renderer`: build it with `make renderer-build` and run `packages/kalinka-renderer/build/kalinka-renderer` alongside the server — it discovers the dev server over mDNS like any other. After editing its sources rebuild and restart that binary; the server itself is pure Python and only needs a restart. See the **Running from source (development)** section of [README.md](README.md) for the full workflow — in-app restart without systemd and `KALINKA_PREFIX` relocation.

## Installing packages you built yourself

The server runs as the `kalusr` system user under systemd; the renderer runs as `kalrndr`, the only one of the two in the `audio` group.

1. Build the app bundle (server + plugins); artifacts land in `debs/`:
```bash
make build-all-deb
```

2. Install the server first, then the plugins you want:
```bash
sudo dpkg -i debs/kalinka-server_*.deb
sudo dpkg -i debs/kalinka-plugin-*.deb
sudo apt install -f   # pull in any missing dependencies
```
On first start `kalinka.service` runs `/opt/kalinka/bootstrap.sh`, which creates
the runtime venv and installs the shipped wheels.

3. **Install a renderer, or the machine stays silent.** It is not part of the bundle above — it builds per-platform and releases on its own train:
```bash
make renderer-deb                                   # needs the C++ toolchain
sudo apt install ./packages/kalinka-renderer/kalinka-renderer-*.deb
```
Or take a published one instead of building it: `./scripts/install-renderer.sh`. Either way the browser player works without it, since the browser is its own renderer.

## Version Management

The app bundle (server + first-party plugins) shares a single version, determined automatically from one git tag. To create a release, tag once:
```bash
git tag kalinka-v1.2.3
git push origin kalinka-v1.2.3
```

The plugin SDK is versioned independently by its own SemVer (single source of truth: `__version__` in `packages/kalinka-plugin-sdk/src/kalinka_plugin_sdk/_version.py`, read by `pyproject.toml` via `[tool.setuptools.dynamic]`); it has no git tag. Plugins pin it `kalinka-plugin-sdk>=1,<2`, so backwards-compatible minor/patch bumps don't break them; a major bump is breaking and requires widening the consumers' `<2` bounds.

The resulting version is used automatically in builds and service discovery. See [RELEASING.md](RELEASING.md) for the full release checklist and how to bump the SDK version.

## Configuration

The server reads `kalinka_conf.cfg` (JSON) — `/etc/kalinka/kalinka_conf.cfg` in
production, or `$KALINKA_PREFIX/etc/kalinka/kalinka_conf.cfg` when running from
source. Most settings are edited live from the app's **Settings** screen and
persisted automatically; per-plugin settings live in the same file. Example
config files live in the repo root (`kalinka_conf.cfg`, `localfiles_config.cfg`,
…).

## Running the Service

### Development
```bash
make dev-run ARGS=--debug
```

### Production (systemd service)
```bash
sudo systemctl restart kalinka.service
sudo systemctl enable kalinka.service   # start on boot
journalctl -u kalinka.service -f        # follow logs
```
