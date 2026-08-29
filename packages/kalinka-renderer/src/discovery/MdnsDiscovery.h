#pragma once

#include <atomic>
#include <chrono>
#include <string>
#include <thread>
#include <vector>

#include "CoreGrouper.h"
#include "DiscoveryCache.h"

/**
 * @brief Self-contained DNS-SD browser for _kalinkaplayer._tcp.
 *
 * Vendored mdns.h, so no avahi or Bonjour daemon is required. One socket per
 * multicast-capable interface — group membership and query egress are
 * per-interface, so a wildcard socket would browse only the interface the
 * kernel happens to pick — hears announcements and goodbyes, and answers to
 * the periodic PTR queries (doubling 1s -> 60s). The interfaces are rescanned
 * while browsing: one that appears (Wi-Fi associating after boot, a cable
 * plugged in) gets a socket and an immediate query, one that loses its
 * address loses its socket, and live sockets are never churned.
 *
 * What the callbacks report are Cores, not instances: a Core announces one
 * instance per interface address and the CoreGrouper folds them into one
 * endpoint by their server_id TXT value, failing over between addresses as
 * they come and go. Endpoint changes use a replacement callback, distinct
 * from permanent removal, so a route change does not say the renderer shut
 * down and discard an active session.
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
  using AddFn = CoreGrouper::AddFn;
  using ReplaceFn = CoreGrouper::ReplaceFn;
  using RemoveFn = CoreGrouper::RemoveFn;

  MdnsDiscovery(AddFn onAdd, ReplaceFn onReplace, RemoveFn onRemove);
  ~MdnsDiscovery();

  MdnsDiscovery(const MdnsDiscovery &) = delete;
  MdnsDiscovery &operator=(const MdnsDiscovery &) = delete;

  /**
   * @brief Open the multicast sockets and start the browse thread.
   *
   * One socket per eligible interface at this moment; the browse thread keeps
   * the set reconciled as interfaces come and go. With no eligible interface a
   * wildcard socket is opened instead, yielding once a real one appears.
   *
   * @return false when no socket could be opened, leaving nothing running.
   */
  bool start();

  /// Stop and join the browse thread. Idempotent.
  void stop();

private:
  struct BrowseSocket {
    /// Empty for the wildcard fallback socket.
    std::string interface;
    std::string ip;
    int sock;
  };

  void run();
  void sendQuery();
  void drainSocket(int sock);
  /// Report every service whose lifetime ran out, and bring the next query
  /// forward if one is due for refreshing before then.
  void expireStale();
  /// Diff the held sockets against the currently eligible interfaces; open,
  /// close, or fall back to a wildcard accordingly. Returns whether anything
  /// changed. Live sockets on surviving addresses are never touched.
  bool reconcileSockets();

  CoreGrouper grouper_;
  std::vector<BrowseSocket> socks_;
  std::thread thread_;
  std::atomic<bool> stopping_{false};
  DiscoveryCache cache_;
  std::chrono::steady_clock::time_point nextQuery_;
  std::chrono::steady_clock::time_point lastQuery_;
  std::chrono::steady_clock::time_point nextRescan_;
  std::chrono::seconds queryInterval_{1};
};
