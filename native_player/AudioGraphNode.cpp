#include "AudioGraphNode.h"

#ifdef __PYTHON__
#include <pybind11/pybind11.h>
#endif

#include "Log.h"

StreamState AudioGraphNode::getState() {
  std::lock_guard lock(mutex);
  return state;
}

int AudioGraphNode::onStateChange(
    std::function<bool(AudioGraphNode *, StreamState)> callback) {
  std::lock_guard lock(mutex);
  stateChangeCallbacks.insert({++callbackId, callback});
  callback(this, state);
  return callbackId;
}

void AudioGraphNode::removeStateChangeCallback(int id) {
  if (id < 0) {
    return;
  }

  std::lock_guard lock(mutex);
  stateChangeCallbacks.erase(id);
}

AudioGraphNode::~AudioGraphNode() {}

void AudioGraphNode::setState(const StreamState &newState) {
  std::lock_guard lock(mutex);
  if (state.state == newState.state) {
    return;
  }
  state = newState;
  for (auto it = stateChangeCallbacks.begin();
       it != stateChangeCallbacks.end();) {
    auto stateChangeCallback = it->second;
    if (!stateChangeCallback(this, state)) {
      it = stateChangeCallbacks.erase(it);
    } else {
      ++it;
    }
  }
}
