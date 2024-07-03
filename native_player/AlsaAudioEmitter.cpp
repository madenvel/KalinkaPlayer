#include "AlsaAudioEmitter.h"
#include "StreamState.h"

#include "StateMonitor.h"
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

  /* choose all parameters */
  err = snd_pcm_hw_params_any(pcm_handle, params);
  if (err < 0) {
    error << "Broken configuration for playback: no configurations available: "
          << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  /* set the interleaved read/write format */
  err = snd_pcm_hw_params_set_access(pcm_handle, params,
                                     SND_PCM_ACCESS_RW_INTERLEAVED);
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
  // /* set the buffer time */
  // err = snd_pcm_hw_params_set_buffer_time_near(pcm_handle, params,
  // &buffer_time,
  //                                              &dir);
  // if (err < 0) {
  //   printf("Unable to set buffer time %u for playback: %s\n", buffer_time,
  //          snd_strerror(err));
  //   return err;
  // }
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
  // err = snd_pcm_hw_params_get_period_size(params, &period_size, &dir);
  // if (err < 0) {
  //   printf("Unable to get period size for playback: %s\n",
  //   snd_strerror(err)); return err;
  // }
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
  /* start the transfer when the buffer is almost full: */
  /* (buffer_size / avail_min) * avail_min */
  err = snd_pcm_sw_params_set_start_threshold(
      handle, swparams, (buffer_size / period_size) * period_size);
  if (err < 0) {
    error << "Unable to set start threshold mode for playback: "
          << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  /* allow the transfer when at least period_size samples can be processed */
  /* or disable this mechanism when period event is enabled (aka interrupt like
   * style processing) */
  bool period_event = false;
  err = snd_pcm_sw_params_set_avail_min(
      handle, swparams, period_event ? buffer_size : period_size);
  if (err < 0) {
    error << "Unable to set avail min for playback: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }
  /* enable period events when requested */
  if (period_event) {
    err = snd_pcm_sw_params_set_period_event(handle, swparams, 1);
    if (err < 0) {
      error << "Unable to set period event: " << snd_strerror(err);
      throw std::runtime_error(error.str());
    }
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
      printf("Can't recovery from underrun, prepare failed: %s\n",
             snd_strerror(err));
    return 0;
  } else if (err == -ESTRPIPE) {
    while ((err = snd_pcm_resume(handle)) == -EAGAIN)
      sleep(1); /* wait until the suspend flag is released */
    if (err < 0) {
      err = snd_pcm_prepare(handle);
      if (err < 0)
        printf("Can't recovery from suspend, prepare failed: %s\n",
               snd_strerror(err));
    }
    return 0;
  }
  return err;
}

int wait_for_poll(snd_pcm_t *handle, struct pollfd *ufds, unsigned int count) {
  unsigned short revents = 0;

  while (1) {
    poll(ufds, count, -1);
    snd_pcm_poll_descriptors_revents(handle, ufds, count, &revents);
    if (revents & POLLERR)
      return -EIO;
    if (revents & POLLOUT)
      return 0;
  }
}
} // namespace

AlsaAudioEmitter::AlsaAudioEmitter(const std::string &deviceName,
                                   size_t bufferSize, size_t periodSize)
    : deviceName(deviceName), bufferSize(bufferSize), periodSize(periodSize) {

  /* Open the device */
  int err = snd_pcm_open((snd_pcm_t **)&pcmHandle, deviceName.c_str(),
                         SND_PCM_STREAM_PLAYBACK, 0);

  /* Error check */
  if (err < 0) {
    std::stringstream stream;
    stream << "Cannot open audio device " << deviceName << " ("
           << snd_strerror(err) << ")";
    pcmHandle = nullptr;
    throw std::runtime_error(stream.str());
  }
}

AlsaAudioEmitter::~AlsaAudioEmitter() {
  stop();
  if (pcmHandle != nullptr) {
    snd_pcm_close((snd_pcm_t *)pcmHandle);
  }
}

void AlsaAudioEmitter::connectTo(
    std::shared_ptr<AudioGraphOutputNode> outputNode) {
  if (inputNode != nullptr && inputNode != outputNode) {
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
  auto state = getState();
  auto stateToSet = state.state == AudioGraphNodeState::STREAMING
                        ? AudioGraphNodeState::PAUSED
                        : AudioGraphNodeState::STREAMING;

  if ((state.state == AudioGraphNodeState::STREAMING ||
       state.state == AudioGraphNodeState::PAUSED) &&
      state.state != stateToSet) {
    StreamState newState(stateToSet, 0, state.streamInfo);
    newState.position =
        framesPlayed * 1000 / currentStreamAudioFormat.sampleRate;
    setState(newState);
  }
}

void AlsaAudioEmitter::start() {
  if (inputNode == nullptr) {
    throw std::runtime_error("AlsaAudioEmitter must be connected to an "
                             "AudioGraphOutputNode node");
  }
  if (!playbackThread.joinable()) {
    playbackThread =
        std::jthread(std::bind_front(&AlsaAudioEmitter::workerThread, this));
  }
}

void AlsaAudioEmitter::stop() {
  if (playbackThread.joinable() &&
      !playbackThread.get_stop_source().stop_requested()) {
    playbackThread.request_stop();
    playbackThread.join();
    playbackThread = std::jthread();
    setState(StreamState(AudioGraphNodeState::STOPPED));
  }
}

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
      break;
    default:
      setState(StreamState(AudioGraphNodeState::STOPPED));
      break;
    }
    StateChangeWaitLock lock(token, *inputNode, inputNodeState.timestamp);
    inputNodeState = lock.state();
  };

  return inputNodeState;
}

void AlsaAudioEmitter::workerThread(std::stop_token token) {
  if (inputNode == nullptr) {
    return;
  }

  while (!token.stop_requested()) {
    auto inputNodeState = waitForInputToBeReady(token);

    if (token.stop_requested()) {
      break;
    }

    auto streamInfo = inputNode->getState().streamInfo;
    if (!streamInfo.has_value()) {
      setState({AudioGraphNodeState::ERROR, "No format information available"});
      continue;
    }
    if (streamInfo.value().format != currentStreamAudioFormat &&
        inputNodeState.state != AudioGraphNodeState::PREPARING) {
      setState(StreamState(AudioGraphNodeState::PREPARING));
    }

    setupAudioFormat(streamInfo.value().format);
    const size_t dataToRequest =
        snd_pcm_frames_to_bytes(pcmHandle, bufferSize / 2);
    size_t dataAvailable = inputNode->waitForData(token, dataToRequest);
    if (dataAvailable < dataToRequest) {
      std::cerr << "Not enough data available for playback - requested "
                << dataToRequest << " received " << dataAvailable
                << " - expect XRUN" << std::endl;
    }
    streamInfo.value().format = currentStreamAudioFormat;
    setState({AudioGraphNodeState::STREAMING, 0, streamInfo.value()});

    std::optional<std::chrono::steady_clock::time_point> setStreamingStateAfter;
    bool newAudioFormat = false;
    framesPlayed = 0;

    while (!token.stop_requested()) {
      // Update streaming state
      if (setStreamingStateAfter.has_value() &&
          std::chrono::steady_clock::now() >= setStreamingStateAfter.value()) {
        setState(StreamState(AudioGraphNodeState::SOURCE_CHANGED));

        auto streamInfo = inputNode->getState().streamInfo;
        streamInfo.value().format = currentStreamAudioFormat;

        setState({AudioGraphNodeState::STREAMING,
                  std::chrono::duration_cast<std::chrono::milliseconds>(
                      std::chrono::steady_clock::now() -
                      setStreamingStateAfter.value())
                      .count(),
                  streamInfo});
        setStreamingStateAfter.reset();
      }

      int err = wait_for_poll((snd_pcm_t *)pcmHandle, ufds.data(), ufds.size());
      if (err < 0) {
        throw std::runtime_error("Poll failed for playback");
      }
      snd_pcm_sframes_t frames = snd_pcm_avail_update((snd_pcm_t *)pcmHandle);
      if (frames < 0) {
        throw std::runtime_error("Can't get avail update for playback: " +
                                 std::string(snd_strerror(frames)));
      }

      if (static_cast<unsigned long>(frames) < periodSize) {
        continue;
      }
      size_t framesToRead =
          std::min(snd_pcm_bytes_to_frames(pcmHandle, buffer.size()), frames);
      size_t bytesToRead = snd_pcm_frames_to_bytes(pcmHandle, framesToRead);

      if (getState().state == AudioGraphNodeState::PAUSED) {
        memset(buffer.data(), 0, bytesToRead);
        err = snd_pcm_writei(pcmHandle, buffer.data(), framesToRead);
        if (err == -EAGAIN) {
          continue;
        }
        if (err < 0) {
          std::cout << "XRUN" << std::endl;
          if (xrun_recovery(pcmHandle, err) < 0) {
            printf("Write error: %s\n", snd_strerror(err));
            break;
          }
        }
        continue;
      }

      size_t read = inputNode->read(buffer.data(), bytesToRead);
      framesPlayed += snd_pcm_bytes_to_frames(pcmHandle, read);

      inputNodeState = inputNode->getState();
      newAudioFormat = false;

      if (inputNodeState.state == AudioGraphNodeState::SOURCE_CHANGED) {
        // Request new state to get new stream info
        // It should only be present if the state is STREAMING
        inputNode->acceptSourceChange();
        framesPlayed = 0;
        inputNodeState = inputNode->getState();
        auto newStreamInfo = inputNodeState.streamInfo;

        if (inputNodeState.state != AudioGraphNodeState::STREAMING ||
            !newStreamInfo.has_value() ||
            newStreamInfo.value().format != currentStreamAudioFormat) {
          newAudioFormat = true;
        } else if (read < bytesToRead) {
          int timeToReportPosition =
              1000 * framesToRead / currentStreamAudioFormat.sampleRate;
          setStreamingStateAfter =
              std::chrono::steady_clock::now() +
              std::chrono::milliseconds(timeToReportPosition);
          auto moreBytes =
              inputNode->read(buffer.data() + read, bytesToRead - read);
          framesPlayed += snd_pcm_bytes_to_frames(pcmHandle, moreBytes);
          read += moreBytes;
        }
      }

      if (read < bytesToRead) {
        if (inputNodeState.state != AudioGraphNodeState::FINISHED &&
            !newAudioFormat) {
          std::cerr << "Possible XRUN - read " << read << " but expected "
                    << bytesToRead << std::endl;
        }
        memset(buffer.data() + read, 0, bytesToRead - read);
      }

      err = snd_pcm_writei((snd_pcm_t *)pcmHandle, buffer.data(), framesToRead);
      if (err == -EAGAIN) {
        continue;
      }
      if (err < 0) {
        std::cout << "XRUN" << std::endl;
        if (xrun_recovery((snd_pcm_t *)pcmHandle, err) < 0) {
          printf("Write error: %s\n", snd_strerror(err));
          break;
        }
      }

      if (newAudioFormat ||
          inputNodeState.state == AudioGraphNodeState::FINISHED ||
          inputNodeState.state == AudioGraphNodeState::ERROR) {
        break;
      }
    }

    if (token.stop_requested()) {
      snd_pcm_drop(pcmHandle);
    } else {
      snd_pcm_drain(pcmHandle);
      if (newAudioFormat) {
        setState(StreamState(AudioGraphNodeState::SOURCE_CHANGED));
      }
    }
  }
}

void AlsaAudioEmitter::setupAudioFormat(
    const StreamAudioFormat &streamAudioFormat) {

  if (streamAudioFormat == currentStreamAudioFormat) {
    snd_pcm_prepare(pcmHandle);
    return;
  }

  int err = 0;

  snd_pcm_format_t format = getFormat(streamAudioFormat.bitsPerSample);
  unsigned sampleRate = streamAudioFormat.sampleRate;

  snd_pcm_hw_params_t *hw_params;
  snd_pcm_hw_params_malloc(&hw_params);
  try {
    init_hwparams(pcmHandle, hw_params, sampleRate, bufferSize, periodSize,
                  format);
  } catch (const std::exception &ex) {
    snd_pcm_hw_params_free(hw_params);
    throw ex;
  }
  snd_pcm_hw_params_free(hw_params);

  if (err < 0) {
    std::stringstream error;
    error << "Unable to set hw params for playback: " << snd_strerror(err);
    throw std::runtime_error(error.str());
  }

  snd_pcm_sw_params_t *sw_params;
  snd_pcm_sw_params_malloc(&sw_params);
  try {
    set_swparams(pcmHandle, sw_params, bufferSize, periodSize);
  } catch (const std::exception &ex) {
    snd_pcm_sw_params_free(sw_params);
    throw ex;
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

  buffer.resize(snd_pcm_frames_to_bytes(pcmHandle, periodSize));

  // Hack for Raspberry Pi HiFiBerry
  // Sleep to make sure RPi is ready to play
  std::this_thread::sleep_for(std::chrono::milliseconds(500));
}