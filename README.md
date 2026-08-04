# What is it?

Website: [kalinkaplayer.com](https://kalinkaplayer.com)

KalinkaPlayer is a lightweight backend service for music playback on Linux systems (including Raspberry Pi 4+). It exposes a REST + WebSocket API for playback control, library discovery, search and queue management, and is driven by the separate **Kalinka Music App** (a multi-platform Flutter client).

The current focus and most advanced functionality is **local file playback**. A flexible indexing & enrichment pipeline builds and maintains a rich local music library by first reading embedded tags and local file metadata such as filename, path, and technical parameters. If an AcoustID API key is configured, it computes a fingerprint to identify the recording, then fills missing metadata through MusicBrainz, Wikidata, and Deezer, with filesystem heuristics as the last resort when external sources cannot resolve a track. On top of that, an optional **AI search** layer embeds your audio with a CLAP model so you can find tracks by natural-language description ("dreamy ambient guitar", "upbeat 80s synth pop").

The target audience: DIY HiFi enthusiasts comfortable with Linux and the command line who want a controllable, efficient audio backend.

# System requirements

| Configuration | Minimum hardware | Notes |
|---|---|---|
| Playback + library (AI search off) | Raspberry Pi 3 / Zero 2 W, or any arm64/amd64 box with **512 MB RAM** | Headless (Lite) OS recommended at 512 MB; enable swap for the initial install and large library scans. 1 GB is comfortable. |
| With AI search (CLAP) | Raspberry Pi 4B with **4 GB RAM**, or any amd64 machine with 4 GB+ | The embedding model (~285 MB) stays resident in RAM; the initial embedding pass is CPU-heavy and runs in the background — expect it to take a while on large libraries. Allow ~1 GB extra disk for the model. |

A **64-bit OS is required** — packages are built for arm64 and amd64 only.
On Raspberry Pi that means Raspberry Pi OS (64-bit); the Pi 2, Pi 1 and
original Pi Zero (32-bit-only CPUs) are not supported. AI search can be
toggled per install, so you can start small and enable it after moving the
library to a bigger board.

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

KalinkaPlayer is a small **core server** (`packages/kalinka-server`, pure Python) with a modular **plugin** system, plus a network **renderer** (`packages/kalinka-renderer`, C++) that plays the audio. The server owns the REST/WebSocket API, queue, playback state and config; plugins provide sources, enrichers and device integrations; the renderer runs on a speaker-attached box (or the same machine), discovers the server over mDNS and talks to ALSA directly.

```
packages/
├── kalinka-server            # Core server, REST/WS API, queue, config (pure Python)
├── kalinka-renderer          # Network audio renderer: native ALSA player (C++)
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
Deb packages are provided in the [Releases](https://github.com/madenvel/KalinkaPlayer/releases) section. The whole app bundle (server, plugins, SDK) is pure Python and arch-independent (`_all.deb`); only the renderer ships per-arch builds, from its own `kalinka-renderer-v*` releases.

### Building Debian packages
The build produces **separate** `.deb` packages — one for the server and one per plugin — and collects them in the top-level `debs/` directory. All of them are `Architecture: all` and install on any machine.

#### Prerequisites
Install the required system dependencies:
```bash
sudo apt install python3 python3-venv python3-pip make git dpkg-dev
```
Python 3.11+ is required (production runs 3.13). The server packages are pure
Python; only the renderer needs a C++ toolchain (`make renderer-deb`, see
`packages/kalinka-renderer/scripts/build_deb.sh` for its dependencies).

#### Build process
Clone the repository and build — there's no virtualenv to set up by hand, the build provisions its own:
```bash
git clone https://github.com/madenvel/KalinkaPlayer.git
cd KalinkaPlayer
make build-all-deb
```
On first run `make build-all-deb` creates a `.venv` with the wheel-build toolchain (or reuses an already-active `$VIRTUAL_ENV`), builds the server and every plugin, and moves the artifacts into `debs/`. To build against a specific interpreter, pass it explicitly: `make build-all-deb PYTHON=/path/to/python3.13`.

You can also build pieces individually: `make kalinka-server-deb`, `make kalinka-plugins-deb`, or `make renderer-deb`; `make build-env` just provisions the venv without building anything. Run `make help` to list all targets.

The app bundle — the server and the first-party plugins — shares one version, derived from a single `kalinka-vX.Y.Z` git tag via setuptools-scm (one tag per release). The plugin SDK is versioned independently by its own SemVer; plugins pin it `>=1,<2`, so backwards-compatible minor/patch SDK bumps don't break them — only a major bump is breaking. See [RELEASING.md](RELEASING.md) for the full release and version-bump procedure.

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

`make dev-setup` + `make dev-run` get you a running server from a source checkout — no root, no systemd, and nothing written to the system's `/etc` or `/var`. Everything lives in a per-user "fakeroot" under `$KALINKA_PREFIX` (default `~/kalinka`), and the editable installs mean Python edits are picked up on the next restart.

The dev venv needs **Python 3.11+** (production runs 3.13). `make dev-setup` creates the venv with `python3` and refuses to proceed against anything older than 3.11.

1. Clone the repo and install the system prerequisites (including Python 3.11+):
```bash
git clone https://github.com/madenvel/KalinkaPlayer.git
cd KalinkaPlayer
sudo apt install python3 python3-venv
```
   To use a specific interpreter, pass it explicitly: `make dev-setup PYTHON=/path/to/python3.13`.
   To also build and run the renderer locally (audio playback), see
   `make renderer-build` — that one needs the C++ toolchain
   (`g++ cmake protobuf-compiler libprotobuf-dev libboost-dev libcurlpp-dev libflac++-dev libasound2-dev libspdlog-dev`).
2. One-step setup. Creates a virtualenv at `.venv` with `python3` (or **reuses an already-active `$VIRTUAL_ENV`** — it never makes a second venv), installs the SDK, server and all bundled plugins editable, and seeds the fakeroot directory tree plus a default config:
```bash
make dev-setup
```
3. Run the server in the foreground (Ctrl-C to stop):
```bash
make dev-run
```
   It prints where everything lives and tees output to a log file, so you have logs to grep instead of only stdout:
   - config:        `~/kalinka/etc/kalinka/kalinka_conf.cfg`
   - state & DB:     `~/kalinka/var/lib/kalinka/`
   - logs:          `~/kalinka/var/log/kalinka/server.log`
   - music drop-off: `~/kalinka/srv/kalinka/music`

   Forward server flags with `ARGS` (e.g. `make dev-run ARGS=--debug`), and relocate the whole tree with `make dev-run KALINKA_PREFIX=/path/to/root`.
4. **Restart to pick up changes.** Python edits go live on restart — either click **Restart** in the app (this works without systemd: `dev-run` watches the restart trigger in the fakeroot and relaunches) or Ctrl-C and re-run `make dev-run`. After editing renderer C++, rebuild with `make renderer-build` and restart the renderer binary.
   Enabling an optional feature (AI search) in **Settings** and hitting **Restart** also just works: `dev-run` installs the requested optional packages into the venv before relaunching — the same flow `kalinka.service` runs at boot in production.
5. In the Kalinka Music App, go to **Settings → Connection**; the service should appear under the name you configured. Pick it and tap **Connect**.

# Configuration & tuning

- Most settings are editable live from the app's **Settings** screen and persisted to the `.cfg` files. The server exposes its config schema at `GET /server/config/schema` so the app can render forms.
- **AI search** is opt-in. Enable the **embedder** in the localfiles module config. On first run the embedder downloads the CLAP ONNX models to the model directory (default `/var/lib/kalinka/models`, or `$KALINKA_PREFIX/var/lib/kalinka/models` when running from source) and embeds tracks in the background; watch progress via `GET /indexer/status`.
- **AcoustID** enrichment needs a free API key from the [AcoustID website](https://acoustid.org/) — set it in the localfiles enricher config.
- Re-embedding / re-tagging is driven by `current_version` fields in the config; bump them to force a rebuild after a model change. See [`scripts/clap_onnx_release.md`](scripts/clap_onnx_release.md).

# Testing

```bash
make test
```
Runs the SDK and server Python test suites. Most packages also include their own `tests/` directory; the renderer has its own C++ test set under `packages/kalinka-renderer/tests` (built when GoogleTest is installed).

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
