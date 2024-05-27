from setuptools import setup, Extension

from Cython.Build import build_ext, cythonize

extensions = [
    Extension(
        "rpiplayer",
        sources=[
            "rpiplayer.pyx",
            "call_obj.pyx",
            "FlacDecoder.cpp",
            "DataSource.cpp",
            "AlsaPlayNode.cpp",
            "ThreadPool.cpp",
            "ProcessNode.cpp",
            "BufferNode.cpp",
            "AudioFormat.cpp",
        ],
        libraries=["curlpp", "curl", "FLAC++", "FLAC", "asound"],
        language="c++",
        extra_compile_args=["-O2", "--std=c++20"],
        # , "-fsanitize=address"],
    ),
    Extension(
        "state_info",
        sources=["state_info.pyx", "StateMachine.cpp"],
        language="c++",
        extra_compile_args=["-O2", "--std=c++20"],
    ),
]

setup(
    name="rpiplayer",
    cmdclass={"build_ext": build_ext},
    ext_modules=cythonize(extensions),
)
