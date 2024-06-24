#include "AudioGraphNode.h"

#ifdef __PYTHON__
#include <pybind11/pybind11.h>
#endif

StreamState AudioGraphNode::getState() {
  std::lock_guard lock(mutex);
  return state;
}

StreamState
AudioGraphNode::waitForStatus(std::stop_token stopToken,
                              std::optional<AudioGraphNodeState> nextState) {
  std::unique_lock lock(mutex);
  cv.wait(lock, stopToken, [&] {
    if (done) {
      return true;
    }
    if (nextState.has_value()) {
      return state.state == nextState.value();
    }
    return true;
  });
  return state;
}

AudioGraphNode::~AudioGraphNode() {
  done = true;
  for (auto monitor : monitors) {
    monitor->ptr = nullptr;
    monitor->stopped = true;
  }
  cv.notify_all();
}

void AudioGraphNode::setState(const StreamState &newState) {
  {
    std::lock_guard lock(mutex);
    state = newState;
    for (auto monitor : monitors) {
      monitor->queue_.push(newState);
    }
  }
  cv.notify_all();
}

StateMonitor::StateMonitor(AudioGraphNode *ptr) : ptr(ptr) {
  std::unique_lock lock(ptr->mutex);
  ptr->monitors.emplace_back(this);
  queue_.push(ptr->state);
}

StateMonitor::~StateMonitor() {
  if (ptr) {
    std::unique_lock lock(ptr->mutex);
    ptr->monitors.remove(this);
  }
}

StreamState StateMonitor::waitState() {
#ifdef __PYTHON__
  pybind11::gil_scoped_release release;
#endif
  if (stopped) {
    return StreamState(AudioGraphNodeState::STOPPED);
  }

  std::unique_lock lock(ptr->mutex);
  ptr->cv.wait(lock, [this] { return !queue_.empty() || stopped; });
  if (stopped) {
    return StreamState(AudioGraphNodeState::STOPPED);
  }
  auto message = queue_.front();
  queue_.pop();
  return message;
}

bool StateMonitor::hasData() {
  std::unique_lock lock(ptr->mutex);
  return !queue_.empty();
}

void StateMonitor::stop() {
  stopped = true;
  ptr->cv.notify_all();
}