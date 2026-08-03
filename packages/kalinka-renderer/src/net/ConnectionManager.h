#pragma once

#include <boost/asio.hpp>

#include <map>
#include <memory>
#include <string>

#include "../Identity.h"
#include "../discovery/Discovery.h"
#include "CoreConnection.h"

// Owns one CoreConnection per discovered Core, keyed by CoreEndpoint::key.
// add()/remove() are safe to call from any thread (they post onto the
// io_context); stop() likewise.
class ConnectionManager {
public:
  ConnectionManager(boost::asio::io_context &ioc, Identity identity,
                    std::string friendlyName,
                    std::shared_ptr<SessionManager> sessionManager);

  void add(CoreEndpoint endpoint);
  void remove(std::string key);
  void stop();

private:
  boost::asio::io_context &ioc_;
  Identity identity_;
  std::string friendlyName_;
  std::shared_ptr<SessionManager> sessionManager_;
  std::map<std::string, std::shared_ptr<CoreConnection>> connections_;
  bool stopped_ = false;
};
