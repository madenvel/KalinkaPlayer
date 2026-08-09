#pragma once

#include <cstdint>
#include <string>

/**
 * @brief A resolved Kalinka Core on the network.
 *
 * @note `key` identifies the service instance for dedupe and removal. Until a
 *       Core advertises a stable server_id in TXT, the mDNS instance name
 *       stands in, so the same Core seen under two instance names is two
 *       endpoints.
 */
struct CoreEndpoint {
  std::string key;
  std::string host;  ///< Numeric address from the resolver, or a hostname.
  uint16_t port = 0;
  std::string name;  ///< Service instance name, for logging.
};
