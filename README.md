# What is it?

Website: [kalinkaplayer.com](https://kalinkaplayer.com)

Kalinka is an experimental open-source music system.
"Kalinka" is a working project name and may be subject to trademark registration.

KalinkaPlayer is a lightweight backend service for music playback on Linux systems (including Raspberry Pi 4+). It exposes a REST + WebSocket API for playback control, library discovery, search and queue management, and is driven by the separate **Kalinka Music App** (a multi-platform Flutter client).

The current focus and most advanced functionality is **local file playback**. A flexible indexing & enrichment pipeline builds and maintains a rich local music library, augmenting track / album / artist metadata using external services (AcoustID fingerprinting, MusicBrainz, Deezer, Wikidata) with a fallback strategy that keeps the library usable even when some lookups fail. On top of that, an optional **AI search** layer embeds your audio with a CLAP model so you can find tracks by natural-language description ("dreamy ambient guitar", "upbeat 80s synth pop").

The target audience: DIY HiFi enthusiasts comfortable with Linux and the command line who want a controllable, efficient audio backend.

# Features

**Core (Local Library)**
- Local files playback — the primary, most advanced path
  - Indexing of directory trees into an internal SQLite database
  - Filesystem watching with an upload-quiescence window so partial/in-progress files aren't indexed
  - Metadata enrichment pipeline: AcoustID (fingerprints) → MusicBrainz → Deezer → Wikidata, with a filename/path-based fallback
  - Artwork extraction & caching, plus artist/album/track relationship modeling
- Full FLAC & MP3 playback up to 192 kHz / 24-bit (FLAC limit) with a bit-perfect path where ALSA config permits\*
- Gapless playback (consecutive tracks of the same format)
- Mixed-source queue: seamlessly queue tracks from different sources together
- Browsing, fuzzy text search, favorites, genres and server-side playlists

**AI Search (optional)**
- Natural-language / semantic search over your library via a CLAP audio↔text embedding model (ONNX, downloaded on first boot)
- Background **embedder** computes per-track CLAP vectors; results are KNN-retrieved and re-ranked against full-text + metadata signals
- Optional tag prediction (genre / mood / danceability) via essentia-tensorflow for richer ranking — opt-in, since it pulls large ARM-only wheels
- Tunable ranking weights and re-embedding versioning exposed through configuration
- See [`scripts/clap_onnx_release.md`](scripts/clap_onnx_release.md) for the model release/upgrade process

**Other**
- MusicCast device volume control and automatic power on/off
- Low CPU & memory footprint; performance-critical audio engine in C++ with direct ALSA access
- Runs well on Raspberry Pi 4 (Raspberry Pi OS bullseye tested)
- Service discovery (zeroconf/SSDP) so the app finds the server automatically
- Configuration is editable live from the app's Settings (with a "simple" tier of common fields and an expert/`about:config`-style search for everything else)

**Maintenance / Utilities**
- One-shot "rebuild library on next restart" trigger (purge index + artwork cache and rescan)
- Server restart from the app
- Structured logging with adjustable verbosity

# Architecture

KalinkaPlayer is a small **core server** (`packages/kalinka-server`) with a modular **plugin** system. The server owns the REST/WebSocket API, queue, playback state and config; plugins provide sources, enrichers and device integrations. The native audio engine is a C++ extension (`packages/kalinka-server/src/native_player`) that talks to ALSA directly.

```
packages/
├── kalinka-server            # Core server, REST/WS API, queue, native ALSA player (C++)
├── kalinka-plugin-sdk        # Shared plugin interface & helpers (mandatory dependency)
├── kalinka-plugin-localfiles # Local library: indexer, enricher, embedder, searcher (most complete)
├── kalinka-plugin-musiccast  # Yamaha MusicCast volume/power control
└── kalinka-plugin-dummydevice# Mock playback device for development/CI
```

Plugins are ordinary Python packages discovered at runtime via package metadata. You can add or remove functionality without touching the server core. To create your own, start from the cookiecutter template under [`template/cookiecutter-kalinka-plugin/`](template/cookiecutter-kalinka-plugin/) and read its README. Plugin Debian packaging conventions are documented in [`docs/plugin-deb-packaging.md`](docs/plugin-deb-packaging.md).

# API overview

The server exposes a REST API (FastAPI) plus WebSocket channels for live state. Highlights:

| Area      | Endpoints (examples) |
|-----------|----------------------|
| Queue     | `GET /queue/list`, `POST /queue/add`, `PUT /queue/{play,pause,next,prev,stop}`, `PUT /queue/current_track/seek`, `PUT /queue/{mode,clear,move}`, `POST /queue/remove` |
| Browse    | `GET /browse`, `GET /browse/{id}`, `GET /get/{entity_id}`, `GET /genre/list` |
| Search    | `GET /search/{search_type}/{query}` (fuzzy), `GET /ai_search?query=...` (semantic) |
| Library   | `GET /favorite/list/{type}`, `PUT /favorite/add/{id}`, playlists (`/playlist/{create,update,delete,list,add_tracks,remove_tracks}`) |
| Devices   | `GET /device/list`, `GET/PUT /device/{get,set}_volume` |
| Server    | `GET /server/{config,config/schema,version,modules,optional_packages}`, `PUT /server/{config,restart}`, `GET /indexer/status`, `GET /resource/{file}` |
| Live      | `WS /queue/ws`, `WS /device/ws`, plus SSE-style `GET /queue/events`, `GET /device/events` |

# Installation

## Debian Package
A deb package for arm64 (Raspbian) is provided in the [Releases](https://github.com/madenvel/KalinkaPlayer/releases) section.

### Building Debian packages
The build produces **separate** `.deb` packages — one for the server and one per plugin — and collects them in the top-level `debs/` directory. Packages are built natively for the platform you build on (no cross-compilation).

#### Prerequisites
Install the required system dependencies:
```bash
sudo apt install python3 g++ libasound2-dev libflac-dev libflac++-dev \
  libcurlpp-dev libspdlog-dev libfmt-dev python3-dev python3-venv python3-pip build-essential
```
Python 3.10+ is required (3.11 if you intend to use the optional tag-prediction wheels).

#### Build process
1. Clone the repository:
```bash
git clone https://github.com/madenvel/KalinkaPlayer.git
cd KalinkaPlayer
```
2. Set up a Python virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
3. Build everything (server + all plugins) and move the artifacts into `debs/`:
```bash
make build-all-deb
```
You can also build pieces individually: `make kalinka-server-deb`, `make kalinka-plugins-deb`, or `make build-native`. Run `make help` to list all targets. The app bundle — the server and the first-party plugins — shares one version, derived from a single `kalinka-vX.Y.Z` git tag via setuptools-scm (one tag per release). The plugin SDK is versioned independently by its own SemVer (currently `1.0.0`); plugins pin it `>=1,<2`, so backwards-compatible minor/patch SDK bumps don't break them — only a major bump is breaking. See [RELEASING.md](RELEASING.md) for the full release and version-bump procedure.

#### Cleaning build artifacts
```bash
make clean
```
This removes compiled objects, shared libraries and Python build artifacts.

#### Installation
Install the server first, then the plugins you want:
```bash
sudo dpkg -i debs/kalinka-server_*.deb
sudo dpkg -i debs/kalinka-plugin-*.deb
sudo apt-get install -f   # install any missing dependencies
```
At startup `kalinka.service` runs `/opt/kalinka/bootstrap.sh`, which creates `/opt/kalinka/venv` and pip-installs every wheel found under `/opt/kalinka/wheels/`. Plugins ship their wheel there and trigger a server restart, so they're picked up automatically (see [`docs/plugin-deb-packaging.md`](docs/plugin-deb-packaging.md)).

#### Service management
- Restart: `sudo systemctl restart kalinka.service`
- Check status: `systemctl status kalinka.service`
- View logs: `journalctl -u kalinka.service`

# Running from source (development)

Use a virtual environment with editable installs so code changes are picked up immediately.

1. Clone and enter the repo, then create/activate a virtualenv:
```bash
git clone https://github.com/madenvel/KalinkaPlayer.git
cd KalinkaPlayer
python3 -m venv .venv
source .venv/bin/activate
```
2. Install system prerequisites (same list as above):
```bash
sudo apt install python3 g++ libasound2-dev libflac-dev libflac++-dev \
  libcurlpp-dev libspdlog-dev libfmt-dev python3-dev
```
3. Set up the dev environment in one step — this installs the SDK and server editable and builds the native player:
```bash
make setup-dev
```
   (Equivalent to running [`./setup_dev_env.sh`](setup_dev_env.sh).) To also develop a plugin, install it editable too:
```bash
cd packages/kalinka-plugin-localfiles && pip install -e . && cd -
```
   Or install the server and all bundled plugins editable at once:
```bash
cd packages/kalinka-server && pip install -e . && cd -
for p in packages/kalinka-plugin-*; do (cd "$p" && pip install -e . || true); done
```
4. Run the server (foreground):
```bash
kalinka-server --config kalinka_conf.cfg
```
   …or via the Makefile helper: `make run-server`. Per-plugin config files (e.g. `localfiles_config.cfg`) are created alongside the main config. Example config files live in the repo root.
5. In the Kalinka Music App, go to **Settings → Connection**; the service should appear under the name you configured. Pick it and tap **Connect**.

# Configuration & tuning

- Most settings are editable live from the app's **Settings** screen and persisted to the `.cfg` files. The server exposes its config schema at `GET /server/config/schema` so the app can render forms.
- **AI search** is opt-in. Enable the **embedder** (and optionally tag prediction under the searcher) in the localfiles module config. On first run the embedder downloads the CLAP ONNX models to the model directory (default `/var/lib/kalinka/models`) and embeds tracks in the background; watch progress via `GET /indexer/status`.
- **AcoustID** enrichment needs a free API key from the [AcoustID website](https://acoustid.org/) — set it in the localfiles enricher config.
- Re-embedding / re-tagging is driven by `current_version` fields in the config; bump them to force a rebuild after a model change. See [`scripts/clap_onnx_release.md`](scripts/clap_onnx_release.md).

# Testing

```bash
make test
```
Runs the SDK and server Python test suites. Most packages also include their own `tests/` directory; the native player has its own C++ test set under `packages/kalinka-server/src/native_player`.

# Contributing

1. Fork the repository and create a feature branch.
2. Make changes with editable installs active so they take effect immediately.
3. Run `make test` (and any package-specific tests for the area you touched).
4. Open a pull request describing the change and which plugin or server area it affects.

# Notes

\* The audio engine uses ALSA directly and relies on its configuration. If automatic resampling is configured it will likely affect the path but should still work.

\* Developed and tested on a Raspberry Pi 4 with a HiFiBerry Digi2 card configured per its manual. It should work with any ALSA-compatible card, though some cards may need extra quirks.

# License

GPL-3.0-or-later. See the `license` field in each package's `pyproject.toml`.
