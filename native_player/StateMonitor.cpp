#include "StateMonitor.h"

#ifdef __PYTHON__
#include <pybind11/pybind11.h>
#endif

StateMonitor::StateMonitor(AudioGraphNode *ptr) : ptr(ptr) {
  if (ptr) {
    subscriptionId = ptr->onStateChange(
        [this](AudioGraphNode *node, StreamState state) -> bool {
          std::unique_lock lock(mutex);
          queue_.push(state);
          cv.notify_all();
          return true;
        });
  }
}

StateMonitor::~StateMonitor() { stop(); }

StreamState StateMonitor::waitState() {
#ifdef __PYTHON__
  pybind11::gil_scoped_release release;
#endif
  if (stopped) {
    return StreamState(AudioGraphNodeState::STOPPED);
  }

  std::unique_lock lock(mutex);
  cv.wait(lock, [this] { return !queue_.empty() || stopped; });

  if (stopped) {
    return StreamState(AudioGraphNodeState::STOPPED);
  }
  auto state = queue_.front();
  queue_.pop();
  return state;
}

bool StateMonitor::hasData() {
  std::unique_lock lock(mutex);
  return !queue_.empty();
}

void StateMonitor::stop() {
  if (stopped) {
    return;
  }
  ptr->removeStateChangeCallback(subscriptionId);
  stopped = true;
  cv.notify_all();
}

StateChangeWaitLock::StateChangeWaitLock(std::stop_token token,
                                         AudioGraphNode &node,
                                         AudioGraphNodeState nextState)
    : lastState(AudioGraphNodeState::STOPPED) {
  mutex.lock();
  int subscriptionId = node.onStateChange(
      [this, nextState](AudioGraphNode *node, StreamState state) -> bool {
        lastState = state;
        if (state.state == nextState) {
          mutex.unlock();
          return false;
        }
        return true;
      });
  mutex.lock();
  mutex.unlock();
  //   std::unique_lock lock(mutex);
  //   cv.wait(lock, token,
  //           [this, nextState] { return lastState.state == nextState; });
  node.removeStateChangeCallback(subscriptionId);
}

StateChangeWaitLock::StateChangeWaitLock(std::stop_token token,
                                         AudioGraphNode &node,
                                         unsigned long long timestamp)
    : lastState(AudioGraphNodeState::STOPPED) {
  int subscriptionId = node.onStateChange(
      [this, timestamp](AudioGraphNode *node, StreamState state) -> bool {
        std::lock_guard lock(mutex);
        lastState = state;
        cv.notify_all();
        return state.timestamp > timestamp;
      });
  std::unique_lock lock(mutex);
  cv.wait(lock, token,
          [this, timestamp] { return lastState.timestamp > timestamp; });
  node.removeStateChangeCallback(subscriptionId);
}
