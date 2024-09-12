#ifndef AUDIO_INFO_H
#define AUDIO_INFO_H

#include <string>

struct StreamAudioFormat {
  unsigned int sampleRate = 0;
  unsigned int channels = 0;
  unsigned int bitsPerSample = 0;

  bool operator==(const StreamAudioFormat &other) const = default;
  bool operator!=(const StreamAudioFormat &other) const = default;

  std::string toString() const {
    return "<AudioFormat sampleRate=" + std::to_string(sampleRate) +
           ", channels=" + std::to_string(channels) +
           ", bitsPerSample=" + std::to_string(bitsPerSample) + ">";
  }
};

struct StreamInfo {
  StreamAudioFormat format;
  unsigned long long totalSamples = 0;

  bool operator==(const StreamInfo &other) const = default;
  bool operator!=(const StreamInfo &other) const = default;

  std::string toString() const {
    return "<StreamInfo format=" + format.toString() +
           ", totalSamples=" + std::to_string(totalSamples) + ">";
  }
};

#endif