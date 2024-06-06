#ifndef AUDIO_INFO_H
#define AUDIO_INFO_H

#include <string>

struct AudioInfo {
  unsigned int sampleRate = 0;
  unsigned int channels = 0;
  unsigned int bitsPerSample = 0;
  unsigned long long durationMs = 0;
  unsigned long long totalSamples = 0;

  std::string toString() const {
    return "<AudioInfo sampleRate=" + std::to_string(sampleRate) +
           ", channels=" + std::to_string(channels) +
           ", bitsPerSample=" + std::to_string(bitsPerSample) +
           ", durationMs=" + std::to_string(durationMs) +
           ", totalSamples=" + std::to_string(totalSamples) + ">";
  }
};

#endif