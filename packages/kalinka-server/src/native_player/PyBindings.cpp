#include "AlsaDeviceEnumeration.h"
#include "AlsaVolumeControl.h"
#include "AudioGraphNode.h"
#include "AudioInfo.h"
#include "AudioPlayer.h"
#include "Config.h"
#include "StateMonitor.h"
#include "StreamState.h"

#include <pybind11/operators.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;

void flatten_dict(const py::dict &py_dict,
                  std::unordered_map<std::string, std::string> &result,
                  const std::string &prefix = "") {
  for (auto item : py_dict) {
    std::string key = item.first.cast<std::string>();
    auto value = item.second;

    std::string new_prefix = prefix.empty() ? key : prefix + "." + key;

    if (py::isinstance<py::dict>(value)) {
      flatten_dict(value.cast<py::dict>(), result, new_prefix);
    } else if (py::isinstance<py::str>(value)) {
      result[new_prefix] = value.cast<std::string>();
    } else if (py::isinstance<py::bool_>(value)) {
      result[new_prefix] = value.cast<bool>() ? "true" : "false";
    } else if (py::isinstance<py::int_>(value)) {
      result[new_prefix] = std::to_string(value.cast<int>());
    } else if (py::isinstance<py::float_>(value)) {
      result[new_prefix] = std::to_string(value.cast<float>());
    }
  }
}

std::unordered_map<std::string, std::string>
dict_to_map(const py::dict &py_dict) {
  std::unordered_map<std::string, std::string> result;
  flatten_dict(py_dict, result);
  return result;
}

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
      .def_readwrite("stream_type", &StreamInfo::streamType)
      .def_readwrite("stream_size", &StreamInfo::streamSize)
      .def("__repr__", [](const StreamInfo &a) { return a.toString(); })
      .def(pybind11::self == pybind11::self)
      .def(pybind11::self != pybind11::self);

  py::enum_<StreamErrorSource>(m, "StreamErrorSource")
      .value("NONE", StreamErrorSource::NONE)
      .value("HTTP_STREAM", StreamErrorSource::HTTP_STREAM)
      .value("AUDIO_OUTPUT", StreamErrorSource::AUDIO_OUTPUT)
      .value("DECODER", StreamErrorSource::DECODER)
      .export_values();

  py::class_<StreamError>(m, "StreamError")
      .def(py::init<StreamErrorSource, std::string>(),
           py::arg("source"), py::arg("message"))
      .def_readwrite("source", &StreamError::source)
      .def_readwrite("message", &StreamError::message)
      .def(pybind11::self == pybind11::self)
      .def(pybind11::self != pybind11::self);

  py::class_<StreamState>(m, "StreamState")
      .def(py::init<AudioGraphNodeState, long, std::optional<StreamInfo>>(),
           py::arg("state"), py::arg("position"), py::arg("stream_info"))
      .def(py::init<AudioGraphNodeState, StreamError>(),
           py::arg("state"), py::arg("error"))
      .def(py::init<AudioGraphNodeState>(), py::arg("state"))
      .def(py::init<AudioGraphNodeState, long>(), py::arg("state"),
           py::arg("position"))
      .def_readwrite("state", &StreamState::state)
      .def_readwrite("position", &StreamState::position)
      .def_readwrite("error", &StreamState::error)
      .def_readwrite("stream_info", &StreamState::streamInfo)
      .def_readwrite("timestamp", &StreamState::timestamp)
      .def("__repr__", [](const StreamState &s) { return s.toString(); })
      .def(pybind11::self == pybind11::self)
      .def(pybind11::self != pybind11::self);

  py::enum_<StreamType>(m, "StreamType")
      .value("BYTES", StreamType::BYTES)
      .value("FRAMES", StreamType::FRAMES)
      .export_values();

  py::class_<StateMonitor>(m, "StateMonitor")
      .def("wait_state", &StateMonitor::waitState)
      .def("has_data", &StateMonitor::hasData)
      .def("is_running", &StateMonitor::isRunning)
      .def("stop", &StateMonitor::stop);

  // Which backend produced a VolumeState; the Python fallback device runs its
  // external-change listener only for HARDWARE.
  py::enum_<VolumeBackend>(m, "VolumeBackend")
      .value("NONE", VolumeBackend::None)
      .value("HARDWARE", VolumeBackend::Hardware)
      .value("SOFTWARE", VolumeBackend::Software)
      .export_values();

  // Percent-scale (0..100) snapshot of the active volume backend.
  py::class_<VolumeState>(m, "VolumeState")
      .def_readonly("supported", &VolumeState::supported)
      .def_readonly("current", &VolumeState::current)
      .def_readonly("max", &VolumeState::max)
      .def_readonly("backend", &VolumeState::backend);

  // Blocks in wait() until the hardware mixer changes (knob / amixer / another
  // app). Mirrors StateMonitor; inert when the active backend isn't hardware.
  py::class_<VolumeMonitor>(m, "VolumeMonitor")
      .def("wait", &VolumeMonitor::wait)
      .def("has_data", &VolumeMonitor::hasData)
      .def("is_running", &VolumeMonitor::isRunning)
      .def("stop", &VolumeMonitor::stop);

  py::class_<AudioPlayer>(m, "AudioPlayer")
      .def(py::init<const Config &>(), py::arg("config"))
      .def("append", &AudioPlayer::append, py::arg("url"), py::arg("format"))
      .def("remove", &AudioPlayer::remove, py::arg("stream_id"))
      .def("clear_all", &AudioPlayer::clearAll)
      .def("stop", &AudioPlayer::stop)
      .def("pause", &AudioPlayer::pause)
      .def("resume", &AudioPlayer::resume)
      .def("seek", &AudioPlayer::seek, py::arg("position_ms"))
      .def("get_state", &AudioPlayer::getState)
      .def("monitor", &AudioPlayer::monitor)
      .def("get_volume", &AudioPlayer::getVolume)
      .def("set_volume", &AudioPlayer::setVolume, py::arg("volume"))
      .def("volume_monitor", &AudioPlayer::volumeMonitor);

  py::enum_<AudioGraphNodeState>(m, "AudioGraphNodeState")
      .value("ERROR", AudioGraphNodeState::ERROR)
      .value("STOPPED", AudioGraphNodeState::STOPPED)
      .value("PREPARING", AudioGraphNodeState::PREPARING)
      .value("STREAMING", AudioGraphNodeState::STREAMING)
      .value("PAUSED", AudioGraphNodeState::PAUSED)
      .value("FINISHED", AudioGraphNodeState::FINISHED)
      .value("SOURCE_CHANGED", AudioGraphNodeState::SOURCE_CHANGED)
      .export_values();

  py::enum_<AudioFormat>(m, "AudioFormat")
      .value("FLAC", AudioFormat::FormatFlac)
      .value("MPEG", AudioFormat::FormatMpeg)
      .export_values();

  m.def("py_dict_to_config", &dict_to_map);

  // ALSA device enumeration — exposed as AlsaPcmDevice objects with
  // read-only `name`, `label`, and `ioid` attributes (the Python side
  // in alsa_options.py reads them by name). `name` is what gets passed
  // back to snd_pcm_open() at playback time (e.g. "default",
  // "hw:CARD=sofhdadsp,DEV=0"), `label` is the joined card+pcm
  // description for the UI, and `ioid` is "Output", "Input", or empty
  // (= both). Filtering to outputs is the caller's job.
  py::class_<AlsaPcmDevice>(m, "AlsaPcmDevice")
      .def_readonly("name", &AlsaPcmDevice::name)
      .def_readonly("label", &AlsaPcmDevice::label)
      .def_readonly("ioid", &AlsaPcmDevice::ioid);

  m.def("list_alsa_pcm_devices", &listAlsaPcmDevices,
        "Enumerate ALSA PCM device hints (every CARD/DEV combo + "
        "virtual entries like 'default', 'pipewire'). Returns an "
        "empty list if libasound's hint API is unavailable.");
}