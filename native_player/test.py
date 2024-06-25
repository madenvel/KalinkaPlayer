#!/usr/bin/env python3

from native_player import AudioPlayer, AudioGraphNodeState

import time
from threading import Thread

stop = False


def print_state(monitor):
    while not stop:
        print("New state: ", monitor.wait_state())

    print("Monitor finished")


player = AudioPlayer("hw:0,0")

player.play("https://getsamplefiles.com/download/flac/sample-3.flac")
player.play_next("https://getsamplefiles.com/download/flac/sample-4.flac")

monitor = player.monitor()
state_update_thread = Thread(target=print_state, args=(monitor,))
state_update_thread.start()

while player.get_state().state != AudioGraphNodeState.FINISHED:
    time.sleep(1)
    print("Working!!!")

stop = True
monitor.stop()

state_update_thread.join()
