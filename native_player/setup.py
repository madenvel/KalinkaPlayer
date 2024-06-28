from setuptools import setup, Extension
import pybind11

compile_flags = ["-O2", "--std=c++23", "-D__PYTHON__"]

extensions = [
    Extension(
        "native_player",
        sources=[
            "AlsaAudioEmitter.cpp",
            "AudioFormat.cpp",
            "AudioGraphHttpStream.cpp",
            "AudioGraphNode.cpp",
            "AudioPlayer.cpp",
            "AudioStreamSwitcher.cpp",
            "FlacStreamDecoder.cpp",
            "StreamState.cpp",
            "StateMonitor.cpp",
            "PyBindings.cpp",
        ],
        include_dirs=[pybind11.get_include()],
        libraries=["curlpp", "curl", "FLAC++", "FLAC", "asound", "pthread", "m"],
        language="c++",
        extra_compile_args=compile_flags,
        # , "-fsanitize=address"],
    )
]

setup(
    name="native_player",
    ext_modules=extensions,
)
