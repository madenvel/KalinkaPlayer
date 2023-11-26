cdef public void call_obj(obj, int contextId, int oldState, int newState) with gil:
    obj(contextId, oldState, newState)

cdef public void call_obj_pr(obj, float progress) with gil:
    obj(progress)

