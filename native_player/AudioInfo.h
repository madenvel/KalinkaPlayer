#ifndef AUDIO_INFO_H
#define AUDIO_INFO_H

#include "AudioSampleFormat.h"

#include <string>

/// @brief Defines the type of the stream.
enum StreamType {
  // Indicates that the stream is a sequence of bytes
  // that need to be processed (i.g. compressed data)
  Bytes,
  // Indicates that the stream is a sequence of audio frames
  // that can be directly played (i.g. PCM data).
  // The format of the frames is defined by the StreamAudioFormat.
  Frames
};

inline std::string streamTypeToString(StreamType type) {
  switch (type) {
  case Bytes:
    return "bytes";
  case Frames:
    return "frames";
  default:
    return "unnknown";
  }
}

/// @brief Defines the audio format of the frames in the stream.
struct StreamAudioFormat {
  unsigned int sampleRate = 0;
  unsigned int channels = 0;
  unsigned int bitsPerSample = 0;
  AudioSampleFormat sampleFormat;

  bool operator==(const StreamAudioFormat &other) const = default;
  bool operator!=(const StreamAudioFormat &other) const = default;

  std::string toString() const {
    return "<AudioFormat sampleRate=" + std::to_string(sampleRate) +
           ", channels=" + std::to_string(channels) +
           ", bitsPerSample=" + std::to_string(bitsPerSample) + ">";
  }
};

/// @brief Defines the information about the stream.
struct StreamInfo {
  // The format of the audio frames in the stream.
  // If the stream is a sequence of bytes, this field
  // is not used.
  StreamAudioFormat format;

  // The type of the stream.
  StreamType streamType;
  // The size of the stream in frames or bytes,
  // depending on the stream type.
  unsigned long streamSize = 0;

  bool operator==(const StreamInfo &other) const = default;
  bool operator!=(const StreamInfo &other) const = default;

  std::string toString() const {
    return "<StreamInfo format=" + format.toString() +
           ", streamType=" + streamTypeToString(streamType) +
           ", streamSize=" + std::to_string(streamSize) + ">";
  }
};

#endif