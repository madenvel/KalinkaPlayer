#pragma once

#include <cstdint>
#include <string>

// A resolved Kalinka Core on the network. `key` identifies the service
// instance for dedupe/removal; until the Core advertises a stable server_id
// in TXT (see design doc §6.1) the mDNS instance name is used.
struct CoreEndpoint {
  std::string key;
  std::string host;  // numeric address from the resolver, or a hostname
  uint16_t port = 0;
  std::string name;  // service instance name, for logging
};
