#include "AudioInfo.h"
#include "AudioPlayer.h"

#include <pybind11/pybind11.h>

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
      .def_readonly("sample_rate", &AudioInfo::sampleRate)
      .def_readonly("channels", &AudioInfo::channels)
      .def_readonly("bits_per_sample", &AudioInfo::bitsPerSample)
      .def_readonly("duration_ms", &AudioInfo::durationMs)
      .def("__repr__", [](const AudioInfo &a) { return a.toString(); });

  py::class_<StateInfo>(m, "StateInfo")
      .def(py::init<>())
      .def_readonly("state", &StateInfo::state)
      .def_readonly("position", &StateInfo::position)
      .def_readonly("message", &StateInfo::message)
      .def_readonly("audio_info", &StateInfo::audioInfo)
      .def("__repr__", [](const StateInfo &s) { return s.toString(); });

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
      .value("INVALID", State::INVALID)
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