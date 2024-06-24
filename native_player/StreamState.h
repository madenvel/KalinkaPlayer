#pragma once

#include <chrono>
#include <optional>
#include <string>

#include "AudioGraphNode.h"
#include "AudioInfo.h"

// enum class State {
//   IDLE = 0,
//   READY,
//   BUFFERING,
//   PLAYING,
//   PAUSED,
//   FINISHED,
//   STOPPED,
//   ERROR
// };

enum class AudioGraphNodeState {
  ERROR = -1,
  STOPPED,
  PREPARING,
  STREAMING,
  PAUSED,
  FINISHED,
  SOURCE_CHANGED
};

inline long long getTimestampNs() {
  return std::chrono::steady_clock::now().time_since_epoch().count();
}

struct StreamState {
  AudioGraphNodeState state;
  long position;
  std::optional<StreamInfo> streamInfo;
  std::optional<std::string> message;
  unsigned long long timestamp;

  StreamState(AudioGraphNodeState state, long position,
              std::optional<StreamInfo> streamInfo)
      : state(state), position(position), streamInfo(streamInfo),
        timestamp(getTimestampNs()) {}

  StreamState(AudioGraphNodeState state, std::optional<std::string> message)
      : state(state), position(0), message(message),
        timestamp(getTimestampNs()) {}

  explicit StreamState(AudioGraphNodeState state)
      : state(state), position(0), timestamp(getTimestampNs()) {}

  StreamState(AudioGraphNodeState state, long position)
      : state(state), position(position), timestamp(getTimestampNs()) {}

  bool operator==(const StreamState &other) const = default;
  bool operator!=(const StreamState &other) const = default;

  std::string toString() const {
    return "<StreamState state=" + std::to_string(static_cast<int>(state)) +
           ", position=" + std::to_string(position) +
           ", message=" + message.value_or("null") + ", streamInfo=" +
           (streamInfo.has_value() ? streamInfo.value().toString() : "null") +
           ", timestamp=" + std::to_string(timestamp) + ">";
  }
};