#include "AudioSampleFormat.h"

#include <cstring>
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

namespace {
inline int32_t getSample(const uint8_t *source, AudioSampleFormat format) {
  int32_t result = 0;
  switch (format) {
  case PCM16_LE:
    result = *reinterpret_cast<const int16_t *>(source);
    return (result << 8);
  case PCM24_LE:
    result = (*reinterpret_cast<const int32_t *>(source) & 0xffffff);
    result |= (0xff000000 * (result & 0x800000));
    return result;
  case PCM32_LE:
    result = *reinterpret_cast<const int32_t *>(source) >> 8;
    return result;
  case PCM24_3LE:
    result = ((source[0] << 8) | (source[1] << 16) | (source[2] << 24)) >> 8;
    return result;
  default:
    break;
  }
  throw std::runtime_error("getSample: Unsupported format");
}

inline size_t putSample(uint8_t *dest, int32_t rawSample,
                        AudioSampleFormat format) {
  switch (format) {
  case PCM16_LE:
    *reinterpret_cast<int16_t *>(dest) = (rawSample >> 8) & 0xffff;
    return 2u;
  case PCM24_LE:
    *reinterpret_cast<int32_t *>(dest) = rawSample & 0xffffff;
    return 4u;
  case PCM32_LE:
    *reinterpret_cast<int32_t *>(dest) = rawSample << 8;
    return 4u;
  case PCM24_3LE:
    *dest++ = rawSample & 0xff;
    *dest++ = (rawSample >> 8) & 0xff;
    *dest = (rawSample >> 16) & 0xff;
    return 3u;
  default:
    throw std::runtime_error("putSample: Unsupported format");
  }
}
} // namespace

size_t convertSampleFormat(const void *source, AudioSampleFormat sourceFormat,
                           size_t sourceSamples, void *dest,
                           AudioSampleFormat destFormat, size_t destSizeBytes) {

  auto const destSampleBytes = sampleSize(destFormat);
  auto const sourceSampleBytes = sampleSize(sourceFormat);

  if (sourceFormat == destFormat) {
    auto minBytesToCopy =
        std::min(sourceSamples * sourceSampleBytes, destSizeBytes);
    minBytesToCopy -= minBytesToCopy % sourceSampleBytes;
    memcpy(dest, source, minBytesToCopy);
    return minBytesToCopy / sourceSampleBytes;
  }

  const uint8_t *sourcePtr = static_cast<const uint8_t *>(source);
  uint8_t *destPtr = static_cast<uint8_t *>(dest);

  for (size_t i = 0; i < sourceSamples; i++) {
    if ((i + 1) * destSampleBytes > destSizeBytes) {
      return i;
    }
    const int32_t rawSample = getSample(sourcePtr, sourceFormat);
    destPtr += putSample(destPtr, rawSample, destFormat);
    sourcePtr += sourceSampleBytes;
  }
  return sourceSamples;
}
