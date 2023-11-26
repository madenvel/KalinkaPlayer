#include "AudioFormat.h"

void convertToFormat(void *buffer, const int32_t *const samples[], size_t size,
                     AudioFormat format) {
  switch (format) {
  case AudioFormat::pcm_16le: {
    uint32_t *iBuffer = static_cast<uint32_t *>(buffer);
    for (size_t i = 0; i < size; ++i) {
      *iBuffer++ = packIntegers(samples[0][i], samples[1][i]);
    }
    break;
  }
  case AudioFormat::pcm_24le: {
    uint32_t *iBuffer = static_cast<uint32_t *>(buffer);
    for (size_t i = 0; i < size; ++i) {
      *iBuffer++ = samples[0][i] & 0xffffff;
      *iBuffer++ = samples[1][i] & 0xffffff;
    }
    break;
  }
  case AudioFormat::pcm_32le: {
    uint32_t *iBuffer = static_cast<uint32_t *>(buffer);
    for (size_t i = 0; i < size; ++i) {
      *iBuffer++ = (samples[0][i] & 0xffffff) << 8;
      *iBuffer++ = (samples[1][i] & 0xffffff) << 8;
    }
    break;
  }
  }
}
