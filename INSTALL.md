# Kalinka Player Installation Guide

## Development Installation

### Prerequisites
- Python 3.11 (the dev venv is pinned to 3.11 to match production)
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

2. Install the system build prerequisites (including Python 3.11):
```bash
sudo apt install python3.11 python3.11-venv python3.11-dev g++ libasound2-dev \
  libflac-dev libflac++-dev libcurlpp-dev libspdlog-dev libfmt-dev build-essential
```

3. One-step setup — creates a venv at `.venv` with Python 3.11 (or reuses an already-active `$VIRTUAL_ENV`), installs the SDK, server and all plugins editable, builds the native player, and seeds the fakeroot plus a default config. It refuses to run against a non-3.11 interpreter; override the path with `make dev-setup PYTHON=/path/to/python3.11` if needed:
```bash
make dev-setup
```

4. Run the server in the foreground (Ctrl-C to stop; logs tee'd to `~/kalinka/var/log/kalinka/server.log`):
```bash
make dev-run            # add ARGS=--debug for verbose logging
```

After editing C++ under `packages/kalinka-server/src/native_player`, run `make dev-rebuild-native` and restart. See the **Running from source (development)** section of [README.md](README.md) for the full workflow — in-app restart without systemd and `KALINKA_PREFIX` relocation.

## Production Installation

Production runs as the `kalusr` system user under systemd, installed from
per-platform `.deb` packages. See the **Installation** section of
[README.md](README.md) for the full walkthrough.

1. Build the packages (server + all plugins); artifacts land in `debs/`:
```bash
make build-all-deb
```

2. Install the server first, then the plugins you want:
```bash
sudo dpkg -i debs/kalinka-server_*.deb
sudo dpkg -i debs/kalinka-plugin-*.deb
sudo apt-get install -f   # pull in any missing dependencies
```
On first start `kalinka.service` runs `/opt/kalinka/bootstrap.sh`, which creates
the runtime venv and installs the shipped wheels.

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
