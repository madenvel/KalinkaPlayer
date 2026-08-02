#pragma once

#include <atomic>
#include <chrono>
#include <functional>
#include <map>
#include <string>
#include <thread>

#include "Discovery.h"

// Self-contained DNS-SD browser for _kalinkaplayer._tcp (vendored mdns.h, no
// avahi/Bonjour daemon). Socket on 5353 hears announcements/goodbyes and
// answers to the periodic PTR queries (doubling 1s -> 60s). Servers without a
// matching "renderer_proto" TXT value are never connected; a TXT change (e.g.
// a server upgrade re-announcing) flips them in or out, so no request
// bombardment of servers that would only reject us. Callbacks run on the
// discovery thread and must not block.
class MdnsDiscovery {
public:
  using AddFn = std::function<void(CoreEndpoint)>;
  using RemoveFn = std::function<void(std::string key)>;

  MdnsDiscovery(AddFn onAdd, RemoveFn onRemove);
  ~MdnsDiscovery();

  MdnsDiscovery(const MdnsDiscovery &) = delete;
  MdnsDiscovery &operator=(const MdnsDiscovery &) = delete;

  // Opens the multicast socket and starts the browse thread.
  bool start();

  // Stops and joins the browse thread. Idempotent.
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
