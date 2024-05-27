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
  State state;
  long position;

  StateInfo(State state, long position) : state(state), position(position) {}
  StateInfo() = default;
};

class StateMachine {
  std::atomic<int> st;
  std::string stateComment;
  std::function<void(const StateInfo *)> callback;

public:
  StateMachine(std::function<void(const StateInfo *)> cb)
      : st(INVALID), callback(cb) {}

  void updateState(State newState, long position) {
    st.store(newState);
    auto state = StateInfo(newState, position);
    callback(&state);
  }

  void setStateComment(std::string comment) { stateComment = comment; }

  std::string getStateComment() { return stateComment; }

  State state() const { return static_cast<State>(st.load()); }
};

#endif