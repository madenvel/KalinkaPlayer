# state_info.pxd
cdef extern from "StateMachine.h":
    cdef enum State:
        pass
    cdef cppclass StateInfo:
        StateInfo(State state, int position)
        int state
        int position

cdef class PyStateInfo:
    cdef StateInfo* cpp_obj

