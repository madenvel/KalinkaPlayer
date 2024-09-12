#include "AudioSampleFormat.h"

#include <stdexcept>

void convertToFormat(void *buffer, const int32_t *const samples[], size_t size,
                     AudioSampleFormat format) {
  switch (format) {
  case AudioSampleFormat::PCM16_LE: {
    uint32_t *iBuffer = static_cast<uint32_t *>(buffer);
    for (size_t i = 0; i < size; ++i) {
      *iBuffer++ = packIntegers(samples[0][i], samples[1][i]);
    }
    break;
  }
  case AudioSampleFormat::PCM24_LE: {
    uint32_t *iBuffer = static_cast<uint32_t *>(buffer);
    for (size_t i = 0; i < size; ++i) {
      *iBuffer++ = samples[0][i] & 0xffffff;
      *iBuffer++ = samples[1][i] & 0xffffff;
    }
    break;
  }
  case AudioSampleFormat::PCM32_LE: {
    uint32_t *iBuffer = static_cast<uint32_t *>(buffer);
    for (size_t i = 0; i < size; ++i) {
      *iBuffer++ = (samples[0][i] & 0xffffff) << 8;
      *iBuffer++ = (samples[1][i] & 0xffffff) << 8;
    }
    break;
  }
  case AudioSampleFormat::PCM24_3LE:
  default:
    throw std::runtime_error("Unsupported format");
  }
}
