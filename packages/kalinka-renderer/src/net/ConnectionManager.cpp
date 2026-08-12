#include "ConnectionManager.h"

#include <spdlog/spdlog.h>

namespace asio = boost::asio;

ConnectionManager::ConnectionManager(
    asio::io_context &ioc, Identity identity, std::string friendlyName,
    RendererServices services)
    : ioc_(ioc), identity_(std::move(identity)),
      friendlyName_(std::move(friendlyName)), services_(std::move(services)) {}

void ConnectionManager::add(CoreEndpoint endpoint) {
  asio::post(ioc_, [this, endpoint = std::move(endpoint)]() mutable {
    if (stopped_ || connections_.contains(endpoint.key)) {
      return;
    }
    connect(std::move(endpoint));
  });
}

void ConnectionManager::replace(CoreEndpoint endpoint) {
  asio::post(ioc_, [this, endpoint = std::move(endpoint)]() mutable {
    if (stopped_) {
      return;
    }
    if (const auto it = connections_.find(endpoint.key);
        it != connections_.end()) {
      it->second->retire();
      connections_.erase(it);
    }
    connect(std::move(endpoint));
  });
}

void ConnectionManager::remove(std::string key) {
  asio::post(ioc_, [this, key = std::move(key)]() {
    auto it = connections_.find(key);
    if (it == connections_.end()) {
      return;
    }
    it->second->stop();
    connections_.erase(it);
  });
}

void ConnectionManager::stop() {
  asio::post(ioc_, [this]() {
    stopped_ = true;
    for (auto &[key, connection] : connections_) {
      connection->stop();
    }
    connections_.clear();
  });
}

void ConnectionManager::connect(CoreEndpoint endpoint) {
  auto key = endpoint.key;
  auto connection = std::make_shared<CoreConnection>(
      ioc_, std::move(endpoint), identity_, friendlyName_, services_);
  connections_.emplace(std::move(key), connection);
  connection->start();
}
