from enum import Enum


class State(Enum):
    IDLE = 0
    READY = 1
    BUFFERING = 2
    PLAYING = 3
    PAUSED = 4
    FINISHED = 5
    STOPPED = 6
    ERROR = 7
