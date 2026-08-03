#pragma once

#include <atomic>
#include <chrono>
#include <functional>
#include <map>
#include <string>
#include <thread>

#include "Discovery.h"

/**
 * @brief Self-contained DNS-SD browser for _kalinkaplayer._tcp.
 *
 * Vendored mdns.h, so no avahi or Bonjour daemon is required. A socket on 5353
 * hears announcements and goodbyes, and answers to the periodic PTR queries
 * (doubling 1s -> 60s).
 *
 * Servers without a matching "renderer_proto" TXT value are never reported; a
 * TXT change — a server upgrade re-announcing, say — flips them in or out, so
 * servers that would only reject us are never bombarded with connections.
 *
 * @note Callbacks run on the discovery thread and must not block. The renderer
 *       posts them onto its io_context.
 */
class MdnsDiscovery {
public:
  using AddFn = std::function<void(CoreEndpoint)>;
  using RemoveFn = std::function<void(std::string key)>;

  MdnsDiscovery(AddFn onAdd, RemoveFn onRemove);
  ~MdnsDiscovery();

  MdnsDiscovery(const MdnsDiscovery &) = delete;
  MdnsDiscovery &operator=(const MdnsDiscovery &) = delete;

  /**
   * @brief Open the multicast socket and start the browse thread.
   * @return false when the socket could not be opened, leaving nothing running.
   */
  bool start();

  /// Stop and join the browse thread. Idempotent.
  void stop();

private:
  void run();
  void sendQuery();
  void drainSocket();

  AddFn onAdd_;
  RemoveFn onRemove_;
  int sock_ = -1;
  std::thread thread_;
  std::atomic<bool> stopping_{false};
  std::map<std::string, bool> known_;  // instance -> renderer-capable
  std::chrono::steady_clock::time_point nextQuery_;
  std::chrono::seconds queryInterval_{1};
};
