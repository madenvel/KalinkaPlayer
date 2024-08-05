#include "AudioGraphNode.h"
#include "AudioInfo.h"
#include "AudioPlayer.h"
#include "StateMonitor.h"
#include "StreamState.h"

#include <pybind11/operators.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;

PYBIND11_MODULE(native_player, m) {
  py::class_<StreamAudioFormat>(m, "StreamAudioFormat")
      .def(py::init<>())
      .def_readwrite("sample_rate", &StreamAudioFormat::sampleRate)
      .def_readwrite("channels", &StreamAudioFormat::channels)
      .def_readwrite("bits_per_sample", &StreamAudioFormat::bitsPerSample)
      .def("__repr__", [](const StreamAudioFormat &a) { return a.toString(); })
      .def(pybind11::self == pybind11::self)
      .def(pybind11::self != pybind11::self);

  py::class_<StreamInfo>(m, "StreamInfo")
      .def(py::init<>())
      .def_readwrite("format", &StreamInfo::format)
      .def_readwrite("total_samples", &StreamInfo::totalSamples)
      .def_readwrite("duration_ms", &StreamInfo::durationMs)
      .def("__repr__", [](const StreamInfo &a) { return a.toString(); })
      .def(pybind11::self == pybind11::self)
      .def(pybind11::self != pybind11::self);

  py::class_<StreamState>(m, "StreamState")
      .def(py::init<AudioGraphNodeState, long, std::optional<StreamInfo>>(),
           py::arg("state"), py::arg("position"), py::arg("stream_info"))
      .def(py::init<AudioGraphNodeState, std::optional<std::string>>(),
           py::arg("state"), py::arg("message"))
      .def(py::init<AudioGraphNodeState>(), py::arg("state"))
      .def(py::init<AudioGraphNodeState, long>(), py::arg("state"),
           py::arg("position"))
      .def_readwrite("state", &StreamState::state)
      .def_readwrite("position", &StreamState::position)
      .def_readwrite("message", &StreamState::message)
      .def_readwrite("stream_info", &StreamState::streamInfo)
      .def_readwrite("timestamp", &StreamState::timestamp)
      .def("__repr__", [](const StreamState &s) { return s.toString(); })
      .def(pybind11::self == pybind11::self)
      .def(pybind11::self != pybind11::self);

  py::class_<StateMonitor>(m, "StateMonitor")
      .def("wait_state", &StateMonitor::waitState)
      .def("has_data", &StateMonitor::hasData)
      .def("stop", &StateMonitor::stop);

  py::class_<AudioPlayer>(m, "AudioPlayer")
      .def(py::init<const std::string &>(), py::arg("audio_device"))
      .def("play", &AudioPlayer::play, py::arg("url"))
      .def("play_next", &AudioPlayer::playNext, py::arg("url"))
      .def("stop", &AudioPlayer::stop)
      .def("pause", &AudioPlayer::pause, py::arg("paused"))
      .def("seek", &AudioPlayer::seek, py::arg("position_ms"))
      .def("get_state", &AudioPlayer::getState)
      .def("monitor", &AudioPlayer::monitor);

  py::enum_<AudioGraphNodeState>(m, "AudioGraphNodeState")
      .value("ERROR", AudioGraphNodeState::ERROR)
      .value("STOPPED", AudioGraphNodeState::STOPPED)
      .value("PREPARING", AudioGraphNodeState::PREPARING)
      .value("STREAMING", AudioGraphNodeState::STREAMING)
      .value("PAUSED", AudioGraphNodeState::PAUSED)
      .value("FINISHED", AudioGraphNodeState::FINISHED)
      .value("SOURCE_CHANGED", AudioGraphNodeState::SOURCE_CHANGED)
      .export_values();
}