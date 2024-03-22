#ifndef AUDIO_INFO_H
#define AUDIO_INFO_H

struct AudioInfo {
  int sampleRate;
  int channels;
  int bitsPerSample;
  int durationMs;
};

#endif