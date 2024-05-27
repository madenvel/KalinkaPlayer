cimport state_info

cdef from_cpp_instance(const state_info.StateInfo* cpp_instance)
cdef public void call_obj(obj, int contextId, const state_info.StateInfo* state) noexcept with gil

cdef extern from "PyObjectWrapper.h":
    cdef cppclass PyObjWrapper:
        PyObjWrapper()
        PyObjWrapper(object)