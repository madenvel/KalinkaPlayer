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

class PyObjWrapper2 {
public:
  PyObjWrapper2(PyObject *o) : held(o) { Py_XINCREF(o); }
  PyObjWrapper2(const PyObjWrapper2 &rhs) : PyObjWrapper2(rhs.held) {}
  PyObjWrapper2(PyObjWrapper2 &&rhs) : held(rhs.held) { rhs.held = nullptr; }
  PyObjWrapper2() : PyObjWrapper2(0) {}
  ~PyObjWrapper2() { Py_XDECREF(held); }

  PyObjWrapper2 &operator=(const PyObjWrapper2 &rhs) {
    PyObjWrapper2 tmp = rhs;
    return (*this = std::move(tmp));
  }

  PyObjWrapper2 &operator=(PyObjWrapper2 &&rhs) {
    std::swap(held, rhs.held);
    return *this;
  }

  template <typename... Args> void operator()(Args... args) {
    if (held) {
      std::invoke(call_obj_pr, held, args...);
    }
  }

private:
  PyObject *held;
};