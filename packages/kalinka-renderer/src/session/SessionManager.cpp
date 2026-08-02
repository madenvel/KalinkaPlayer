#include "SessionManager.h"

#include <spdlog/spdlog.h>

bool SessionManager::open(const std::string &sessionId,
                          const std::string &ownerServerId,
                          std::string &busyOwner) {
  if (active_) {
    if (active_->sessionId == sessionId) {
      return true;  // duplicate SessionOpen, e.g. a retried open
    }
    busyOwner = active_->ownerServerId;
    spdlog::info("Refusing session {}: session {} is already running (owner {})",
                 sessionId, active_->sessionId, busyOwner);
    return false;
  }
  active_ = Active{sessionId, ownerServerId};
  spdlog::info("Session {} opened by server {}", sessionId, ownerServerId);
  return true;
}

bool SessionManager::close(const std::string &sessionId) {
  if (!active_ || active_->sessionId != sessionId) {
    spdlog::debug("Ignoring close of unknown session {}", sessionId);
    return false;
  }
  spdlog::info("Session {} closed", sessionId);
  // Tearing down the audio graph belongs here once playback is wired up.
  active_.reset();
  return true;
}
