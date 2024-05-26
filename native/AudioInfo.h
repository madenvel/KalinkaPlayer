#ifndef AUDIO_INFO_H
#define AUDIO_INFO_H

struct AudioInfo {
  int sampleRate = 0;
  int channels = 0;
  int bitsPerSample = 0;
  int durationMs = 0;
};

#endif