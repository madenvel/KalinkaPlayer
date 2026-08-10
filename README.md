# What is it?

Website: [kalinkaplayer.com](https://kalinkaplayer.com)

KalinkaPlayer is a lightweight, plugin-based music backend for Linux systems (including Raspberry Pi 4+). At its centre is **kalinka-server** — a small, pure-Python core that owns the REST + WebSocket API, the play queue, playback state and configuration. It is driven by the separate **Kalinka Music App** (a multi-platform Flutter client) or from any browser through the built-in web interface. Everything that provides music or talks to hardware is a **plugin**: music sources, metadata enrichers and device integrations plug into the server without touching its core. The audio itself is played by a **renderer** — a separate process that runs on the same machine or on a speaker-attached box elsewhere on the network.

Two source plugins ship with the server: **Local Library** (`kalinka-plugin-localfiles`) — the most powerful one, with a full indexing and enrichment pipeline for your own files — and **Jamendo** (`kalinka-plugin-jamendo`), free streaming from the Jamendo Creative-Commons catalog.

The target audience: DIY HiFi enthusiasts comfortable with Linux and the command line who want a controllable, efficient audio backend.

# System requirements

| Configuration | Minimum hardware | Notes |
|---|---|---|
| Playback + library (Smart Search off) | Raspberry Pi 3 / Zero 2 W, or any arm64/amd64 box with **512 MB RAM** | Headless (Lite) OS recommended at 512 MB; enable swap for the initial install and large library scans. 1 GB is comfortable. |
| With Local Library Smart Search (CLAP audio analysis) | Raspberry Pi 4B with **4 GB RAM**, or any amd64 machine with 4 GB+ | The embedding model (~285 MB) stays resident in RAM; the initial embedding pass is CPU-heavy and runs in the background — expect it to take a while on large libraries. Allow ~1 GB extra disk for the model. |

A **64-bit OS is required** — packages are built for arm64 and amd64 only. On Raspberry Pi that means Raspberry Pi OS (64-bit); the Pi 2, Pi 1 and original Pi Zero (32-bit-only CPUs) are not supported. Smart Search can be toggled per install, so you can start small and enable it after moving the library to a bigger board.

# Features

**Core server (`kalinka-server`)**
- REST + WebSocket API for playback control, library discovery, search and queue management
- Mixed-source queue: seamlessly queue tracks from different sources together
- Browsing, fuzzy text search, favorites, genres and server-side playlists
- Service discovery (zeroconf/SSDP) so the app finds the server automatically
- Configuration editable live from the app's Settings (with a "simple" tier of common fields and an expert/`about:config`-style search for everything else)
- Web interface: the server serves the optional Kalinka web player bundle, so any browser can control it — and play audio right in the browser (see [Playback](#playback))

**Local Library plugin (`kalinka-plugin-localfiles`) — the most complete source**
- Indexing of directory trees into an internal SQLite database
- Filesystem watching with an upload-quiescence window so partial/in-progress files aren't indexed
- Metadata enrichment pipeline: AcoustID fingerprinting (with a free API key) plus MusicBrainz, Wikidata and Deezer lookups, falling back to filename/path heuristics when external sources cannot resolve a track
- Artwork extraction & caching — with procedurally generated covers when none can be found — plus artist/album/track relationship modeling
- Smart Search over the audio itself (see [Smart Search](#smart-search))

**Jamendo plugin (`kalinka-plugin-jamendo`)**
- Free streaming from the Jamendo Creative-Commons catalog (needs a free Jamendo API client id)
- Search and catalog browsing: popular tracks, new releases, popular albums and artists, featured playlists
- Smart Search over textual track descriptions (see [Smart Search](#smart-search))

**Other**
- MusicCast device volume control and automatic power on/off (`kalinka-plugin-musiccast`)
- Low CPU & memory footprint; the performance-critical audio path lives in the C++ renderer with direct ALSA access
- Runs well on Raspberry Pi 4 (Raspberry Pi OS bullseye tested)

**Maintenance / Utilities**
- One-shot "rebuild library on next restart" trigger (purge index + artwork cache and rescan)
- Server restart from the app
- Structured logging with adjustable verbosity

# Smart Search

Both bundled source plugins can turn a natural-language request ("dreamy ambient guitar", "upbeat 80s synth pop") into music suggestions — surfaced in the app as **AI Search** — but they work differently:

- **Local Library** analyses the audio itself. An optional background **embedder** runs your tracks through a CLAP audio↔text model (ONNX), so a text query is matched against what the music actually *sounds* like — no tags required; results are KNN-retrieved and re-ranked against full-text and metadata signals. When enabled, this needs noticeably more resources: the models are downloaded on first run (~285 MB resident in RAM, ~1 GB on disk) and 4 GB RAM is recommended — see the system-requirements table. Ranking weights and re-embedding versioning are exposed through configuration; the model release/upgrade process is described in [`scripts/clap_onnx_release.md`](scripts/clap_onnx_release.md).
- **Jamendo** relies on textual descriptions of the tracks: your query is matched against a prebuilt index of caption embeddings (~140 MB, downloaded on first use). No audio analysis runs on your device, so it stays lightweight.

Smart Search is opt-in per plugin, so you can enable it only where you want it.

# Playback

Audio is played by a **renderer**, not by the server itself, so gapless and bit-perfect behaviour depend on the renderer in use.

The default renderer (`kalinka-renderer`, C++) targets Linux systems with ALSA and talks to the sound card directly. It plays FLAC & MP3 up to 192 kHz / 24-bit (the FLAC limit), supports gapless playback (consecutive tracks of the same format), and is **bit-perfect** where ALSA configuration permits\*: it does not alter the audio unless specifically instructed to — for example when software volume control is enabled.

In-browser playback through the web interface is also supported: open the server's address in a browser and play right there. It is a convenience path — no gapless playback at this point, and no bit-perfect guarantee, since audio goes through the browser's audio stack.

# Architecture

The core, the plugins and the renderer each live in their own package. A renderer discovers the server over mDNS and connects out to it, then fetches media over HTTP itself — audio never flows through the core. What a renderer must do to be one is written down in [`docs/native-renderer-design.md`](docs/native-renderer-design.md), so the bundled ALSA renderer is an implementation of that contract rather than the only possible one.

```
packages/
├── kalinka-server            # Core server, REST/WS API, queue, config (pure Python)
├── kalinka-renderer          # Network audio renderer: native ALSA player (C++)
├── kalinka-plugin-sdk        # Shared plugin interface & helpers (mandatory dependency)
├── kalinka-plugin-localfiles # Local Library: indexer, enricher, embedder, searcher (most complete)
├── kalinka-plugin-jamendo    # Jamendo streaming source (Creative-Commons catalog)
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
Python 3.11+ is required (production runs 3.13). The server packages are pure Python; only the renderer needs a C++ toolchain (`make renderer-deb`, see `packages/kalinka-renderer/scripts/build_deb.sh` for its dependencies).

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
   To also build and run the renderer locally (audio playback), see `make renderer-build` — that one needs the C++ toolchain (`g++ cmake protobuf-compiler libprotobuf-dev libboost-dev libcurlpp-dev libflac++-dev libasound2-dev libspdlog-dev`).
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
   Enabling an optional feature (Smart Search) in **Settings** and hitting **Restart** also just works: `dev-run` installs the requested optional packages into the venv before relaunching — the same flow `kalinka.service` runs at boot in production.
5. In the Kalinka Music App, go to **Settings → Connection**; the service should appear under the name you configured. Pick it and tap **Connect**.

# Configuration & tuning

- Most settings are editable live from the app's **Settings** screen and persisted to the `.cfg` files. The server exposes its config schema at `GET /server/config/schema` so the app can render forms.
- **Smart Search** is opt-in. For the Local Library, turn on **AI search** in the localfiles module config — one switch covers both indexing the library and answering queries. On first run it downloads the CLAP ONNX models to the model directory (default `/var/lib/kalinka/models`, or `$KALINKA_PREFIX/var/lib/kalinka/models` when running from source) and embeds tracks in the background; watch progress via `GET /indexer/status`.
- **AcoustID** enrichment needs a free API key from the [AcoustID website](https://acoustid.org/) — set it in the localfiles enricher config. The key is the only switch: no key means the plugin isn't loaded.
- Re-embedding is driven by `CLAP_MODEL_VERSION` in `embedding_utils.py`, not by config; bumping it in code forces a rebuild after a model change. See [`scripts/clap_onnx_release.md`](scripts/clap_onnx_release.md).

# Testing

```bash
make test
```
Runs the SDK and server Python test suites. Most packages also include their own `tests/` directory; the renderer has its own C++ test set under `packages/kalinka-renderer/tests` (built when GoogleTest is installed).

# Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to send a change, and for the project's disclosure on AI-assisted development.

# Notes

\* The audio engine uses ALSA directly and relies on its configuration. If automatic resampling is configured it will likely affect the path but should still work.

\* Developed and tested on a Raspberry Pi 4 with a HiFiBerry Digi2 card configured per its manual. It should work with any ALSA-compatible card, though some cards may need extra quirks.

# License

GPL-3.0-or-later. See the `license` field in each package's `pyproject.toml`.
