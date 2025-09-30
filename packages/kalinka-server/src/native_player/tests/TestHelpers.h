#include "StateMonitor.h"
#include "StreamState.h"

namespace {
StreamState
waitForStatus(AudioGraphNode &node, AudioGraphNodeState status,
              std::optional<std::chrono::milliseconds> timeout = std::nullopt) {
  StateChangeWaitLock lock(std::stop_token(), node, status, timeout);

  return lock.state();
}
} // namespace