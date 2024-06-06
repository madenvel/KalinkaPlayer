#include <alsa/asoundlib.h>

#include <cstddef>
#include <cstdio>

#include <vector>

#include <algorithm>
#include <chrono>
#include <iostream>
#include <math.h>

#include <sstream>

#include "AlsaPlayNode.h"
#include "StateMachine.h"

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

void AlsaPlayNode::start() {
  if (!playThread.joinable()) {
    playThread =
        std::jthread(std::bind_front(&AlsaPlayNode::workerThread, this));
  }
}

void AlsaPlayNode::pause(bool paused_) { paused = paused_; }

void AlsaPlayNode::connectIn(std::shared_ptr<ProcessNode> inputNode) {
  in = inputNode;
}

void AlsaPlayNode::stop() {
  if (playThread.joinable() && !playThread.get_stop_source().stop_requested()) {
    playThread.request_stop();
    playThread.join();
    playThread = std::jthread();
  }
}

int AlsaPlayNode::workerThread(std::stop_token token) {
  int err = 0;

  try {
    if (alsaDevice.expired()) {
      throw std::runtime_error("Alsa device is expired");
    }
    auto alsa = alsaDevice.lock();
    snd_pcm_t *pcm_handle = static_cast<snd_pcm_t *>(
        alsa->getHandle(sm->lastState().audioInfo.sampleRate,
                        sm->lastState().audioInfo.bitsPerSample));
    snd_pcm_format_t format = getFormat(alsa->getCurrentBitsPerSample());
    int count = snd_pcm_poll_descriptors_count(pcm_handle);
    if (count <= 0) {
      printf("Invalid poll descriptors count\n");
      return count;
    }

    pollfd *ufds = new pollfd[count];

    if ((err = snd_pcm_poll_descriptors(pcm_handle, ufds, count)) < 0) {
      printf("Unable to obtain poll descriptors for playback: %s\n",
             snd_strerror(err));
      return err;
    }

    int pbits = snd_pcm_format_physical_width(format);
    int pbytes = pbits / 8;

    const auto framesToBytes = [pbytes](size_t frames) -> size_t {
      return frames * pbytes * 2;
    };

    const auto bytesToFrames = [pbytes](size_t bytes) -> size_t {
      return bytes / (pbytes * 2);
    };

    snd_pcm_uframes_t buffer_size;
    snd_pcm_uframes_t period_size;

    err = snd_pcm_get_params(pcm_handle, &buffer_size, &period_size);
    if (err < 0) {
      throw std::runtime_error("Unable to get params for playback: " +
                               std::string(snd_strerror(err)));
    }

    std::vector<uint8_t> data(2 * period_size * pbytes);

    bool prevPaused = paused = false;

    size_t framesCount = 0;
    bool eof = false;
    bool error = false;
    sm->updateState(State::PLAYING, 0);

    while (!token.stop_requested()) {
      err = wait_for_poll(pcm_handle, ufds, count);
      if (err < 0) {
        throw std::runtime_error("Poll failed for playback");
      }
      snd_pcm_sframes_t frames = snd_pcm_avail_update(pcm_handle);
      if (frames < 0) {
        throw std::runtime_error("Can't get avail update for playback: " +
                                 std::string(snd_strerror(frames)));
      }
      if (static_cast<unsigned long>(frames) < period_size) {
        continue;
      }
      size_t framesToRead = std::min((long)bytesToFrames(data.size()), frames);
      size_t bytesToRead = framesToBytes(framesToRead);

      if (prevPaused != paused) {
        sm->updateState(paused ? State::PAUSED : State::PLAYING,
                        (framesCount * 1000) / alsa->getCurrentSampleRate());
        prevPaused = paused;
      }

      if (paused) {
        memset(data.data(), 0, bytesToRead);
      } else {
        if (!in.expired()) {
          auto inNode = in.lock();
          // Streaming error - will not recover
          if (inNode->error()) {
            error = true;
            break;
          }
          size_t read = inNode->read(data.data(), bytesToRead, token);
          framesCount += bytesToFrames(read);
          if (read < bytesToRead) {
            memset(data.data() + read, 0, bytesToRead - read);
          }
        }
      }

      err = snd_pcm_writei(pcm_handle, data.data(), framesToRead);
      if (err == -EAGAIN) {
        continue;
      }
      if (err < 0) {
        std::cout << "XRUN" << std::endl;
        if (xrun_recovery(pcm_handle, err) < 0) {
          printf("Write error: %s\n", snd_strerror(err));
          break;
        }
      }
      if (!in.expired() && in.lock()->eof()) {
        eof = true;
        break;
      }
    }

    if (token.stop_requested() || error) {
      snd_pcm_drop(pcm_handle);
    } else {
      snd_pcm_drain(pcm_handle);
    }
    delete[] ufds;
    if (error) {
      std::string errorMessage;
      if (!in.expired()) {
        try {
          std::rethrow_exception(in.lock()->error());
        } catch (const std::exception &ex) {
          errorMessage = ex.what();
        }
      }
      sm->updateState(State::ERROR, 0, errorMessage);
      return -1;
    }
    sm->updateState(
        eof ? State::FINISHED : State::STOPPED,
        ((eof ? sm->lastState().audioInfo.totalSamples : framesCount) * 1000) /
            alsa->getCurrentSampleRate());
  } catch (const std::exception &ex) {
    sm->updateState(State::ERROR, 0, ex.what());
    return -1;
  }
  return 0;
}

AlsaDevice::AlsaDevice() { openDevice(); }

AlsaDevice::~AlsaDevice() {
  if (pcmHandle) {
    snd_pcm_close(static_cast<snd_pcm_t *>(pcmHandle));
  }
}

void *AlsaDevice::getHandle(unsigned int sampleRate,
                            unsigned int bitsPerSample) {
  if (currentSampleRate != sampleRate ||
      currentBitsPerSample != bitsPerSample || !pcmHandle) {
    init(sampleRate, bitsPerSample);
  } else {
    snd_pcm_prepare(static_cast<snd_pcm_t *>(pcmHandle));
  }
  return pcmHandle;
}

void AlsaDevice::init(unsigned int sampleRate, unsigned int bitsPerSample) {
  int err = 0;

  currentSampleRate = sampleRate;
  currentBitsPerSample = bitsPerSample;
  snd_pcm_format_t format = getFormat(bitsPerSample);

  snd_pcm_uframes_t buffer_size = 16384;
  snd_pcm_uframes_t period_size = 1024;

  snd_pcm_hw_params_t *hw_params;
  snd_pcm_hw_params_malloc(&hw_params);
  try {
    init_hwparams(static_cast<snd_pcm_t *>(pcmHandle), hw_params,
                  currentSampleRate, buffer_size, period_size, format);
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
    set_swparams(static_cast<snd_pcm_t *>(pcmHandle), sw_params, buffer_size,
                 period_size);
  } catch (const std::exception &ex) {
    snd_pcm_sw_params_free(sw_params);
    throw ex;
  }
  snd_pcm_sw_params_free(sw_params);

  // Hack for Raspberry Pi HiFiBerry
  // Sleep to make sure RPi is ready to play
  std::this_thread::sleep_for(std::chrono::milliseconds(500));
}

void AlsaDevice::openDevice() {
  const char *device_name = "hw:0,0";

  /* Open the device */
  int err = snd_pcm_open((snd_pcm_t **)&pcmHandle, device_name,
                         SND_PCM_STREAM_PLAYBACK, 0);

  /* Error check */
  if (err < 0) {
    std::stringstream stream;
    stream << "Cannot open audio device " << device_name << " ("
           << snd_strerror(err) << ")";
    pcmHandle = nullptr;
    throw std::runtime_error(stream.str());
  }
}