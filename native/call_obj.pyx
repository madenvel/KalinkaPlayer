cdef public void call_obj(obj, int contextId, int newState, long position) noexcept with gil:
    obj(contextId, newState, position)


