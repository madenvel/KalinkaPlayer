#include "AudioInfo.h"
#include "AudioPlayer.h"

#include <pybind11/operators.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;

void AudioPlayer::setPythonStateCallback(py::function cb) {
  this->setStateCallback([cb](int context, const StateInfo state) {
    py::gil_scoped_acquire acquire;
    cb(context, state);
  });
}

PYBIND11_MODULE(rpiplayer, m) {
  py::class_<AudioInfo>(m, "AudioInfo")
      .def(py::init<>())
      .def_readwrite("sample_rate", &AudioInfo::sampleRate)
      .def_readwrite("channels", &AudioInfo::channels)
      .def_readwrite("bits_per_sample", &AudioInfo::bitsPerSample)
      .def_readwrite("total_samples", &AudioInfo::totalSamples)
      .def_readwrite("duration_ms", &AudioInfo::durationMs)
      .def("__repr__", [](const AudioInfo &a) { return a.toString(); })
      .def(pybind11::self == pybind11::self)
      .def(pybind11::self != pybind11::self);

  py::class_<StateInfo>(m, "StateInfo")
      .def(py::init<>())
      .def_readwrite("state", &StateInfo::state)
      .def_readwrite("position", &StateInfo::position)
      .def_readwrite("message", &StateInfo::message)
      .def_readwrite("audio_info", &StateInfo::audioInfo)
      .def("__repr__", [](const StateInfo &s) { return s.toString(); })
      .def(pybind11::self == pybind11::self)
      .def(pybind11::self != pybind11::self);

  py::class_<AudioPlayer>(m, "AudioPlayer")
      .def(py::init<>())
      .def("prepare", &AudioPlayer::prepare, py::arg("url"),
           py::arg("level1BufferSize"), py::arg("level2BufferSize"))
      .def("remove_context", &AudioPlayer::removeContext, py::arg("contextId"))
      .def("play", &AudioPlayer::play, py::arg("contextId"))
      .def("stop", &AudioPlayer::stop)
      .def("pause", &AudioPlayer::pause, py::arg("paused"))
      .def("seek", &AudioPlayer::seek, py::arg("time"))
      .def("set_state_callback", &AudioPlayer::setPythonStateCallback,
           py::arg("callback"));

  py::enum_<State>(m, "State")
      .value("IDLE", State::IDLE)
      .value("READY", State::READY)
      .value("BUFFERING", State::BUFFERING)
      .value("PLAYING", State::PLAYING)
      .value("PAUSED", State::PAUSED)
      .value("FINISHED", State::FINISHED)
      .value("STOPPED", State::STOPPED)
      .value("ERROR", State::ERROR)
      .export_values();
}