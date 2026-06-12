#ifndef SINE_WAVE_NODE_H
#define SINE_WAVE_NODE_H

#include "AudioGraphNode.h"

#include <cmath>

// Which channels of the interleaved stereo output carry the tone. Left/Right
// keep the other channel silent — used by the speaker-test endpoint to verify
// channel wiring.
enum class ToneChannel { Both = 0, Left, Right };

class SineWaveNode : public AudioGraphOutputNode {
  size_t position = 0;
  size_t totalDataSize = 0;
  StreamInfo streamInfo;
  int frequency;
  ToneChannel channel;

public:
  SineWaveNode(int frequency, int durationMs, unsigned int sampleRate = 48000,
               unsigned int bitsPerSample = 16,
               ToneChannel channel = ToneChannel::Both)
      : frequency(frequency), channel(channel) {
    totalDataSize = sampleRate * (bitsPerSample >> 3) * 2 * durationMs / 1000;
    streamInfo =
        StreamInfo{.format = {.sampleRate = sampleRate,
                              .channels = 2,
                              .bitsPerSample = bitsPerSample},
                   .streamType = StreamType::FRAMES,
                   .streamSize = static_cast<unsigned int>(durationMs) *
                                 sampleRate / 1000};
    setState({AudioGraphNodeState::STREAMING, 0, streamInfo});
  }
  virtual size_t read(void *data, size_t size) override {

    size = std::min(size, totalDataSize - position);

    for (size_t i = 0; i < size / 2; i++) {
      size_t pos = i + position / 2;
      // Interleaved stereo: even sample index = left, odd = right.
      const bool isRightSample = (pos % 2) != 0;
      const bool muted = (channel == ToneChannel::Left && isRightSample) ||
                         (channel == ToneChannel::Right && !isRightSample);
      const auto value =
          muted ? 0.0
                : floor(8192 * sin(2 * M_PI * static_cast<double>(frequency) *
                                   floor(pos / 2) /
                                   streamInfo.format.sampleRate));
      static_cast<int16_t *>(data)[i] = value;
    }
    position += size;

    if (position == totalDataSize) {
      setState(StreamState(AudioGraphNodeState::FINISHED));
    }

    return size;
  }

  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) override {
    return std::min(size, totalDataSize - position);
  }

  virtual size_t waitForDataFor(std::stop_token stopToken,
                                std::chrono::milliseconds timeout,
                                size_t size) override {
    (void)timeout;
    return waitForData(stopToken, size);
  }

  virtual size_t seekTo(size_t positionSamples) override {
    setState(StreamState{AudioGraphNodeState::PREPARING});
    positionSamples =
        std::min(positionSamples, static_cast<size_t>(streamInfo.streamSize));
    auto positionBytes = positionSamples * streamInfo.format.channels *
                         (streamInfo.format.bitsPerSample >> 3);
    this->position = positionBytes;
    if (position == totalDataSize) {
      setState(StreamState(AudioGraphNodeState::FINISHED));
    } else {
      setState(StreamState{AudioGraphNodeState::STREAMING,
                           static_cast<long>(positionSamples), streamInfo});
    }
    return positionSamples;
  }

  virtual ~SineWaveNode() = default;
};

#endif