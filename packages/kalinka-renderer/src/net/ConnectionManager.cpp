#include "ConnectionManager.h"

#include <spdlog/spdlog.h>

namespace asio = boost::asio;

ConnectionManager::ConnectionManager(asio::io_context &ioc, Identity identity,
                                     std::string friendlyName,
                                     SessionManager &sessions)
    : ioc_(ioc), identity_(std::move(identity)),
      friendlyName_(std::move(friendlyName)), sessionManager_(sessions) {}

void ConnectionManager::add(CoreEndpoint endpoint) {
  asio::post(ioc_, [this, endpoint = std::move(endpoint)]() mutable {
    if (stopped_ || sessions_.contains(endpoint.key)) {
      // Duplicate records for one instance (multiple interfaces) are expected;
      // one session per Core.
      return;
    }
    auto key = endpoint.key;
    auto session = std::make_shared<Session>(
        ioc_, std::move(endpoint), identity_, friendlyName_, sessionManager_);
    sessions_.emplace(std::move(key), session);
    session->start();
  });
}

void ConnectionManager::remove(std::string key) {
  asio::post(ioc_, [this, key = std::move(key)]() {
    auto it = sessions_.find(key);
    if (it == sessions_.end()) {
      return;
    }
    it->second->stop();
    sessions_.erase(it);
  });
}

void ConnectionManager::stop() {
  asio::post(ioc_, [this]() {
    stopped_ = true;
    for (auto &[key, session] : sessions_) {
      session->stop();
    }
    sessions_.clear();
  });
}
