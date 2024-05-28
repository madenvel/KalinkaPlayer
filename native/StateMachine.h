#ifndef STATE_MACHINE_H
#define STATE_MACHINE_H

#include <atomic>
#include <functional>
#include <string>

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

  StateInfo(State state, long position, std::string message = {})
      : state(state), position(position), message(message) {}
  StateInfo() = default;
};

class StateMachine {
  std::atomic<int> st;
  std::function<void(const StateInfo)> callback;

public:
  StateMachine(std::function<void(const StateInfo)> cb)
      : st(INVALID), callback(cb) {}

  void updateState(State newState, long position, std::string message = {}) {
    st.store(newState);
    callback({newState, position, message});
  }

  State lastState() const { return static_cast<State>(st.load()); }
};

#endif