#ifndef STATE_MACHINE_H
#define STATE_MACHINE_H

#include <atomic>
#include <functional>
#include <optional>
#include <string>

#include "AudioInfo.h"

enum State {
  IDLE = 0,
  READY,
  BUFFERING,
  PLAYING,
  PAUSED,
  FINISHED,
  STOPPED,
  ERROR
};

struct StateInfo {
  State state = IDLE;
  long position = 0;
  std::optional<std::string> message;
  std::optional<AudioInfo> audioInfo;

  StateInfo(State state, long position = 0,
            std::optional<std::string> message = std::nullopt)
      : state(state), position(position), message(message) {}
  StateInfo() = default;

  bool operator==(const StateInfo &other) const = default;
  bool operator!=(const StateInfo &other) const = default;

  std::string toString() const {
    return "<StateInfo state=" + std::to_string(state) +
           ", position=" + std::to_string(position) +
           ", message=" + message.value_or("null") + ", audioInfo=" +
           (audioInfo.has_value() ? audioInfo.value().toString() : "null") +
           ">";
  }
};

class StateMachine {
  StateInfo state;
  std::function<void(const StateInfo)> callback;

public:
  StateMachine(std::function<void(const StateInfo)> cb)
      : state(IDLE), callback(cb) {}

  void updateState(State newState, std::optional<long> position = std::nullopt,
                   std::optional<std::string> message = std::nullopt,
                   std::optional<AudioInfo> audioInfo = std::nullopt) {
    state.state = newState;
    state.position = position.value_or(state.position);
    state.message = message;
    state.audioInfo = audioInfo.has_value() ? audioInfo : state.audioInfo;

    callback(state);
  }

  const StateInfo &lastState() const { return state; }
};

#endif