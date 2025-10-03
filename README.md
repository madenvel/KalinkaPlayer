# What is it?
KalinkaPlayer is a lightweight backend service for music playback on Linux systems (including Raspberry Pi 4+) exposing a REST API for control, library discovery and queue management.

The current focus and most advanced functionality is LOCAL FILE PLAYBACK. A flexible indexing & enrichment pipeline builds and maintains a rich local music library. Track / album / artist metadata can be augmented using external services (AcoustID fingerprinting, MusicBrainz, Wikidata and others) with a fallback strategy to keep the library usable even when some lookups fail.

The target audience: DIY HiFi enthusiasts comfortable with Linux and the command line who want a controllable, efficient audio backend.

# Features
Core (Local Library):
- Local files playback (primary, most advanced path)
  - Indexing of directory trees into an internal database
  - Metadata enrichment via AcoustID (fingerprints) -> MusicBrainz -> Wikidata (+ pluggable enrichers)
  - Fallback / defensive enrichment: partial metadata retained even when external services fail
  - Artwork & basic entity relationship modeling (artists, albums, tracks)
- Full FLAC & MP3 playback up to 192 kHz / 24‑bit (FLAC limit) with bit‑perfect path where ALSA config permits*
- Gapless playback (same-format consecutive tracks)
- Mixed-source queue: seamlessly queue tracks from different sources together

Other:
- MusicCast device volume control and automatic power on/off
- Low CPU & memory footprint; performance‑critical audio engine in C++ with direct ALSA access
- Runs well on Raspberry Pi 4 (Raspberry Pi OS bullseye tested)
- Kalinka Music App (separate project) provides multi‑platform control UI

Maintenance / Utilities:
- Database purge & restart options (recent additions) for recovery / rebuilding index
- Structured logging with adjustable verbosity

# Installation
## Debian Package
A deb package for arm64 (Raspbian) is provided in the [Releases](https://github.com/madenvel/KalinkaPlayer/releases) section.

### Building the Debian Package
You can build a Debian package for your platform by following these steps:

#### Prerequisites
Install the required system dependencies:
```bash
sudo apt install python3 g++ libasound2-dev libflac-dev libflac++-dev libcurlpp-dev libspdlog-dev libfmt-dev python3-dev python3-venv python3-pip build-essential
```

#### Build Process
1. Clone the repository:
```bash
git clone https://github.com/madenvel/KalinkaPlayer.git
cd KalinkaPlayer
```

2. Set up Python virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Build the Debian package:
```bash
make build-deb
```
*Or simply use `make` (which calls `build-deb` by default)*

This will:
- Build the native C++ audio engine first
- Create a Python wheel that includes the native player library
- Package everything into a Debian package with version matching the wheel

The resulting package will be named `kalinka-player-<version>.<architecture>.deb` where the version matches the one from the Python wheel (e.g., `kalinka-player-1.4.1.dev96+g641eb978a.d20250905.amd64.deb`). The version of the package is taken from the most recent `release-x.y.z` tag with `z` increased by one.


#### Cleaning Build Artifacts
To clean up build artifacts:
```bash
make clean
```
This removes compiled objects, shared libraries, and generated Debian packages.

#### Installation
Install the generated package:
```bash
sudo dpkg -i kalinka-player-*.deb
sudo apt-get install -f  # Install any missing dependencies
```

**Note**: The package is built for the platform it's being built on (no cross-compilation).

#### Service Management
- Restart: `sudo systemctl restart kalinka.service`
- Check status: `systemctl status kalinka.service`
- View logs: `journalctl -u kalinka.service`

# Running from Sources
## Development Setup
For development or running directly from sources without creating a package:

## Prepare Environment
1. Clone the repository: `git clone https://github.com/madenvel/KalinkaPlayer.git`
2. Install pre-requisites:
```bash
sudo apt install python3 g++ libasound2-dev libflac-dev libflac++-dev libcurlpp-dev libspdlog-dev libfmt-dev python3-dev
```
3. Create python virtual environment:
```bash
cd KalinkaPlayer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
4. Build the native player:
```bash
cd packages/kalinka_server/src/native_player
make
cd ../
```
**Note**: When building the Debian package with `make build-deb`, this step is automatically handled.

5. Run the server:
```bash
nohup ./run_server.py &
```
The log will be saved to `nohup.out`.
If you were running the server on Raspberry Pi, you can logout now.

6. Download and install the app (see KalinkaApp project) and goto Settings -> Connection menu - your service should show up under the name you specified. Pick it from the list and tap "Connect".
7. Enjoy!

# Notes
* Audio engine uses ALSA directly and relies on its configuration. If automatic resampling is set up, it will likely affect the app but it should still work.
* I run this on Raspberry Pi 4 with HiFiBerry Digi2 card configured as recommended in their manual. This software would likely work with any card that works with ALSA but there might be issues hence certain quirks could be needed.

## Project structure and modular plugin interface

KalinkaPlayer is designed as a small core server (`packages/kalinka-server`) with a modular plugin system. The server exposes a REST API for control, discovery and queue management while plugins implement source/back-end integrations (local files, streaming services, device integrations) and enrichers.

Plugins live in the `packages/` directory and are simple Python packages that expose a well-defined interface the server loads at runtime. This modular approach lets you add or remove functionality without changing the server core. Typical plugin responsibilities include:
- Providing a source of tracks / metadata (e.g. local filesystem)
- Supplying enrichment / metadata lookup hooks
- Exposing any external device-specific control (volume, power state)

The plugin interface is intentionally lightweight: a plugin package exposes entry points and a small set of classes/functions that the server discovers using Python package metadata (or simple import-time registration). See the `packages/kalinka-plugin-sdk` to get familiar with the plugin interface and check cookiecutter template manual `template/cookiecutter-kalinka-plugin/README.md` to understand the plugin structure.

## Development: run the server from source

Use a virtual environment and install the server in editable mode so changes to server code are picked up immediately.

1. Create and activate a virtualenv (from the repository root):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install server dependencies in editable mode (this will make `packages/kalinka-server` importable and allow live edits):

```bash
cd packages/kalinka-server
pip install -e .
pip install -r requirements.txt
```

3. Build the native player library:

```bash
cd src/native_player
make
```

4. Run the server (foreground for development):

```bash
kalinka-server --config <config_file>
```

*Note*: Plugin configuration files will be created in the same directory as the main config.

Notes:
- Running `pip install -e .` inside `packages/kalinka-server` is recommended for development: it registers the package and entry points without rebuilding wheels.
- If you modify plugin code under `packages/kalinka-plugin-*`, an editable install of those packages (from their package folders) will also make changes visible immediately. Example:

```bash
cd packages/kalinka-plugin-localfiles
pip install -e .
```

## Building plugins and packages

Each plugin in `packages/` is a normal Python package with a `pyproject.toml` or `setup.py` and includes packaging scripts under `scripts/`. To build a wheel or Debian package for a plugin you can:

- Build a wheel (in the plugin directory):

```bash
cd packages/kalinka-plugin-localfiles
scripts/build_wheel.sh
```

- Build a Debian package (many plugins provide helper scripts):

```bash
cd packages/kalinka-plugin-localfiles
./scripts/build_deb.sh
```

The top-level `Makefile` and `packages/kalinka-server` build scripts also orchestrate building the native player, the server wheel and packaging everything into a `.deb` when building the full project.

## Plugins (included with this repository)

Below is a short description of each plugin included under `packages/`.

- kalinka-plugin-localfiles
  - Purpose: Core local filesystem playback plugin. It indexes directories of audio files, extracts metadata, and exposes the local library to the server. This plugin implements the most complete set of features (indexing, enrichment, artwork handling, and playback mapping).
  - Make sure to check plugin settings via the app to properly enable enrichment plugins like Acoustid (required appID which you can get for free in their website)

- kalinka-plugin-musiccast
  - Purpose: Integration with Yamaha MusicCast devices for device discovery and remote volume/power control. This plugin lets Kalinka control MusicCast receivers (volume, standby) and optionally route playback.

- kalinka-plugin-dummydevice
  - Purpose: A development / testing plugin that simulates a playback device. Useful for CI, tests and development when hardware is not available. It provides a mock audio sink and basic control endpoints.

- kalinka-plugin-sdk
  - Purpose: SDK and helper utilities for writing plugins. Includes templates, common interfaces and packaging helpers. Use this when creating new plugins to ensure compatibility with the server. This is a mandatory dependency for any Kalinka plugins.

If you want to create a new plugin, start with the cookiecutter template under `template/cookiecutter-kalinka-plugin/` - please check the manual supplied on how to do that.

## Example: install plugins for development

From the repository root you can install the server and all included plugins editable so you can iterate quickly:

```bash
source .venv/bin/activate
cd packages/kalinka-server && pip install -e . && cd -
for p in packages/kalinka-plugin-*; do
  (cd "$p" && pip install -e . || true)
done
```

After installing editable packages, start the server (`kalinka-server --config <config>`) and the server will import available plugins automatically.

## How to contribute

1. Fork the repository and create a feature branch.
2. Run tests under `packages/*/tests` (most packages include a `tests` directory, `native-player` have its own set of tests written C++).
3. Open a pull request describing the change and which plugin or server area it affects.

## Quick checklist (developer)

- Clone repo
- Create venv and activate
- Install server editable: `cd packages/kalinka-server && pip install -e .`
- Install any plugin you will develop editable too: `cd packages/kalinka-plugin-localfiles && pip install -e .`
- Build native player: `cd packages/kalinka-server/src/native_player && make`
- Run server: `kalinka-server --config <config>` - when in virtual environment and kalinka-server wheel installed locally.

---

If you'd like, I can also add a short example `dev-setup.sh` script to the repository to automate the above steps. 
