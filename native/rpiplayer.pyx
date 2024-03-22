# distutils: language = c++

from AudioPlayer cimport AudioPlayer, StateCallback, AudioInfo

cdef extern from "PyObjectWrapper.h":
    cdef cppclass PyObjWrapper:
        PyObjWrapper()
        PyObjWrapper(object)


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

    def get_last_error(self, context_id):
        return self.c_instance.getLastError(context_id)

    def get_audio_info(self, context_id):
        caudio_info = self.c_instance.getAudioInfo(context_id)
        result = {
            'sample_rate': caudio_info.sampleRate,
            'channels': caudio_info.channels,
            'bits_per_sample': caudio_info.bitsPerSample,
            'duration_ms': caudio_info.durationMs
        }

        return result

    def __dealloc__(self):
        del self.c_instance