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

class StateMachine {
  std::atomic<int> st;
  std::string stateComment;
  std::function<void(State, long)> callback;

public:
  StateMachine(std::function<void(State, long)> cb)
      : st(INVALID), callback(cb) {}

  void updateState(State newState, long position) {
    st.store(newState);
    callback(newState, position);
  }

  void setStateComment(std::string comment) {
    std::cerr << "State comment: " << comment << std::endl;
    stateComment = comment;
  }

  std::string getStateComment() { return stateComment; }

  State state() const { return static_cast<State>(st.load()); }
};

#endif