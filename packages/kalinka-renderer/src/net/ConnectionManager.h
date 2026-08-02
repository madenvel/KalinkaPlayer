#pragma once

#include <boost/asio.hpp>

#include <map>
#include <memory>
#include <string>

#include "../Identity.h"
#include "../discovery/Discovery.h"
#include "Session.h"

// Owns one Session per discovered Core, keyed by CoreEndpoint::key.
// add()/remove() are safe to call from any thread (they post onto the
// io_context); stop() likewise.
class ConnectionManager {
public:
  ConnectionManager(boost::asio::io_context &ioc, Identity identity,
                    std::string friendlyName);

  void add(CoreEndpoint endpoint);
  void remove(std::string key);
  void stop();

private:
  boost::asio::io_context &ioc_;
  Identity identity_;
  std::string friendlyName_;
  std::map<std::string, std::shared_ptr<Session>> sessions_;
  bool stopped_ = false;
};
