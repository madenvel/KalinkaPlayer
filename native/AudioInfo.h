#ifndef AUDIO_INFO_H
#define AUDIO_INFO_H

#include <string>

struct AudioInfo {
  unsigned int sampleRate = 0;
  unsigned int channels = 0;
  unsigned int bitsPerSample = 0;
  unsigned long long totalSamples = 0;
  unsigned int durationMs = 0;

  std::string toString() const {
    return "<AudioInfo sampleRate=" + std::to_string(sampleRate) +
           ", channels=" + std::to_string(channels) +
           ", bitsPerSample=" + std::to_string(bitsPerSample) +
           ", totalSamples=" + std::to_string(totalSamples) +
           ", durationMs=" + std::to_string(durationMs) + ">";
  }
};

#endif