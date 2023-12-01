#ifndef STATE_MACHINE_H
#define STATE_MACHINE_H

#include <atomic>
#include <functional>
#include <string>

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

class StateMachine {
  std::atomic<int> st;
  std::string stateComment;
  std::function<void(State, State)> callback;

public:
  StateMachine(std::function<void(State, State)> cb) : st(IDLE), callback(cb) {}

  State updateState(State newState) {
    State oldState = static_cast<State>(st.exchange(newState));
    callback(oldState, newState);

    return oldState;
  }

  void setStateComment(std::string comment) { stateComment = comment; }

  State state() const { return static_cast<State>(st.load()); }
};

#endif