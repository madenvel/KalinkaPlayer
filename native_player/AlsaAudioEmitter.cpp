#include "AlsaAudioEmitter.h"
#include "Log.h"
#include "StateMonitor.h"
#include "StreamState.h"
#include "Utils.h"

#include <iostream>
#include <sstream>
#include <stdexcept>

namespace {
snd_pcm_format_t getFormat(int bitsPerSample) {
  return bitsPerSample == 16 ? SND_PCM_FORMAT_S16_LE : SND_PCM_FORMAT_S24_LE;
}

void init_hwparams(snd_pcm_t *pcm_handle, snd_pcm_hw_params_t *params,
                   unsigned int &rate, snd_pcm_uframes_t &buffer_size,
                   snd_pcm_uframes_t &period_size, snd_pcm_format_t format) {

  unsigned int rrate;
  int err, dir = 0;
  std::stringstream error;

  snd_pcm_hw_free(pcm_handle);

  /* choose all parameters */
  err = snd_pcm_hw_params_any(pcm_handle, params);
  if (err < 0) {
    error << "Broken configuration for playback: no configurations available: "
          << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  /* set the interleaved read/write format */
  err = snd_pcm_hw_params_set_access(pcm_handle, params,
                                     SND_PCM_ACCESS_MMAP_INTERLEAVED);
  if (err < 0) {
    error << "Access type not available for playback: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  /* set the sample format */
  err = snd_pcm_hw_params_set_format(pcm_handle, params, format);
  if (err < 0) {
    error << "Sample format not available for playback: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  /* set the count of channels */
  err = snd_pcm_hw_params_set_channels(pcm_handle, params, 2);
  if (err < 0) {
    error << "Channels count (" << 2
          << ") not available for playbacks: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  // Enabled resampling
  err = snd_pcm_hw_params_set_rate_resample(pcm_handle, params, 1);
  if (err < 0) {
    error << "Resampling setup failed for playback: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  /* set the stream rate */
  rrate = rate;
  err = snd_pcm_hw_params_set_rate_near(pcm_handle, params, &rrate, 0);
  if (err < 0) {
    error << "Rate " << rate
          << "Hz not available for playback: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  if (rrate != rate) {
    error << "Rate doesn't match (requested " << rate << "Hz, get " << rrate
          << "Hz)";
    // Comment this out to be able to debug on a machine without resampling
    // throw std::runtime_error(error.str());
  }

  rate = rrate;
  err =
      snd_pcm_hw_params_set_buffer_size_near(pcm_handle, params, &buffer_size);
  if (err < 0) {
    error << "Unable to get buffer size for playback: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  // /* set the period size */
  err = snd_pcm_hw_params_set_period_size_near(pcm_handle, params, &period_size,
                                               &dir);
  if (err < 0) {
    error << "Unable to set period time " << period_size
          << " for playback: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  /* write the parameters to device */
  err = snd_pcm_hw_params(pcm_handle, params);
  if (err < 0) {
    error << "Unable to set hw params for playback: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
}

void set_swparams(snd_pcm_t *handle, snd_pcm_sw_params_t *swparams,
                  snd_pcm_uframes_t buffer_size,
                  snd_pcm_uframes_t period_size) {
  int err;
  std::stringstream error;

  /* get the current swparams */
  err = snd_pcm_sw_params_current(handle, swparams);
  if (err < 0) {
    error << "Unable to determine current swparams for playback: "
          << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  /*
   * Allow the transfer when at least period_size samples can be
   * processed. This will make the main loop to wake up whenever
   * there's enough space for at least a perio of samples.
   */
  err = snd_pcm_sw_params_set_avail_min(handle, swparams, period_size);
  if (err < 0) {
    error << "Unable to set avail min: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }

  /* write the parameters to the playback device */
  err = snd_pcm_sw_params(handle, swparams);
  if (err < 0) {
    error << "Unable to set sw params for playback: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
}

int xrun_recovery(snd_pcm_t *handle, int err) {
  if (err == -EPIPE) { /* under-run */
    err = snd_pcm_prepare(handle);
    if (err < 0)
      spdlog::error("Can't recovery from underrun, prepare failed: %s\n",
                    snd_strerror(err));
    return 0;
  } else if (err == -ESTRPIPE) {
    while ((err = snd_pcm_resume(handle)) == -EAGAIN)
      sleep(1); /* wait until the suspend flag is released */
    if (err < 0) {
      err = snd_pcm_prepare(handle);
      if (err < 0) {
        spdlog::error("Can't recovery from suspend, prepare failed: %s\n",
                      snd_strerror(err));
      }
    }
    return 0;
  }
  return err;
}
} // namespace

AlsaAudioEmitter::AlsaAudioEmitter(const std::string &deviceName,
                                   size_t bufferSize, size_t periodSize,
                                   size_t sleepAfterFormatSetupMs)
    : deviceName(deviceName), requestedBufferSize(bufferSize),
      requestedPeriodSize(periodSize),
      sleepAfterFormatSetupMs(sleepAfterFormatSetupMs) {}

AlsaAudioEmitter::~AlsaAudioEmitter() { stop(); }

void AlsaAudioEmitter::connectTo(
    std::shared_ptr<AudioGraphOutputNode> outputNode) {
  if (outputNode == nullptr) {
    throw std::runtime_error("Input node cannot be nullptr");
  }
  if (inputNode == outputNode) {
    return;
  }

  if (inputNode != nullptr) {
    throw std::runtime_error("AlsaAudioEmitter is already connected to an "
                             "AudioGraphOutputNode node");
  }

  inputNode = outputNode;
  start();
}

void AlsaAudioEmitter::disconnect(
    std::shared_ptr<AudioGraphOutputNode> outputNode) {
  if (inputNode == outputNode) {
    stop();
    inputNode = nullptr;
  }
}

void AlsaAudioEmitter::pause(bool paused) {
  if (playbackThread.joinable() && isWorkerRunning) {
    pauseRequestSignal.sendValue(paused);
    pauseRequestSignal.getResponse(playbackThread.get_stop_token());
  }
}

size_t AlsaAudioEmitter::seek(size_t positionMs) {
  if (playbackThread.joinable() && isWorkerRunning &&
      !seekRequestSignal.getValue()) {
    seekRequestSignal.sendValue(positionMs);
    return seekRequestSignal.getResponse(playbackThread.get_stop_token());
  }

  return -1;
}

void AlsaAudioEmitter::start() {
  if (inputNode == nullptr) {
    throw std::runtime_error("AlsaAudioEmitter must be connected to an "
                             "AudioGraphOutputNode node");
  }

  if (playbackThread.joinable()) {
    playbackThread.request_stop();
    playbackThread.join();
  }
  playbackThread =
      std::jthread(std::bind_front(&AlsaAudioEmitter::workerThread, this));
}

void AlsaAudioEmitter::stop() {
  if (playbackThread.joinable()) {
    playbackThread.request_stop();
    playbackThread.join();
    playbackThread = std::jthread();
    setState(StreamState(AudioGraphNodeState::STOPPED));
  }
}

#if 0
void AlsaAudioEmitter::setState(const StreamState &newState) {
  spdlog::info("Setting state to {}", stateToString(newState.state));

  AudioGraphNode::setState(newState);
}
#endif

StreamState AlsaAudioEmitter::waitForInputToBeReady(std::stop_token token) {
  StreamState inputNodeState = inputNode->getState();
  while (!token.stop_requested()) {
    switch (inputNodeState.state) {
    case AudioGraphNodeState::PREPARING:
      setState(StreamState(AudioGraphNodeState::PREPARING));
      break;
    case AudioGraphNodeState::STREAMING:
      return inputNodeState;
    case AudioGraphNodeState::FINISHED:
      setState(StreamState(AudioGraphNodeState::FINISHED));
      break;
    case AudioGraphNodeState::ERROR:
      setState({AudioGraphNodeState::ERROR, inputNodeState.message});
      break;
    case AudioGraphNodeState::SOURCE_CHANGED:
      setState(StreamState(AudioGraphNodeState::SOURCE_CHANGED));
      inputNode->acceptSourceChange();
      currentSourceTotalFramesWritten = 0;
      break;
    default:
      setState(StreamState(AudioGraphNodeState::STOPPED));
      break;
    }
    if (seekRequestSignal.getValue()) {
      handleSeekSignal();
    }

    if (pauseRequestSignal.getValue()) {
      pauseRequestSignal.respond(false);
    }

    auto combinedToken =
        combineStopTokens(token, seekRequestSignal.getStopToken(),
                          pauseRequestSignal.getStopToken());
    StateChangeWaitLock lock(combinedToken.get_token(), *inputNode,
                             inputNodeState.timestamp);
    inputNodeState = lock.state();
  };

  return inputNodeState;
}

bool AlsaAudioEmitter::handleSeekSignal() {
  auto positionMs = *seekRequestSignal.getValue();
  auto seekValue = positionMs * currentStreamAudioFormat.sampleRate / 1000;
  spdlog::info("Request seek to {}ms ({} frames)", positionMs, seekValue);
  auto retVal = inputNode->seekTo(seekValue);
  if (retVal == -1U) {
    spdlog::warn("Seek request failed: requested={}", seekValue);
    seekRequestSignal.respond(-1);
    return false;
  }
  seekRequestSignal.respond(framesToTimeMs(retVal).count());
  seekHappened = true;
  return true;
}

bool AlsaAudioEmitter::handlePauseSignal(bool paused) {
  auto state = getState();
  auto stateToSet =
      paused ? AudioGraphNodeState::PAUSED : AudioGraphNodeState::STREAMING;

  this->paused = paused;
  StreamState newState(stateToSet, 0, state.streamInfo);
  newState.position = framesToTimeMs(currentSourceTotalFramesWritten).count();
  snd_pcm_sframes_t delay = 0;
  int err = snd_pcm_delay(pcmHandle, &delay);
  if (err < 0) {
    spdlog::error("Error when calling snd_pcm_delay: {}", snd_strerror(err));
    return false;
  }
  playedFramesCounter.callOnOrAfterFrame(
      delay,
      [this, newState](snd_pcm_sframes_t frames) { setState(newState); });

  return true;
}

void AlsaAudioEmitter::openDevice() {
  /* Open the device */
  if (pcmHandle != nullptr) {
    throw std::runtime_error(
        "Error while opening PCM device - the device might already be open");
  }
  int err =
      snd_pcm_open(&pcmHandle, deviceName.c_str(), SND_PCM_STREAM_PLAYBACK, 0);

  /* Error check */
  if (err < 0) {
    std::stringstream stream;
    stream << "Cannot open audio device " << deviceName << " ("
           << snd_strerror(err) << ")";
    pcmHandle = nullptr;
    spdlog::error(stream.str());
    setState({AudioGraphNodeState::ERROR, stream.str()});

    throw std::runtime_error(stream.str());
  }

  currentSourceTotalFramesWritten = 0;
  playedFramesCounter.reset();
}

void AlsaAudioEmitter::closeDevice() {
  if (pcmHandle != nullptr) {
    snd_pcm_drop(pcmHandle);
    snd_pcm_hw_free(pcmHandle);
    snd_pcm_close(pcmHandle);
    pcmHandle = nullptr;
    currentStreamAudioFormat = StreamAudioFormat();
  }
}

snd_pcm_sframes_t AlsaAudioEmitter::waitForAlsaBufferSpace() {
  auto getAvailableFrames = [this]() -> snd_pcm_sframes_t {
    snd_pcm_sframes_t frames = snd_pcm_avail_update(pcmHandle);
    if (frames < 0) {
      spdlog::error("Can't get avail update for playback: {}",
                    snd_strerror(frames));
      throw std::runtime_error("Can't get avail update for playback: " +
                               std::string(snd_strerror(frames)));
    }
    return frames;
  };

  snd_pcm_sframes_t frames = getAvailableFrames();
  unsigned short revents = 0;

  while (!playbackThread.get_stop_token().stop_requested() &&
         static_cast<snd_pcm_uframes_t>(frames) < periodSize) {
    int ret = poll(ufds.data(), ufds.size(), pollTimeout.count());
    if (ret == 0) {
      continue;
    }
    frames = getAvailableFrames();
    snd_pcm_poll_descriptors_revents(pcmHandle, ufds.data(), ufds.size(),
                                     &revents);
    if (revents & POLLERR) {
      throw std::runtime_error("Poll error");
    }
    if (revents & POLLOUT) {
      break;
    }
  }
  return frames;
}

bool AlsaAudioEmitter::hasInputSourceStateChanged() {
  auto inputNodeState = inputNode->getState();
  if (inputNodeState.state == AudioGraphNodeState::SOURCE_CHANGED) {
    inputNode->acceptSourceChange();
    inputNodeState = inputNode->getState();
    currentSourceTotalFramesWritten = 0;

    auto newStreamInfo = inputNodeState.streamInfo;
    if (inputNodeState.state != AudioGraphNodeState::STREAMING ||
        !newStreamInfo.has_value() ||
        newStreamInfo.value().format != currentStreamAudioFormat) {
      spdlog::info(
          "Source changed - not streaming or different format, draining");
      drainPcm();
      setState(StreamState(AudioGraphNodeState::SOURCE_CHANGED));
      return true;
    } else {
      snd_pcm_sframes_t framesDelay = 0;
      int err = snd_pcm_delay(pcmHandle, &framesDelay);
      if (err < 0) {
        spdlog::error("Error when calling snd_pcm_delay: {}",
                      snd_strerror(err));
      }

      spdlog::debug("Reporting source change in {}ms, frames={}",
                    framesToTimeMs(framesDelay).count(), framesDelay);

      playedFramesCounter.callOnOrAfterFrame(
          framesDelay,
          [this, streamInfo = newStreamInfo](snd_pcm_sframes_t frames) {
            setState(StreamState(AudioGraphNodeState::SOURCE_CHANGED));
            setState(StreamState{AudioGraphNodeState::STREAMING,
                                 framesToTimeMs(frames).count(), streamInfo});
          });
    }
  } else if (inputNodeState.state == AudioGraphNodeState::FINISHED ||
             inputNodeState.state == AudioGraphNodeState::ERROR) {
    spdlog::info("Source finished, state={}",
                 stateToString(inputNodeState.state));
    drainPcm();
    return true;
  }

  return false;
}

snd_pcm_sframes_t AlsaAudioEmitter::writeToAlsa(
    snd_pcm_uframes_t framesToWrite,
    std::function<snd_pcm_sframes_t(void *ptr, snd_pcm_uframes_t frames,
                                    size_t bytes)>
        func) {
  const snd_pcm_channel_area_t *my_areas = nullptr;
  snd_pcm_uframes_t offset = 0, frames = framesToWrite;
  int err = snd_pcm_mmap_begin(pcmHandle, &my_areas, &offset, &frames);
  if (err < 0) {
    if (xrun_recovery(pcmHandle, err) < 0) {
      throw std::runtime_error("Error in mmap begin: " +
                               std::string(snd_strerror(err)));
    }
  }

  void *ptr = static_cast<uint8_t *>(my_areas[0].addr) +
              (my_areas[0].first / 8) +
              snd_pcm_frames_to_bytes(pcmHandle, offset);

  frames = func(ptr, frames, snd_pcm_frames_to_bytes(pcmHandle, frames));

  err = snd_pcm_mmap_commit(pcmHandle, offset, frames);
  if (static_cast<snd_pcm_uframes_t>(err) != frames || err < 0) {
    if (xrun_recovery(pcmHandle, err) < 0) {
      throw std::runtime_error("Error in mmap commit: " +
                               std::string(snd_strerror(err)));
    }
  }

  return frames;
}

snd_pcm_sframes_t
AlsaAudioEmitter::readIntoAlsaFromStream(std::stop_token stopToken,
                                         snd_pcm_sframes_t framesToRead) {
  snd_pcm_sframes_t framesRead = 0;

  if (snd_pcm_state(pcmHandle) == SND_PCM_STATE_RUNNING) {
    playedFramesCounter.update(framesToRead);
  }

  while (framesRead < framesToRead) {
    snd_pcm_uframes_t frames = framesToRead - framesRead;
    if (paused) {

      framesRead += writeToAlsa(
          frames, [](void *ptr, snd_pcm_uframes_t frames, size_t bytes) {
            memset(ptr, 0, bytes);
            return frames;
          });

    } else {
      auto actualFrames = writeToAlsa(
          frames, [this](void *ptr, snd_pcm_uframes_t frames, size_t bytes) {
            return snd_pcm_bytes_to_frames(pcmHandle,
                                           inputNode->read(ptr, bytes));
          });

      framesRead += actualFrames;
      currentSourceTotalFramesWritten += actualFrames;

      if (hasInputSourceStateChanged()) {
        return -1;
      }

      if (static_cast<snd_pcm_uframes_t>(actualFrames) < frames) {
        auto bytesAvailable =
            waitForInputData(stopToken, frames - actualFrames);
        if (bytesAvailable == 0) {
          drainPcm();
          return -1;
        }
      }
    }
  }
  return framesRead;
}

size_t AlsaAudioEmitter::waitForInputData(std::stop_token stopToken,
                                          snd_pcm_uframes_t frames) {
  if (snd_pcm_state(pcmHandle) == SND_PCM_STATE_RUNNING) {
    snd_pcm_sframes_t delayFrames = 0;
    snd_pcm_delay(pcmHandle, &delayFrames);
    auto timeout = framesToTimeMs(std::max(
        0l, delayFrames - static_cast<snd_pcm_sframes_t>(2 * periodSize)));
    return inputNode->waitForDataFor(
        stopToken, timeout, snd_pcm_frames_to_bytes(pcmHandle, frames));
  }

  return inputNode->waitForData(stopToken,
                                snd_pcm_frames_to_bytes(pcmHandle, frames));
}

std::chrono::milliseconds
AlsaAudioEmitter::framesToTimeMs(snd_pcm_sframes_t frames) {
  return std::chrono::milliseconds(1000 * frames /
                                   currentStreamAudioFormat.sampleRate);
}

void AlsaAudioEmitter::startPcmStream(const StreamInfo &streamInfo,
                                      snd_pcm_uframes_t position) {
  spdlog::info("Starting playback");
  int err = snd_pcm_start(pcmHandle);
  if (err < 0) {
    throw std::runtime_error(std::string("Can't start PCM: ") +
                             snd_strerror(err));
  }
  setState({AudioGraphNodeState::STREAMING, framesToTimeMs(position).count(),
            streamInfo});
}

void AlsaAudioEmitter::drainPcm() {
  auto drainSequence = playedFramesCounter.drainSequence();
  if (!drainSequence.empty()) {
    snd_pcm_sframes_t prevFrames = 0;
    for (auto frames : drainSequence) {
      std::this_thread::sleep_for(framesToTimeMs(frames - prevFrames));
      playedFramesCounter.update(frames - prevFrames);
      prevFrames = frames;
    }
  }
  if (paused || seekHappened) {
    snd_pcm_drop(pcmHandle);
  } else {
    snd_pcm_drain(pcmHandle);
  }
}

void AlsaAudioEmitter::workerThread(std::stop_token token) {
  if (inputNode == nullptr) {
    return;
  }
  isWorkerRunning = true;
  try {
    openDevice();

    while (!token.stop_requested()) {
      auto inputNodeState = waitForInputToBeReady(token);

      if (token.stop_requested()) {
        break;
      }

      auto streamInfo = inputNodeState.streamInfo;
      if (!streamInfo.has_value()) {
        throw std::runtime_error("No stream information available");
      }

      setState(StreamState(AudioGraphNodeState::PREPARING));
      setupAudioFormat(streamInfo.value().format);
      streamInfo.value().format = currentStreamAudioFormat;

      bool started = false;
      if (seekHappened) {
        currentSourceTotalFramesWritten = inputNodeState.position;
        seekHappened = false;
      }

      snd_pcm_uframes_t streamStartPosition = currentSourceTotalFramesWritten;
      paused = false;

      while (!token.stop_requested()) {
        auto framesToRead = waitForAlsaBufferSpace();
        if (!framesToRead) {
          break;
        }

        auto combinedToken =
            combineStopTokens(token, seekRequestSignal.getStopToken(),
                              pauseRequestSignal.getStopToken());

        auto framesRead =
            readIntoAlsaFromStream(combinedToken.get_token(), framesToRead);
        if (framesRead < 0) {
          break;
        }

        if (pauseRequestSignal.getValue()) {
          if (paused != *pauseRequestSignal.getValue()) {
            bool success = handlePauseSignal(*pauseRequestSignal.getValue());
            pauseRequestSignal.respond(success ? *pauseRequestSignal.getValue()
                                               : paused);
          } else {
            pauseRequestSignal.respond(paused);
          }
          continue;
        }

        if (seekRequestSignal.getValue()) {
          setState(StreamState{AudioGraphNodeState::PREPARING});
          if (!handleSeekSignal()) {
            continue;
          }

          drainPcm();
          break;
        }

        if (!started) {
          startPcmStream(streamInfo.value(), streamStartPosition);
          started = true;
        }
      }

      if (token.stop_requested()) {
        snd_pcm_drop(pcmHandle);
      }
    }
  } catch (const std::exception &ex) {
    spdlog::error("Error in AlsaAudioEmitter::workerThread: {}", ex.what());
    setState({AudioGraphNodeState::ERROR,
              "Internal error: " + std::string(ex.what())});
  }

  closeDevice();
  inputNode = nullptr;
  isWorkerRunning = false;
}

void AlsaAudioEmitter::setupAudioFormat(
    const StreamAudioFormat &streamAudioFormat) {

  spdlog::debug("Setting up audio format: {}", streamAudioFormat.toString());
  if (streamAudioFormat == currentStreamAudioFormat) {
    spdlog::debug("Audio format is already set up - ignoring");
    snd_pcm_prepare(pcmHandle);
    return;
  }

  int err = 0;

  snd_pcm_format_t format = getFormat(streamAudioFormat.bitsPerSample);
  unsigned sampleRate = streamAudioFormat.sampleRate;
  bufferSize = requestedBufferSize;
  periodSize = requestedPeriodSize;

  snd_pcm_hw_params_t *hw_params;
  snd_pcm_hw_params_malloc(&hw_params);
  try {
    init_hwparams(pcmHandle, hw_params, sampleRate, bufferSize, periodSize,
                  format);
  } catch (const std::exception &ex) {
    snd_pcm_hw_params_free(hw_params);
    throw;
  }
  snd_pcm_hw_params_free(hw_params);

  if (err < 0) {
    std::stringstream error;
    error << "Unable to set hw params for playback: " << snd_strerror(err);
    spdlog::error(error.str());
    throw std::runtime_error(error.str());
  }

  snd_pcm_sw_params_t *sw_params;
  snd_pcm_sw_params_malloc(&sw_params);
  try {
    set_swparams(pcmHandle, sw_params, bufferSize, periodSize);
  } catch (const std::exception &ex) {
    snd_pcm_sw_params_free(sw_params);
    throw;
  }
  snd_pcm_sw_params_free(sw_params);

  currentStreamAudioFormat = streamAudioFormat;

  int count = snd_pcm_poll_descriptors_count((snd_pcm_t *)pcmHandle);
  if (count <= 0) {
    throw std::runtime_error("Invalid poll descriptors count");
  }

  ufds.resize(count);

  if ((err = snd_pcm_poll_descriptors(pcmHandle, ufds.data(), count)) < 0) {
    throw std::runtime_error(
        "Unable to obtain poll descriptors for playback: " +
        std::string(snd_strerror(err)));
  }

  pollTimeout = std::chrono::milliseconds(bufferSize * 1000 / sampleRate);

  // Hack for HiFiBerry boards on Raspberry Pi
  // Sleep to make sure RPi is ready to play.
  // I2S sync mechanism doesn't work properly
  // wich results in the first ~500 ms of the track being cut off.
  if (sleepAfterFormatSetupMs) {
    std::this_thread::sleep_for(
        std::chrono::milliseconds(sleepAfterFormatSetupMs));
  }
}

void PlayedFramesCounter::update(snd_pcm_sframes_t frames) {
  lastPlayedFrames += frames;
  for (auto it = onFramesPlayedCallbacks.begin();
       it != onFramesPlayedCallbacks.end();) {
    if (lastPlayedFrames >= it->first) {
      it->second(lastPlayedFrames - it->first);
      it = onFramesPlayedCallbacks.erase(it);
    } else {
      ++it;
    }
  }
}

std::vector<snd_pcm_sframes_t> PlayedFramesCounter::drainSequence() {
  std::vector<snd_pcm_sframes_t> sequence;
  for (auto &callback : onFramesPlayedCallbacks) {
    sequence.push_back(callback.first - lastPlayedFrames);
  }

  std::sort(sequence.begin(), sequence.end());
  return sequence;
}

void PlayedFramesCounter::callOnOrAfterFrame(
    snd_pcm_sframes_t frame,
    std::function<void(snd_pcm_sframes_t)> onFramesPlayed) {
  onFramesPlayedCallbacks.push_back({frame + lastPlayedFrames, onFramesPlayed});
}

void PlayedFramesCounter::reset() {
  onFramesPlayedCallbacks.clear();
  lastPlayedFrames = 0;
}
