# distutils: language = c++

from AudioPlayer cimport AudioPlayer, StateCallback, ProgressCallback

cdef extern from "PyObjectWrapper.h":
    cdef cppclass PyObjWrapper:
        PyObjWrapper()
        PyObjWrapper(object)

    cdef cppclass PyObjWrapper2:
        PyObjWrapper2()
        PyObjWrapper2(object)


cdef class RpiAudioPlayer:
    cdef AudioPlayer* c_instance

    def __cinit__(self):
        self.c_instance = new AudioPlayer()

    def __init__(self):
        pass

    def prepare(self, url, level1_buf_size, level2_buf_size):
        url_b = url.encode('utf-8')
        return self.c_instance.prepare(url_b, level1_buf_size, level2_buf_size)

    def remove_context(self, context_id):
        self.c_instance.removeContext(context_id);

    def play(self, context_id):
        return self.c_instance.play(context_id)

    def stop(self):
        return self.c_instance.stop()

    def pause(self, paused):
        return self.c_instance.pause(paused)

    def seek(self, time):
        return self.c_instance.seek(time)

    def set_state_callback(self, cb):
        cdef PyObjWrapper wrappedCb = PyObjWrapper(cb)
        self.c_instance.setStateCallback(<StateCallback>wrappedCb)

    def set_progress_callback(self, cb):
        cdef PyObjWrapper2 wrappedCb = PyObjWrapper2(cb)
        self.c_instance.setProgressUpdateCallback(<ProgressCallback>wrappedCb)

    def __dealloc__(self):
        del self.c_instance