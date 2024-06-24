#include <cstddef>
#include <cstdint>

enum AudioFormat { pcm_16le, pcm_24le, pcm_32le };

// Hard-coded channels = 2
// We aim for hifi equipment and music
const int CHANNELS = 2;

constexpr AudioFormat bitsToFormat(int bits) {
  if (bits == 16) {
    return AudioFormat::pcm_16le;
  } else if (bits == 24) {
    return AudioFormat::pcm_24le;
  } else if (bits == 32) {
    return AudioFormat::pcm_32le;
  }

  return AudioFormat::pcm_24le;
}

inline size_t samplesToBytes(size_t i32samplesCount, AudioFormat format) {
  switch (format) {
  case AudioFormat::pcm_16le:
    return i32samplesCount * 2 * CHANNELS;
  case AudioFormat::pcm_24le:
  case AudioFormat::pcm_32le:
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
                     AudioFormat format);