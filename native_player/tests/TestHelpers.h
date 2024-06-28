#include "StateMonitor.h"
#include "StreamState.h"

namespace {
StreamState waitForStatus(AudioGraphNode &node, AudioGraphNodeState status) {
  StateChangeWaitLock lock(std::stop_token(), node, status);

  return lock.state();
}
} // namespace