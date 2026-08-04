#include "NativePlayer.h"

#include <spdlog/spdlog.h>

#include <algorithm>
#include <chrono>

#include "../config/SettingsPersistence.h"
#include "../native_player/AlsaDeviceEnumeration.h"
#include "StateTranslator.h"

namespace asio = boost::asio;
namespace pb = kalinka::renderer::v1;

namespace {

int64_t nowUnixMs() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}

AudioFormat formatOf(const pb::Source &source) {
  const std::string &mime = source.mime_type();
  if (mime.find("mpeg") != std::string::npos ||
      mime.find("mp3") != std::string::npos) {
    return AudioFormat::FormatMpeg;
  }
  if (mime.find("flac") != std::string::npos) {
    return AudioFormat::FormatFlac;
  }
  if (source.uri().ends_with(".mp3")) {
    return AudioFormat::FormatMpeg;
  }
  return AudioFormat::FormatFlac;
}

// The noisy virtual PCMs the server's device list also hides.
bool noisyPcm(const std::string &name) {
  static const char *prefixes[] = {"front:",  "surround", "iec958:",
                                   "dmix:",   "dsnoop:",  "hdmi:",
                                   "usbstream:"};
  return std::any_of(std::begin(prefixes), std::end(prefixes),
                     [&name](const char *p) { return name.starts_with(p); });
}

}  // namespace

const std::map<std::string, std::string> &NativePlayer::defaultSettings() {
  static const std::map<std::string, std::string> defaults{
      {"output.driver", "alsa"},
      {"output.device", "default"},
      {"output.volume_mode", "auto"},
  };
  return defaults;
}

void NativePlayer::persistOverrides() const {
  std::map<std::string, std::string> overrides;
  for (const auto &[key, value] : settings_) {
    if (defaultSettings().at(key) != value) {
      overrides[key] = value;
    }
  }
  saveSettingsOverrides(overrides);
}

NativePlayer::NativePlayer(asio::io_context &ioc) : ioc_(ioc) {
  for (const auto &[key, value] : loadSettingsOverrides()) {
    const auto setting = settings_.find(key);
    if (setting != settings_.end()) {
      setting->second = value;
      spdlog::info("Config override {} = '{}'", key, value);
    }
  }
  ensurePlayer();
}

NativePlayer::~NativePlayer() { stopPumps(); }

void NativePlayer::setStateSink(StateSink sink) { sink_ = std::move(sink); }

bool NativePlayer::ensurePlayer() {
  if (player_) {
    return true;
  }
  try {
    player_ = std::make_unique<AudioPlayer>(Config{
        {"output.alsa.device", settings_.at("output.device")},
    });
    player_->configureVolume(effectiveVolumeMode(), "");
    startPumps();
    return true;
  } catch (const std::exception &e) {
    spdlog::error("Audio graph unavailable on device '{}': {}",
                  settings_.at("output.device"), e.what());
    player_.reset();
    return false;
  }
}

void NativePlayer::rebuildPlayer() {
  stopPumps();
  sources_.clear();
  currentId_.reset();
  lastFormat_.reset();
  player_.reset();
  ensurePlayer();
  // Whatever was playing is gone, as APPLY_COST_INTERRUPTS_PLAYBACK declared.
  pb::Envelope env;
  pb::PlaybackStateChanged *changed = env.mutable_playback_state_changed();
  changed->set_state(pb::PLAYBACK_STATE_STOPPED);
  changed->set_position_valid(false);
  changed->set_at_unix_ms(nowUnixMs());
  emit(env);
}

void NativePlayer::startPumps() {
  stateMonitor_ = std::shared_ptr<StateMonitor>(player_->monitor().release());
  volumeMonitor_ =
      std::shared_ptr<VolumeMonitor>(player_->volumeMonitor().release());

  statePump_ = std::thread([this, monitor = stateMonitor_] {
    while (monitor->isRunning()) {
      StreamState state = monitor->waitState();
      if (!monitor->isRunning()) {
        break;
      }
      asio::post(ioc_, [this, state] { onStreamState(state); });
    }
  });
  volumePump_ = std::thread([this, monitor = volumeMonitor_] {
    while (monitor->isRunning()) {
      VolumeState volume = monitor->wait();
      if (!monitor->isRunning()) {
        break;
      }
      asio::post(ioc_, [this, volume] { emitVolume(volume, true); });
    }
  });
}

void NativePlayer::stopPumps() {
  if (stateMonitor_) {
    stateMonitor_->stop();
  }
  if (volumeMonitor_) {
    volumeMonitor_->stop();
  }
  if (statePump_.joinable()) {
    statePump_.join();
  }
  if (volumePump_.joinable()) {
    volumePump_.join();
  }
  stateMonitor_.reset();
  volumeMonitor_.reset();
}

void NativePlayer::reportUnavailable(const char *command) {
  spdlog::warn("Command {}: audio graph unavailable", command);
  pb::Envelope env;
  pb::PlaybackStateChanged *changed = env.mutable_playback_state_changed();
  changed->set_state(pb::PLAYBACK_STATE_ERROR);
  changed->set_position_valid(false);
  changed->set_at_unix_ms(nowUnixMs());
  changed->mutable_error()->set_source(pb::ERROR_SOURCE_AUDIO_OUTPUT);
  changed->mutable_error()->set_message(
      "audio output is unavailable; check the configured device");
  emit(env);
}

StreamId NativePlayer::appendSource(const pb::Source &source) {
  const StreamId id = nextStreamId_++;
  sources_.push_back(TrackedSource{source, id});
  player_->append(id, source.uri(), formatOf(source));
  return id;
}

void NativePlayer::setSource(const pb::Source &source) {
  if (!ensurePlayer()) {
    return reportUnavailable("set_source");
  }
  // Append before removing, or the graph reports the queue finished instead
  // of switching.
  std::vector<StreamId> displaced;
  for (const TrackedSource &tracked : sources_) {
    displaced.push_back(tracked.streamId);
  }
  appendSource(source);
  for (StreamId id : displaced) {
    player_->remove(id);
  }
  // Displaced entries stay until the graph confirms the switch: states still
  // in flight for them must keep resolving to a token.
}

void NativePlayer::enqueueSource(const pb::Source &source) {
  if (!ensurePlayer()) {
    return reportUnavailable("enqueue_source");
  }
  appendSource(source);
}

void NativePlayer::removeSource(const std::string &sourceToken) {
  if (!player_) {
    return;  // nothing to remove from
  }
  auto it = std::find_if(sources_.begin(), sources_.end(),
                         [&sourceToken](const TrackedSource &tracked) {
                           return tracked.source.source_token() == sourceToken;
                         });
  if (it == sources_.end()) {
    spdlog::debug("Ignoring removal of unknown source token {}", sourceToken);
    return;
  }
  // Removing the current source ends it as if it had finished.
  player_->remove(it->streamId);
  if (currentId_ != it->streamId) {
    sources_.erase(it);
  }
}

void NativePlayer::clearQueue() {
  if (!player_) {
    return;
  }
  player_->clearAll();
  sources_.clear();
  currentId_.reset();
}

void NativePlayer::pause() {
  if (!ensurePlayer()) {
    return reportUnavailable("pause");
  }
  player_->pause();
}

void NativePlayer::resume() {
  if (!ensurePlayer()) {
    return reportUnavailable("resume");
  }
  player_->resume();
}

void NativePlayer::stop() {
  if (!player_) {
    return;
  }
  player_->stop();
  sources_.clear();
  currentId_.reset();
  lastFormat_.reset();
}

void NativePlayer::setVolume(uint32_t percent) {
  if (!ensurePlayer()) {
    return reportUnavailable("set_volume");
  }
  player_->setVolume(static_cast<int>(std::min(percent, 100u)));
  emitVolume(player_->getVolume(), false);
}

void NativePlayer::seek(uint64_t positionMs) {
  if (!player_) {
    return;  // ignored when nothing is playing, per the contract
  }
  player_->seek(positionMs);
}

const pb::Source *
NativePlayer::sourceFor(const std::optional<StreamId> &streamId) const {
  if (!streamId) {
    return nullptr;
  }
  auto it = std::find_if(sources_.begin(), sources_.end(),
                         [&streamId](const TrackedSource &tracked) {
                           return tracked.streamId == *streamId;
                         });
  return it == sources_.end() ? nullptr : &it->source;
}

std::optional<std::string>
NativePlayer::tokenFor(const std::optional<StreamId> &streamId) const {
  const pb::Source *source = sourceFor(streamId);
  if (source == nullptr) {
    return std::nullopt;
  }
  return source->source_token();
}

std::optional<std::string> NativePlayer::currentToken() const {
  return tokenFor(currentId_);
}

void NativePlayer::forgetSourcesBefore(StreamId streamId) {
  std::erase_if(sources_, [streamId](const TrackedSource &tracked) {
    return tracked.streamId < streamId;
  });
}

void NativePlayer::onStreamState(const StreamState &state) {
  if (state.state == AudioGraphNodeState::SOURCE_CHANGED) {
    // A handover to something untracked is one already reported, or one
    // whose source was dropped.
    if (!state.streamId || sourceFor(state.streamId) == nullptr) {
      spdlog::debug("Ignoring a source change to an untracked stream");
      return;
    }
    if (currentId_ == state.streamId) {
      return;
    }
    std::optional<std::string> previous = currentToken();
    currentId_ = state.streamId;
    forgetSourcesBefore(*state.streamId);
    spdlog::info("Now playing source '{}' (stream {})", *currentToken(),
                 *currentId_);
    pb::Envelope env;
    pb::SourceChanged *changed = env.mutable_source_changed();
    changed->set_source_token(*currentToken());
    if (previous) {
      changed->set_previous_source_token(*previous);
    }
    changed->set_at_unix_ms(nowUnixMs());
    emit(env);
    return;
  }

  const std::optional<std::string> token = tokenFor(state.streamId);
  if (state.streamInfo && (!lastFormat_ || *state.streamInfo != *lastFormat_)) {
    lastFormat_ = state.streamInfo;
    pb::Envelope env;
    pb::AudioFormatChanged *changed = env.mutable_audio_format_changed();
    if (token) {
      changed->set_source_token(*token);
    }
    state_translator::fillFormat(*state.streamInfo, *changed->mutable_format());
    emit(env);
  }

  pb::Envelope env;
  state_translator::fillPlaybackStateChanged(
      state, token, nowUnixMs(), *env.mutable_playback_state_changed());
  emit(env);
}

void NativePlayer::emitVolume(const VolumeState &volume, bool external) {
  pb::Envelope env;
  pb::VolumeChanged *changed = env.mutable_volume_changed();
  state_translator::fillVolume(volume, *changed->mutable_volume());
  changed->set_external(external);
  emit(env);
}

void NativePlayer::emit(pb::Envelope &env) {
  if (sink_) {
    sink_(env);
  }
}

void NativePlayer::fillConfig(pb::ConfigSection &out) const {
  out.set_path("output");
  out.set_title("Output");

  pb::ConfigField *driver = out.add_fields();
  driver->set_path("output.driver");
  driver->set_title("Driver");
  driver->set_type(pb::CONFIG_FIELD_TYPE_ENUM);
  driver->set_value(settings_.at("output.driver"));
  driver->set_default_value("alsa");
  driver->set_apply(pb::APPLY_COST_RESTART_REQUIRED);
  pb::ConfigOption *alsa = driver->add_options();
  alsa->set_value("alsa");
  alsa->set_label("ALSA");

  pb::ConfigField *device = out.add_fields();
  device->set_path("output.device");
  device->set_title("Device");
  device->set_type(pb::CONFIG_FIELD_TYPE_ENUM);
  device->set_value(settings_.at("output.device"));
  device->set_default_value("default");
  device->set_apply(pb::APPLY_COST_INTERRUPTS_PLAYBACK);
  bool currentOffered = false;
  for (const AlsaPcmDevice &pcm : listAlsaPcmDevices()) {
    if (!pcm.ioid.empty() && pcm.ioid != "Output") {
      continue;
    }
    if (noisyPcm(pcm.name)) {
      continue;
    }
    pb::ConfigOption *option = device->add_options();
    option->set_value(pcm.name);
    option->set_label(pcm.label.empty() ? pcm.name : pcm.label);
    currentOffered = currentOffered || pcm.name == device->value();
  }
  if (!currentOffered) {
    // The configured device may be unplugged right now; it must stay
    // selectable or the page could not even show what is set.
    pb::ConfigOption *option = device->add_options();
    option->set_value(device->value());
    option->set_label(device->value() + " (not present)");
  }

  pb::ConfigField *mode = out.add_fields();
  mode->set_path("output.volume_mode");
  mode->set_title("Volume control");
  mode->set_description(
      "How volume is applied: the device mixer, software scaling, or fixed "
      "bit-perfect output");
  mode->set_type(pb::CONFIG_FIELD_TYPE_ENUM);
  mode->set_value(settings_.at("output.volume_mode"));
  mode->set_default_value("auto");
  mode->set_apply(pb::APPLY_COST_INSTANT);
  for (const char *value : {"auto", "hardware", "software", "fixed"}) {
    pb::ConfigOption *option = mode->add_options();
    option->set_value(value);
    option->set_label(value);
  }
}

bool NativePlayer::applyConfig(const std::string &path,
                               const std::string &value, std::string &error) {
  auto setting = settings_.find(path);
  if (setting == settings_.end()) {
    error = "unknown setting";
    return false;
  }
  if (setting->second == value) {
    return true;  // nothing to do, and nothing to interrupt
  }
  setting->second = value;
  persistOverrides();
  spdlog::info("Config {} = '{}'", path, value);
  if (path == "output.device") {
    rebuildPlayer();
  } else if (path == "output.volume_mode" && player_) {
    // A session override outranks the configured value until it ends.
    if (!sessionVolumeMode_) {
      player_->configureVolume(value, "");
      emitVolume(player_->getVolume(), false);
    }
  }
  return true;
}

const std::string &NativePlayer::effectiveVolumeMode() const {
  return sessionVolumeMode_ ? *sessionVolumeMode_
                            : settings_.at("output.volume_mode");
}

void NativePlayer::beginSessionVolume(const SessionVolume &volume) {
  if (!volume.mode.empty()) {
    const bool changed = volume.mode != effectiveVolumeMode();
    sessionVolumeMode_ = volume.mode;
    if (changed) {
      spdlog::info("Session volume mode: '{}' (configured '{}' is kept)",
                   volume.mode, settings_.at("output.volume_mode"));
      if (player_) {
        player_->configureVolume(volume.mode, "");
      }
    }
  }
  if (volume.percent) {
    setVolume(*volume.percent);
  }
}

void NativePlayer::endSessionVolume() {
  if (!sessionVolumeMode_) {
    return;
  }
  sessionVolumeMode_.reset();
  spdlog::info("Session volume mode ended; back to '{}'",
               settings_.at("output.volume_mode"));
  if (player_) {
    player_->configureVolume(settings_.at("output.volume_mode"), "");
  }
}

void NativePlayer::fillSnapshot(pb::StateSnapshot &out) const {
  out.set_captured_at_unix_ms(nowUnixMs());
  out.set_selected_device_id(settings_.at("output.device"));

  if (!player_) {
    out.set_playback_state(pb::PLAYBACK_STATE_ERROR);
    out.set_position_valid(false);
    out.mutable_volume()->set_supported(false);
    out.mutable_volume()->set_backend(pb::VOLUME_BACKEND_NONE);
    out.mutable_error()->set_source(pb::ERROR_SOURCE_AUDIO_OUTPUT);
    out.mutable_error()->set_message(
        "audio output is unavailable; check the configured device");
    return;
  }

  const StreamState state = player_->getState();
  const std::optional<StreamId> onAir =
      state.streamId ? state.streamId : currentId_;
  pb::PlaybackStateChanged translated;
  state_translator::fillPlaybackStateChanged(state, tokenFor(onAir),
                                             nowUnixMs(), translated);
  out.set_playback_state(translated.state());
  out.set_position_ms(translated.position_ms());
  out.set_position_valid(translated.position_valid());
  if (translated.has_error()) {
    *out.mutable_error() = translated.error();
  }
  if (const pb::Source *source = sourceFor(onAir)) {
    *out.mutable_current_source() = *source;
  }
  if (state.streamInfo) {
    state_translator::fillFormat(*state.streamInfo, *out.mutable_format());
  }
  state_translator::fillVolume(player_->getVolume(), *out.mutable_volume());
  for (const TrackedSource &tracked : sources_) {
    if (!onAir || tracked.streamId > *onAir) {
      out.add_queued_source_tokens(tracked.source.source_token());
    }
  }
}
