from setuptools import setup, Extension
import pybind11

compile_flags = ["-O2", "--std=c++20"]

extensions = [
    Extension(
        "rpiplayer",
        sources=[
            "PyBindings.cpp",
            "AudioPlayer.cpp",
            "FlacDecoder.cpp",
            "DataSource.cpp",
            "AlsaPlayNode.cpp",
            "ThreadPool.cpp",
            "ProcessNode.cpp",
            "BufferNode.cpp",
            "AudioFormat.cpp",
        ],
        include_dirs=[pybind11.get_include()],
        libraries=["curlpp", "curl", "FLAC++", "FLAC", "asound"],
        language="c++",
        extra_compile_args=compile_flags,
        # , "-fsanitize=address"],
    )
]

setup(
    name="rpiplayer",
    ext_modules=extensions,
)
