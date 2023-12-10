from libcpp.functional cimport function
from libcpp cimport bool
from libcpp.string cimport string


ctypedef function[void(int, int, int)] StateCallback
ctypedef function[void(int, float)] ProgressCallback

cdef extern from "AudioPlayer.cpp":
    pass

# Declare the class with cdef
cdef extern from "AudioPlayer.h":
    cdef cppclass AudioPlayer:
        AudioPlayer() except +
        int prepare(const char *url, size_t level1BufferSize, size_t level2BufferSize);
        void removeContext(int contextId);
        void play(int contextId);
        void stop();
        void pause(bool paused);
        void seek(int time);
        void setStateCallback(StateCallback cb);
        void setProgressUpdateCallback(ProgressCallback cb);
        string getLastError(int contextId);        