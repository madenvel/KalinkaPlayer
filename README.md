# What is it?
This project provides a backend to stream music on systems running Linux. It provides an REST API to control the playqueue and playback,
as well as an ability to discover new content.

At this point, it only supports [Qobuz](https://www.qobuz.com) as a streaming platform, so you need to have a valid subscription.

The target audience for this are DIY HiFi enthusiast familiar with linux and command line.

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
- Control App (see RpiMusic project) that runs on multiple platforms*.

# Installation
There's currently no package provided for easy installation, hence the service has to be built and run manually. This is planned to be addressed in the future.

## Prepare environment
1. Clone the repository, `git clone https://github.com/madenvel/RpiPlayer.git`
2. Install pre-requisites [WIP]
```
sudo apt install python3 g++ libasound-dev libflac-dev libflac++-dev libcurlpp-dev llibspdlog-dev libfmt-dev
```
3. Create python virtual environment:
```
cd RpiPlayer
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
output_device:
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
7. Download and install the app (see RpiMusic project) and goto Settings -> Connection menu - your service should show up under the name you specified. Pick it from the list and tap "Connect".
8. Enjoy!

# Notes
* The app has been tested on Windows and on Android mostly. Zeroconf client doesn't work properly on Windows (i.g. ip address of the service is not returned correctly).
* Playback notification is only implemented for Android in native code (Java) hence not available for other platforms (as I don't have a device at the moment). 
* Audio engine uses ALSA directly and relies on its configuration. If automatic resampling is set up, it will likely affect the app but it should still work.
* I run this on Raspberry Pi 4 with HiFiBerry Digi2 card configured as recommended in their manual. This software would likely work with any card that supports ALSA
* but there might be issues which may need to be fixed.
