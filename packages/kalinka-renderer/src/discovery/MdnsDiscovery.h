#pragma once

#include <atomic>
#include <chrono>
#include <functional>
#include <map>
#include <string>
#include <thread>

#include "Discovery.h"
#include "DiscoveryCache.h"

/**
 * @brief Self-contained DNS-SD browser for _kalinkaplayer._tcp.
 *
 * Vendored mdns.h, so no avahi or Bonjour daemon is required. A socket on 5353
 * hears announcements and goodbyes, and answers to the periodic PTR queries
 * (doubling 1s -> 60s).
 *
 * Servers whose "renderer_proto" TXT value falls outside the range this binary
 * speaks are never reported; a TXT change — a server upgrade re-announcing,
 * say — flips them in or out, so servers that would only reject us are never
 * bombarded with connections.
 *
 * A server is reported gone when it says goodbye, and also when it simply stops
 * answering: every answer restarts its lifetime in the DiscoveryCache, and one
 * that runs out is removed. Without that, a Core that died without a goodbye
 * would be reconnected to for as long as the renderer runs.
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
  /// Report every service whose lifetime ran out, and bring the next query
  /// forward if one is due for refreshing before then.
  void expireStale();

  AddFn onAdd_;
  RemoveFn onRemove_;
  int sock_ = -1;
  std::thread thread_;
  std::atomic<bool> stopping_{false};
  DiscoveryCache cache_;
  std::chrono::steady_clock::time_point nextQuery_;
  std::chrono::steady_clock::time_point lastQuery_;
  std::chrono::seconds queryInterval_{1};
};
