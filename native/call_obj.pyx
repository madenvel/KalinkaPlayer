cdef public void call_obj(obj, int contextId, int oldState, int newState) noexcept with gil:
    obj(contextId, oldState, newState)

cdef public void call_obj_pr(obj, float progress) noexcept with gil:
    obj(progress)

