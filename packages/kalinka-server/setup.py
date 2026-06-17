#!/usr/bin/env python3
"""
Setup script for Kalinka Player.

This setup.py includes the native extensions to ensure proper platform tagging.
"""

from setuptools import setup, Extension
import pybind11
import os

# Define native player extension
compile_flags = ["-fpermissive", "-O2", "--std=c++23", "-D__PYTHON__"]

# Get the source directory for native_player
native_player_src = os.path.join("src", "native_player")

extensions = [
    Extension(
        "native_player.native_player",
        sources=[
            os.path.join(native_player_src, "Buffer.cpp"),
            os.path.join(native_player_src, "AlsaAudioEmitter.cpp"),
            os.path.join(native_player_src, "AlsaDeviceEnumeration.cpp"),
            os.path.join(native_player_src, "AlsaVolumeControl.cpp"),
            os.path.join(native_player_src, "AudioSampleFormat.cpp"),
            os.path.join(native_player_src, "AudioGraphHttpStream.cpp"),
            os.path.join(native_player_src, "AudioGraphNode.cpp"),
            os.path.join(native_player_src, "AudioPlayer.cpp"),
            os.path.join(native_player_src, "AudioStreamSwitcher.cpp"),
            os.path.join(native_player_src, "FlacStreamDecoder.cpp"),
            os.path.join(native_player_src, "Mp3StreamDecoder.cpp"),
            os.path.join(native_player_src, "PerfMon.cpp"),
            os.path.join(native_player_src, "StreamState.cpp"),
            os.path.join(native_player_src, "StateMonitor.cpp"),
            os.path.join(native_player_src, "PyBindings.cpp"),
            os.path.join(native_player_src, "Log.cpp"),
        ],
        include_dirs=[pybind11.get_include()],
        libraries=[
            "curlpp",
            "curl",
            "FLAC++",
            "FLAC",
            "asound",
            "pthread",
            "spdlog",
            "fmt",
        ],
        language="c++",
        extra_compile_args=compile_flags,
    )
]

if __name__ == "__main__":
    setup(ext_modules=extensions)
