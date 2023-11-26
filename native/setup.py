from setuptools import setup

from Cython.Build import build_ext

from distutils.extension import Extension


setup(
    cmdclass={"build_ext": build_ext},
    ext_modules=[
        Extension(
            "rpiplayer",
            sources=[
                "FlacDecoder.cpp",
                "DataSource.cpp",
                "AlsaPlayNode.cpp",
                "ThreadPool.cpp",
                "StateMachine.cpp",
                "ProcessNode.cpp",
                "BufferNode.cpp",
                "AudioFormat.cpp",
                "rpiplayer.pyx",
                "call_obj.pyx",
            ],
            # libraries=["externlib"],  # refers to "libexternlib.so"
            language="c++",  # remove this if C and not C++
            extra_compile_args=["-O2", "--std=c++20"],
            # , "-fsanitize=address"],
            extra_link_args=["-lcurlpp", "-lcurl", "-lFLAC++", "-lFLAC", "-lasound"],
        )
    ],
)
