#ifndef SINE_WAVE_NODE_H
#define SINE_WAVE_NODE_H

#include "AudioGraphNode.h"

#include <cmath>

class SineWaveNode : public AudioGraphOutputNode {
  size_t position = 0;
  size_t totalDataSize = 0;
  int durationMs;
  int frequency;

public:
  SineWaveNode(int frequency, int durationMs)
      : durationMs(durationMs), frequency(frequency) {
    totalDataSize = 96000 * 2 * durationMs / 1000;
    setState(AudioGraphNodeState::STREAMING);
  }
  virtual size_t read(void *data, size_t size) override {

    size = std::min(size, totalDataSize - position);

    for (size_t i = 0; i < size / 2; i++) {
      size_t pos = i + position / 2;
      const auto value =
          floor(8192 * sin(2 * M_PI * static_cast<double>(frequency) *
                           floor(pos / 2) / 48000.0));
      static_cast<int16_t *>(data)[i] = value;
    }
    position += size;

    if (position == totalDataSize) {
      setState(AudioGraphNodeState::FINISHED);
    }

    return size;
  }

  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) {
    return std::min(size, totalDataSize - position);
  }

  virtual StreamInfo getStreamInfo() override {
    return StreamInfo{
        .format = {.sampleRate = 48000, .channels = 2, .bitsPerSample = 16},
        .totalSamples = static_cast<unsigned int>(durationMs) * 48000 / 1000,
        .durationMs = static_cast<unsigned int>(
            durationMs)}; // {sampleRate, channels, bitsPerSample,
                          // totalSamples, durationMs
  }
  virtual ~SineWaveNode() = default;
};

#endif