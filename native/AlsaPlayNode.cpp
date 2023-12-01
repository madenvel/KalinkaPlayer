#include <alsa/asoundlib.h>

#include <cstddef>
#include <cstdio>

#include <vector>

#include <algorithm>
#include <iostream>
#include <math.h>

#include "AlsaPlayNode.h"
#include "StateMachine.h"

int init_hwparams(snd_pcm_t *pcm_handle, snd_pcm_hw_params_t *params,
                  unsigned int rate, snd_pcm_uframes_t &buffer_size,
                  snd_pcm_uframes_t &period_size, snd_pcm_format_t format) {

  unsigned int rrate;
  int err, dir = 0;

  /* choose all parameters */
  err = snd_pcm_hw_params_any(pcm_handle, params);
  if (err < 0) {
    printf(
        "Broken configuration for playback: no configurations available: %s\n",
        snd_strerror(err));
    return err;
  }
  /* set the interleaved read/write format */
  err = snd_pcm_hw_params_set_access(pcm_handle, params,
                                     SND_PCM_ACCESS_RW_INTERLEAVED);
  if (err < 0) {
    printf("Access type not available for playback: %s\n", snd_strerror(err));
    return err;
  }
  /* set the sample format */
  err = snd_pcm_hw_params_set_format(pcm_handle, params, format);
  if (err < 0) {
    printf("Sample format not available for playback: %s\n", snd_strerror(err));
    return err;
  }
  /* set the count of channels */
  err = snd_pcm_hw_params_set_channels(pcm_handle, params, 2);
  if (err < 0) {
    printf("Channels count (%u) not available for playbacks: %s\n", 2,
           snd_strerror(err));
    return err;
  }
  // Enabled resampling
  err = snd_pcm_hw_params_set_rate_resample(pcm_handle, params, 1);
  if (err < 0) {
    printf("Resampling setup failed for playback: %s\n", snd_strerror(err));
    return err;
  }
  /* set the stream rate */
  rrate = rate;
  err = snd_pcm_hw_params_set_rate_near(pcm_handle, params, &rrate, 0);
  if (err < 0) {
    printf("Rate %uHz not available for playback: %s\n", rate,
           snd_strerror(err));
    return err;
  }
  if (rrate != rate) {
    printf("Rate doesn't match (requested %uHz, get %iHz)\n", rate, rrate);
    // return -EINVAL;
  }
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
    printf("Unable to get buffer size for playback: %s\n", snd_strerror(err));
    return err;
  }
  // /* set the period size */
  err = snd_pcm_hw_params_set_period_size_near(pcm_handle, params, &period_size,
                                               &dir);
  if (err < 0) {
    printf("Unable to set period time %lu for playback: %s\n", period_size,
           snd_strerror(err));
    return err;
  }
  // err = snd_pcm_hw_params_get_period_size(params, &period_size, &dir);
  // if (err < 0) {
  //   printf("Unable to get period size for playback: %s\n",
  //   snd_strerror(err)); return err;
  // }
  /* write the parameters to device */
  err = snd_pcm_hw_params(pcm_handle, params);
  if (err < 0) {
    printf("Unable to set hw params for playback: %s\n", snd_strerror(err));
    return err;
  }
  return 0;
}

static int set_swparams(snd_pcm_t *handle, snd_pcm_sw_params_t *swparams,
                        snd_pcm_uframes_t buffer_size,
                        snd_pcm_uframes_t period_size) {
  int err;

  /* get the current swparams */
  err = snd_pcm_sw_params_current(handle, swparams);
  if (err < 0) {
    printf("Unable to determine current swparams for playback: %s\n",
           snd_strerror(err));
    return err;
  }
  /* start the transfer when the buffer is almost full: */
  /* (buffer_size / avail_min) * avail_min */
  err = snd_pcm_sw_params_set_start_threshold(
      handle, swparams, (buffer_size / period_size) * period_size);
  if (err < 0) {
    printf("Unable to set start threshold mode for playback: %s\n",
           snd_strerror(err));
    return err;
  }
  /* allow the transfer when at least period_size samples can be processed */
  /* or disable this mechanism when period event is enabled (aka interrupt like
   * style processing) */
  bool period_event = false;
  err = snd_pcm_sw_params_set_avail_min(
      handle, swparams, period_event ? buffer_size : period_size);
  if (err < 0) {
    printf("Unable to set avail min for playback: %s\n", snd_strerror(err));
    return err;
  }
  /* enable period events when requested */
  if (period_event) {
    err = snd_pcm_sw_params_set_period_event(handle, swparams, 1);
    if (err < 0) {
      printf("Unable to set period event: %s\n", snd_strerror(err));
      return err;
    }
  }
  /* write the parameters to the playback device */
  err = snd_pcm_sw_params(handle, swparams);
  if (err < 0) {
    printf("Unable to set sw params for playback: %s\n", snd_strerror(err));
    return err;
  }

  return err;
}

static int xrun_recovery(snd_pcm_t *handle, int err) {
  if (true)
    printf("stream recovery\n");
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
    std::cout << "Requesting alsa thread to stop" << std::endl;
    playThread.request_stop();
    playThread.join();
    playThread = std::jthread();
  }
}

int AlsaPlayNode::workerThread(std::stop_token token) {
  /* This holds the error code returned */
  int err = 0;

  /* Our device handle */
  snd_pcm_t *pcm_handle = NULL;

  /* The device name */
  const char *device_name = "hw:0,0";

  /* Open the device */
  err = snd_pcm_open(&pcm_handle, device_name, SND_PCM_STREAM_PLAYBACK, 0);

  /* Error check */
  if (err < 0) {
    fprintf(stderr, "cannot open audio device %s (%s)\n", device_name,
            snd_strerror(err));
    pcm_handle = NULL;
    return -1;
  }

  unsigned int rrate = sampleRate;
  snd_pcm_format_t format =
      bitsPerSample == 16 ? SND_PCM_FORMAT_S16_LE : SND_PCM_FORMAT_S24_LE;

  int pbits = snd_pcm_format_physical_width(format);
  int pbytes = pbits / 8;

  snd_pcm_uframes_t buffer_size = 16384;
  snd_pcm_uframes_t period_size = 1024;

  const auto framesToBytes = [pbytes](size_t frames) -> size_t {
    return frames * pbytes * 2;
  };

  const auto bytesToFrames = [pbytes](size_t bytes) -> size_t {
    return bytes / (pbytes * 2);
  };

  snd_pcm_hw_params_t *hw_params;
  snd_pcm_hw_params_malloc(&hw_params);
  err = init_hwparams(pcm_handle, hw_params, rrate, buffer_size, period_size,
                      format);
  snd_pcm_hw_params_free(hw_params);

  if (err < 0) {
    fprintf(stderr, "cannot set hw params %s (%s)\n", device_name,
            snd_strerror(err));
    return err;
  }

  snd_pcm_sw_params_t *sw_params;
  snd_pcm_sw_params_malloc(&sw_params);
  set_swparams(pcm_handle, sw_params, buffer_size, period_size);
  snd_pcm_sw_params_free(sw_params);

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

  std::vector<uint8_t> data(2 * period_size * pbytes);

  sm->updateState(State::PLAYING);
  bool prevPaused = paused = false;

  size_t framesCount = 0;
  size_t prevFramesCountProgressReported = -1;
  bool eof = false;
  bool error = false;

  while (!token.stop_requested()) {
    err = wait_for_poll(pcm_handle, ufds, count);
    if (err < 0) {
      break;
    }
    snd_pcm_sframes_t frames = snd_pcm_avail_update(pcm_handle);
    if (frames < period_size) {
      continue;
    }
    size_t framesToRead = std::min((long)bytesToFrames(data.size()), frames);
    size_t bytesToRead = framesToBytes(framesToRead);

    if (prevPaused != paused) {
      sm->updateState(paused ? State::PAUSED : State::PLAYING);
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
        if (framesCount - prevFramesCountProgressReported > sampleRate / 2 ||
            (inNode->eof())) {
          prevFramesCountProgressReported = framesCount;
          progressCb((float)framesCount / totalFrames);
        }
        if (read < bytesToRead) {
          memset(data.data() + read, 0, bytesToRead - read);
        }
      }
    }

    err = snd_pcm_writei(pcm_handle, data.data(), framesToRead);
    if (err == -EAGAIN)
      continue;
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

  snd_pcm_drain(pcm_handle);
  snd_pcm_close(pcm_handle);
  delete[] ufds;
  if (error) {
    sm->updateState(State::ERROR);
    if (!in.expired()) {
      try {
        std::rethrow_exception(in.lock()->error());
      } catch (const std::exception &ex) {
        sm->setStateComment(ex.what());
      }
    }
    return -1;
  }
  sm->updateState(eof ? State::FINISHED : State::STOPPED);
  return 0;
}