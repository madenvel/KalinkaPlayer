#include "StreamState.h"

#include <sstream>

std::ostream &operator<<(std::ostream &os, AudioGraphNodeState state) {
  switch (state) {
  case AudioGraphNodeState::ERROR:
    os << "ERROR";
    break;
  case AudioGraphNodeState::STOPPED:
    os << "STOPPED";
    break;
  case AudioGraphNodeState::PREPARING:
    os << "PREPARING";
    break;
  case AudioGraphNodeState::STREAMING:
    os << "STREAMING";
    break;
  case AudioGraphNodeState::PAUSED:
    os << "PAUSED";
    break;
  case AudioGraphNodeState::FINISHED:
    os << "FINISHED";
    break;
  case AudioGraphNodeState::SOURCE_CHANGED:
    os << "SOURCE_CHANGED";
    break;
  }
  return os;
}

std::string stateToString(AudioGraphNodeState state) {
  std::stringstream os;
  os << state;
  return os.str();
}
