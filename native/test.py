#!/usr/bin/env python3

from rpiplayer import RpiAudioPlayer

import time

player = RpiAudioPlayer()

context_1 = player.prepare(
    "https://getsamplefiles.com/download/flac/sample-2.flac",
    10 * 1024 * 1024,
    64 * 1024,
)

context_2 = player.prepare(
    "https://getsamplefiles.com/download/flac/sample-4.flac",
    10 * 1024 * 1024,
    64 * 1024,
)

time.sleep(3)

print("Start playback, time=", time.time())
player.play(context_1)
info = player.get_audio_info(context_1)
print("Audio info=", info)
time.sleep(3)
print("Start playback 2, time=", time.time())
player.play(context_2)
time.sleep(3)
player.stop()

print("End playback, time=", time.time())
