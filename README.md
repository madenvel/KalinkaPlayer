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
Pre-built packages: TBD

A debian package can be built by running `make` in the root directory. Note, that there's no cross-compilation,
the package is built for the platform it is being built on.

Make sure you update the config file `/opt/kalinka/kalinka_conf.yaml` after you install the package (see below).

The service can be restarted with `sudo systemctl restart kalinka.service`, to check the status of the service
and the last log lines use `systemctl status kalinka.service`.

# Running from sources
## Prepare environment
1. Clone the repository, `git clone https://github.com/madenvel/KalinkaPlayer.git`
2. Install pre-requisites [WIP]
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
5. Create a config file in the root folder of the cloned project
```yaml
---
server:
  interface: <NETWORK INTERFACE> # i.g. wlan0
  port: <PORT>
  service_name: <SERVICE NAME> # i.g. MyPlayer
output:
  alsa:
    device: <ALSA OUTPUT DEVICE> # typically hw:0,0
addons:
  device:
    musiccast: # optional for musiccast device
      device_addr: <MUSICCAST IP> # ip address of musiccast device
      device_port: <MUSICCAST PORT> # port of musiccast device, typical 80
      connected_input: <CONNECTED OUTPUT> # output port to switch to, i.g. optical2
  input_module:
    qobuz:
      email: your@email.com # your qobuz email address
      password_hash: <PASSWORD MD5 HASH> # hash only, not plain text! see below
```

* `<NETWORK INTERFACE>` is the interface the server will listen to HTTP requests, usually the interface looking to your home network (not localhost). Use `ifconfig` to find out available interfaces.
* `<SERVICE NAME>` is a name you want to give to your service. It will be visible via ZeroConf
* `<ALSA OUTPUT DEVICE>` is your output device. This player support ALSA device only at the moment. To see available devices use `aplay -l`. Please refer to your
sound card or Raspberry Pi documentation on how to set it up correctly. Usually, this value is `hw:X,Y`, - where X is a device number and Y is subdevice.
* `<PASSWORD HASH>` to generate an MD5 hash for your password you can use the following command:
```
echo -n 'your password' | md5sum
```
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
