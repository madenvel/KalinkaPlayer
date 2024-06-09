#ifndef STATE_MACHINE_H
#define STATE_MACHINE_H

#include <atomic>
#include <functional>
#include <optional>
#include <string>

#include "AudioInfo.h"

enum State {
  INVALID = -1,
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
  State state = INVALID;
  long position = 0;
  std::string message;
  std::optional<AudioInfo> audioInfo;

  StateInfo(State state, long position = 0, std::string message = {})
      : state(state), position(position), message(message) {}
  StateInfo() = default;

  std::string toString() const {
    return "<StateInfo state=" + std::to_string(state) +
           ", position=" + std::to_string(position) + ", message=" + message +
           ", audioInfo=" +
           (audioInfo.has_value() ? audioInfo.value().toString() : "null") +
           ">";
  }
};

class StateMachine {
  StateInfo state;
  std::function<void(const StateInfo)> callback;

public:
  StateMachine(std::function<void(const StateInfo)> cb)
      : state(INVALID), callback(cb) {}

  void updateState(State newState, std::optional<long> position = std::nullopt,
                   std::optional<std::string> message = std::nullopt,
                   std::optional<AudioInfo> audioInfo = std::nullopt) {
    state.state = newState;
    state.position = position.value_or(state.position);
    state.message = message.value_or(state.message);
    state.audioInfo = audioInfo.has_value() ? audioInfo : state.audioInfo;

    callback(state);
  }

  const StateInfo &lastState() const { return state; }
};

#endif