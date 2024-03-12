#ifndef PY_OBJ_WRAPPER_H
#define PY_OBJ_WRAPPER_H

#include "call_obj.h"
#include <Python.h>
#include <string>

class PyObjWrapper {
public:
  PyObjWrapper(PyObject *o) : held(o) { Py_XINCREF(o); }
  PyObjWrapper(const PyObjWrapper &rhs) : PyObjWrapper(rhs.held) {}
  PyObjWrapper(PyObjWrapper &&rhs) : held(rhs.held) { rhs.held = nullptr; }
  PyObjWrapper() : PyObjWrapper(nullptr) {}
  ~PyObjWrapper() { Py_XDECREF(held); }

  PyObjWrapper &operator=(const PyObjWrapper &rhs) {
    PyObjWrapper tmp = rhs;
    return (*this = std::move(tmp));
  }

  PyObjWrapper &operator=(PyObjWrapper &&rhs) {
    std::swap(held, rhs.held);
    return *this;
  }

  template <typename... Args> void operator()(Args... args) {
    if (held) {
      call_obj(held, args...);
    }
  }

private:
  PyObject *held;
};

#endif