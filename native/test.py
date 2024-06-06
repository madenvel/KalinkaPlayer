#!/usr/bin/env python3

from rpiplayer import AudioPlayer, StateInfo

import time


def state_cb(context_id: int, state: StateInfo):
    print("State callback, context_id:", context_id, "state:", state)


player = AudioPlayer()
player.set_state_callback(state_cb)

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
time.sleep(3)
print("Start playback 2, time=", time.time())
player.play(context_2)
time.sleep(3)
player.stop()

context_3 = player.prepare(
    "http:://www.google.com",
    10 * 1024 * 1024,
    64 * 1024,
)

time.sleep(3)

print("End playback, time=", time.time())
