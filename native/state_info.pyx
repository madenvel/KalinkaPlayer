# distutils: language = c++
# distutils: sources = StateMachine.cpp

cimport state_info

cdef class PyStateInfo:

    def __cinit__(self, int state, long position):
        self.cpp_obj = new state_info.StateInfo(<state_info.State>state, position)

    def __dealloc__(self):
        del self.cpp_obj

    @property
    def state(self):
        return self.cpp_obj.state

    @property
    def position(self):
        return self.cpp_obj.position
