#ifndef AUDIO_SAMPLE_FORMAT_H
#define AUDIO_SAMPLE_FORMAT_H

#include <cstddef>
#include <cstdint>

enum AudioSampleFormat { PCM16_LE, PCM24_LE, PCM32_LE, PCM24_3LE };

inline size_t sampleSize(AudioSampleFormat format) {
  switch (format) {
  case AudioSampleFormat::PCM16_LE:
    return 2;
  case AudioSampleFormat::PCM24_LE:
  case AudioSampleFormat::PCM32_LE:
    return 4;
  case AudioSampleFormat::PCM24_3LE:
    return 3;
  default:
    return 0;
  }
}

inline size_t sampleBits(AudioSampleFormat format) {
  switch (format) {
  case AudioSampleFormat::PCM16_LE:
    return 16;
  case AudioSampleFormat::PCM24_LE:
  case AudioSampleFormat::PCM24_3LE:
  case AudioSampleFormat::PCM32_LE:
    return 24;
  default:
    return 0;
  }
}

inline const char *const sampleFormatToString(AudioSampleFormat format) {
  switch (format) {
  case AudioSampleFormat::PCM16_LE:
    return "PCM16_LE";
  case AudioSampleFormat::PCM24_LE:
    return "PCM24_LE";
  case AudioSampleFormat::PCM32_LE:
    return "PCM32_LE";
  case AudioSampleFormat::PCM24_3LE:
    return "PCM24_3LE";
  default:
    return "Unknown";
  }
}

inline int32_t packIntegers(int32_t a, int32_t b) {
  return ((b & 0xffff) << 16) + (a & 0xffff);
}

// Make sure buffer has enough space to allocate framesToBytes()
void convertToFormat(void *buffer, const int32_t *const samples[], size_t size,
                     AudioSampleFormat format);

/// @brief Convert a buffer of samples from one format to another.
/// @param source data to convert
/// @param sourceFormat format to convert from
/// @param sourceSamples number of samples in source
/// @param dest destination buffer
/// @param destFormat destination sample format
/// @param destSizeBytes size of the destination buffer in bytes
/// @return the number of samples converted
size_t convertSampleFormat(const void *source, AudioSampleFormat sourceFormat,
                           size_t sourceSamples, void *dest,
                           AudioSampleFormat destFormat, size_t destSizeBytes);

#endif // AUDIO_SAMPLE_FORMAT_H