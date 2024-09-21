#ifndef AUDIO_SAMPLE_FORMAT_H
#define AUDIO_SAMPLE_FORMAT_H

#include <cstddef>
#include <cstdint>

enum AudioSampleFormat { PCM16_LE, PCM24_LE, PCM32_LE, PCM24_3LE };

// Hard-coded channels = 2
// We aim for hifi equipment and music
const int CHANNELS = 2;

constexpr AudioSampleFormat bitsToFormat(int bits) {
  if (bits == 16) {
    return AudioSampleFormat::PCM16_LE;
  } else if (bits == 24) {
    return AudioSampleFormat::PCM24_LE;
  } else if (bits == 32) {
    return AudioSampleFormat::PCM32_LE;
  }

  return AudioSampleFormat::PCM24_LE;
}

inline size_t samplesToBytes(size_t i32samplesCount, AudioSampleFormat format) {
  switch (format) {
  case AudioSampleFormat::PCM16_LE:
    return i32samplesCount * 2 * CHANNELS;
  case AudioSampleFormat::PCM24_LE:
  case AudioSampleFormat::PCM32_LE:
    return i32samplesCount * 4 * CHANNELS;
  default:
    return 0;
  }
}

inline int32_t packIntegers(int32_t a, int32_t b) {
  return ((b & 0xffff) << 16) + (a & 0xffff);
}

// Make sure buffer has enough space to allocate framesToBytes()
void convertToFormat(void *buffer, const int32_t *const samples[], size_t size,
                     AudioSampleFormat format);

#endif // AUDIO_SAMPLE_FORMAT_H