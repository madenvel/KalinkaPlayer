#ifndef SLOW_OUTPUT_NODE_H
#define SLOW_OUTPUT_NODE_H

#include "AudioGraphNode.h"
#include "Log.h"

#include <chrono>

class SlowOutputNode : public AudioGraphOutputNode {
  unsigned int duration;
  int delayMs;
  size_t position = 0;
  std::chrono::time_point<std::chrono::steady_clock> delayStartTime;
  std::chrono::time_point<std::chrono::steady_clock> delayEndTime;

  bool hasDelayed = false;
  bool finished = false;

public:
  SlowOutputNode(unsigned int duration, int delayMs = 500)
      : AudioGraphOutputNode(), duration(duration), delayMs(delayMs) {
    setState(StreamState{
        AudioGraphNodeState::STREAMING, 0,
        StreamInfo{
            .format = {.sampleRate = 48000, .channels = 2, .bitsPerSample = 16},
            .totalSamples = 48 * 2 * duration,
            .durationMs = duration}});
  }

  virtual size_t read(void *data, size_t size) override {
    if (finished) {
      return 0;
    }

    auto halfDurationBytes = 48 * 2 * duration;

    auto time = std::chrono::steady_clock::now();

    if (time >= delayStartTime && time < delayEndTime) {
      spdlog::info("Delaying, delayStartTime={}, time={}, delayEndTime={}",
                   delayStartTime.time_since_epoch().count(),
                   time.time_since_epoch().count(),
                   delayEndTime.time_since_epoch().count());
      return 0;
    }

    if (position + size > halfDurationBytes && !hasDelayed) {
      spdlog::info("Reached half duration");
      size = halfDurationBytes - position;
      delayStartTime = std::chrono::steady_clock::now();
      delayEndTime = delayStartTime + std::chrono::milliseconds(delayMs);
      hasDelayed = true;
    }

    if (position + size > 2 * halfDurationBytes) {
      size = 2 * halfDurationBytes - position;
      finished = true;
      setState(StreamState{AudioGraphNodeState::FINISHED});
    }

    for (size_t i = 0; i < size / 2; i += 2) {
      int16_t amplitude = -3000 + 6000 * (((position / 2 + i) / 100) % 2);
      static_cast<uint16_t *>(data)[i] = amplitude;
      static_cast<uint16_t *>(data)[i + 1] = amplitude;
    }

    position += size;

    return size;
  }

  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) override {
    if (finished) {
      return 0;
    }
    auto time = std::chrono::steady_clock::now();

    if (time >= delayStartTime && time < delayEndTime) {
      spdlog::info("Sleeping as we are in delay");
      std::this_thread::sleep_for(delayEndTime - time);
    }
    return 1;
  }

  virtual size_t waitForDataFor(std::stop_token stopToken,
                                std::chrono::milliseconds timeout,
                                size_t size) override {
    if (finished) {
      return 0;
    }
    auto time = std::chrono::steady_clock::now();

    if (time >= delayStartTime && time < delayEndTime) {
      auto duration = delayEndTime - time;
      spdlog::info("Sleeping as we are in delay (with timeout)");
      std::this_thread::sleep_for(std::min(
          std::chrono::duration_cast<std::chrono::milliseconds>(duration),
          timeout));

      time = std::chrono::steady_clock::now();
      if (time < delayEndTime) {
        return 0;
      }
    }

    return 1;
  }
};

#endif // SLOW_OUTPUT_NODE_H