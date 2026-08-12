#pragma once

#include <cstdint>
#include <string>

/**
 * @brief A resolved Kalinka Core on the network.
 *
 * @note `key` identifies the Core for dedupe and removal: the stable
 *       server_id when the Core advertises one in TXT, else the mDNS instance
 *       name. A Core announcing one instance per interface is therefore a
 *       single endpoint downstream, whichever instance resolved it.
 */
struct CoreEndpoint {
  std::string key;
  std::string host;  ///< Numeric address from the resolver, or a hostname.
  uint16_t port = 0;
  std::string name;      ///< display_name TXT value, or the instance name.
  std::string serverId;  ///< server_id TXT value; empty on older Cores.
};
