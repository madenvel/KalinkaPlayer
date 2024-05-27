cdef from_cpp_instance(const state_info.StateInfo* cpp_instance):
    py_obj = state_info.PyStateInfo(cpp_instance.state, cpp_instance.position)
    return py_obj

cdef public void call_obj(obj, int contextId, const state_info.StateInfo* state) noexcept with gil:
    py_instance = from_cpp_instance(state)
    obj(contextId, py_instance)
