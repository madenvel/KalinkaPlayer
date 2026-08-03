#include "SessionManager.h"

#include <spdlog/spdlog.h>

SessionManager::SessionManager(boost::asio::io_context &ioc,
                               std::chrono::seconds ownerGrace,
                               std::shared_ptr<Player> player)
    : ioc_(ioc), ownerGrace_(ownerGrace), player_(std::move(player)) {}

std::shared_ptr<Session> SessionManager::open(const std::string &sessionId,
                                              const std::string &ownerServerId,
                                              std::string &busyOwner) {
  if (current_) {
    if (current_->sessionId() == sessionId &&
        current_->ownerServerId() == ownerServerId) {
      return current_;
    }
    busyOwner = current_->ownerServerId();
    spdlog::info("Refusing session {}: session {} is already running "
                 "(owner {})",
                 sessionId, current_->sessionId(), busyOwner);
    return nullptr;
  }
  current_ = Session::create(
      ioc_, sessionId, ownerServerId, ownerGrace_, player_,
      [weak = weak_from_this(), sessionId]() {
        if (auto self = weak.lock()) {
          self->forget(sessionId);
        }
      });
  spdlog::info("Session {} opened by server {}", sessionId, ownerServerId);
  return current_;
}

bool SessionManager::close(const std::string &sessionId,
                           const std::string &requesterServerId) {
  // A local copy: closing clears current_ from under us via forget().
  auto session = current_;
  if (!session || session->sessionId() != sessionId) {
    spdlog::debug("Ignoring close of unknown session {}", sessionId);
    return false;
  }
  if (session->ownerServerId() != requesterServerId) {
    spdlog::warn("Server {} tried to close session {} owned by {}; ignoring",
                 requesterServerId, sessionId, session->ownerServerId());
    return false;
  }
  session->close("closed by its owner");
  return true;
}

std::shared_ptr<Session>
SessionManager::ownedBy(const std::string &serverId) const {
  if (current_ && !serverId.empty() && current_->ownerServerId() == serverId) {
    return current_;
  }
  return nullptr;
}

void SessionManager::forget(const std::string &sessionId) {
  if (current_ && current_->sessionId() == sessionId) {
    current_.reset();
  }
}
