#include "AudioGraphNode.h"

AudioGraphNodeState AudioGraphNode::getState() {
  std::lock_guard lock(mutex);
  return state;
}

AudioGraphNodeState
AudioGraphNode::waitForStatus(std::stop_token stopToken,
                              std::optional<AudioGraphNodeState> nextState) {
  std::unique_lock lock(mutex);
  cv.wait(lock, stopToken, [&] {
    if (nextState.has_value()) {
      return state == nextState.value();
    }
    return true;
  });
  return state;
}

void AudioGraphNode::setState(AudioGraphNodeState newState) {
  {
    std::lock_guard lock(mutex);
    state = newState;
    for (auto monitor : monitors) {
      monitor->queue_.push(newState);
    }
  }
  cv.notify_all();
}

StateMonitor::StateMonitor(AudioGraphNode &ptr) : ptr(ptr) {
  std::unique_lock lock(ptr.mutex);
  ptr.monitors.emplace_back(this);
  queue_.push(ptr.state);
}

StateMonitor::~StateMonitor() {
  std::unique_lock lock(ptr.mutex);
  ptr.monitors.remove(this);
}

AudioGraphNodeState StateMonitor::waitState() {
  std::unique_lock lock(ptr.mutex);
  ptr.cv.wait(lock, [this] { return !queue_.empty(); });
  auto message = queue_.front();
  queue_.pop();
  return message;
}

bool StateMonitor::hasData() {
  std::unique_lock lock(ptr.mutex);
  return !queue_.empty();
}