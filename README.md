# What is it?
This project provides a backend to stream music on systems running Linux (including Raspberry Pi 4+) and  REST API to control the playqueue and playback, as well as an ability to discover new content.

At this point, it only supports [Qobuz](https://www.qobuz.com) as a streaming platform, and you must have a valid subscription.

The target audience for this are DIY HiFi enthusiasts familiar with linux and command line.

# Features
- Supports Qobuz as a streaming platform, including:
  - Search, new content discovery (New releases, Qobuz playlists, playlists by category, etc.)
  - Add / remove favorites
  - Autoplay
  - Weekly Q playlist
- Supports OGG/FLAC playback, up to 192Khz / 24bit (FLAC limitation), bit perfect playback*
- Gapless playback for the songs of the same audio format
- Supports MusicCast device volume control and automatic turn on / off
- Works on Raspberry Pi (used on RPi 4 with Raspberry OS bullseye), low CPU & memory usage. The main part is written in C++.
- Kalinka Music App is a player control application that runs on multiple platforms.

# Installation
## Debian-package
A deb package for arm64 (Raspbian) is provided in the [Releases](https://github.com/madenvel/KalinkaPlayer/releases) section.

A debian package can be built by running `make` in the root directory. Note, that there's no cross-compilation,
the package is built for the platform it is being built for.

Make sure you update the config file `/opt/kalinka/kalinka_conf.yaml` after you install the package (see below).

The service can be restarted with `sudo systemctl restart kalinka.service`, to check the status of the service
and the last log lines use `systemctl status kalinka.service`.

To check the full log: `journalctl -u kalinka.service`.

# Running from sources
## Prepare environment
1. Clone the repository, `git clone https://github.com/madenvel/KalinkaPlayer.git`
2. Install pre-requisites
```
sudo apt install python3 g++ libasound2-dev libflac-dev libflac++-dev libcurlpp-dev libspdlog-dev libfmt-dev python3-dev
```
3. Create python virtual environment:
```
cd KalinkaPlayer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
4. Build the native player:
```
cd native_player
make
cd ../
```
5. Create a config file based on the [example](https://github.com/madenvel/KalinkaPlayer/blob/main/kalinka_conf_example.yaml)
6. Run the server
```
nohup ./run_server.py &
```
The log will be saved to `nohup.out`.
If you were running the server on Raspberry Pi, you can logout now.

7. Download and install the app (see KalinkaApp project) and goto Settings -> Connection menu - your service should show up under the name you specified. Pick it from the list and tap "Connect".
9. Enjoy!

# Notes
* Audio engine uses ALSA directly and relies on its configuration. If automatic resampling is set up, it will likely affect the app but it should still work.
* I run this on Raspberry Pi 4 with HiFiBerry Digi2 card configured as recommended in their manual. This software would likely work with any card that works with ALSA but there might be issues.
