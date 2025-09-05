# What is it?
KalinkaPlayer is a lightweight backend service for music playback on Linux systems (including Raspberry Pi 4+) exposing a REST API for control, library discovery and queue management.

The current focus and most advanced functionality is LOCAL FILE PLAYBACK. A flexible indexing & enrichment pipeline builds and maintains a rich local music library. Track / album / artist metadata can be augmented using external services (AcoustID fingerprinting, MusicBrainz, Wikidata and others) with a fallback strategy to keep the library usable even when some lookups fail.

An experimental integration with [Qobuz](https://www.qobuz.com) exists and can be enabled, allowing you to mix local tracks and Qobuz items inside the same play queue. Qobuz support is intentionally minimal compared to local file handling and may change.

The target audience: DIY HiFi enthusiasts comfortable with Linux and the command line who want a controllable, efficient audio backend.

> Disclaimer: Qobuz functionality is EXPERIMENTAL, not officially endorsed or supported by Qobuz, and may be removed at any time. This project is not affiliated with or sponsored by Qobuz.

# Features
Core (Local Library):
- Local files playback (primary, most advanced path)
  - Indexing of directory trees into an internal database
  - Metadata enrichment via AcoustID (fingerprints) -> MusicBrainz -> Wikidata (+ pluggable enrichers)
  - Fallback / defensive enrichment: partial metadata retained even when external services fail
  - Artwork & basic entity relationship modeling (artists, albums, tracks)
- Full FLAC & MP3 (and OGG) playback up to 192 kHz / 24‑bit (FLAC limit) with bit‑perfect path where ALSA config permits*
- Gapless playback (same-format consecutive tracks)
- Mixed-source queue: seamlessly queue local tracks together with experimental Qobuz items

Experimental (Qobuz):
- Search & limited discovery (new releases, playlists by category)
- Add / remove favorites
- Autoplay / Weekly mix style playlist

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

The resulting package will be named `kalinka-player-<version>.<architecture>.deb` where the version matches the one from the Python wheel (e.g., `kalinka-player-1.4.1.dev96+g641eb978a.d20250905.amd64.deb`).

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
cd native_player
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
* I run this on Raspberry Pi 4 with HiFiBerry Digi2 card configured as recommended in their manual. This software would likely work with any card that works with ALSA but there might be issues.
* Qobuz integration is experimental and may break or be removed without notice.
