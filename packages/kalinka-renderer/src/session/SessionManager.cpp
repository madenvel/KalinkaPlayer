#include "SessionManager.h"

#include <spdlog/spdlog.h>

namespace asio = boost::asio;

SessionManager::SessionManager(asio::io_context &ioc,
                                               std::chrono::seconds ownerGrace)
    : ioc_(ioc), ownerGrace_(ownerGrace), graceTimer_(ioc) {}

void SessionManager::coreConnected(const std::string &serverId) {
  if (serverId.empty()) {
    return;
  }
  ++connectedCores_[serverId];
  if (active_ && serverId == active_->ownerServerId) {
    graceTimer_.cancel();
  }
}

void SessionManager::coreDisconnected(const std::string &serverId) {
  if (serverId.empty()) {
    return;
  }
  auto it = connectedCores_.find(serverId);
  if (it == connectedCores_.end()) {
    return;
  }
  if (--it->second <= 0) {
    connectedCores_.erase(it);
  }
  if (active_ && serverId == active_->ownerServerId && !ownerConnected()) {
    ownerLost();
  }
}

bool SessionManager::open(const std::string &sessionId,
                                  const std::string &ownerServerId,
                                  std::string &busyOwner) {
  if (active_) {
    // Same id from the same owner is a retried open; from anyone else it is a
    // replay of an id we broadcast in Hello, and must not hand over the graph.
    if (active_->sessionId == sessionId &&
        active_->ownerServerId == ownerServerId) {
      return true;
    }
    busyOwner = active_->ownerServerId;
    spdlog::info("Refusing session {}: session {} is already running (owner {})",
                 sessionId, active_->sessionId, busyOwner);
    return false;
  }
  active_ = Active{sessionId, ownerServerId};
  graceTimer_.cancel();
  spdlog::info("Session {} opened by server {}", sessionId, ownerServerId);
  return true;
}

bool SessionManager::close(const std::string &sessionId,
                                   const std::string &requesterServerId) {
  if (!active_ || active_->sessionId != sessionId) {
    spdlog::debug("Ignoring close of unknown session {}", sessionId);
    return false;
  }
  if (active_->ownerServerId != requesterServerId) {
    spdlog::warn("Server {} tried to close session {} owned by {}; ignoring",
                 requesterServerId, sessionId, active_->ownerServerId);
    return false;
  }
  release("closed by its owner");
  return true;
}

void SessionManager::setPlaybackState(PlaybackState state) {
  playbackState_ = state;
  if (state == PlaybackState::Stopped && active_ && !ownerConnected()) {
    release("playback stopped while the owner was away");
  }
}

bool SessionManager::ownerConnected() const {
  return active_ && connectedCores_.contains(active_->ownerServerId);
}

void SessionManager::ownerLost() {
  if (playbackState_ == PlaybackState::Stopped) {
    release("owner disconnected and nothing is playing");
    return;
  }
  spdlog::info(
      "Session {} owner {} disconnected; releasing in {}s unless it returns",
      active_->sessionId, active_->ownerServerId, ownerGrace_.count());
  graceTimer_.expires_after(ownerGrace_);
  graceTimer_.async_wait(
      [weak = weak_from_this()](const boost::system::error_code &ec) {
        if (ec) {
          return;  // cancelled: the owner came back, or we shut down
        }
        if (auto self = weak.lock(); self && !self->ownerConnected()) {
          self->release("owner did not return");
        }
      });
}

void SessionManager::release(const char *reason) {
  if (!active_) {
    return;
  }
  spdlog::info("Releasing session {} (owner {}): {}", active_->sessionId,
               active_->ownerServerId, reason);
  // Tearing down the audio graph belongs here once playback is wired up.
  active_.reset();
  playbackState_ = PlaybackState::Stopped;
  graceTimer_.cancel();
}
