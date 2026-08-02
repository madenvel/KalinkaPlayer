#pragma once

#include <atomic>
#include <chrono>
#include <functional>
#include <set>
#include <string>
#include <thread>

#include "Discovery.h"

// Self-contained DNS-SD browser for _kalinkaplayer._tcp built on the vendored
// public-domain mdns.h (third_party/mdns) — plain multicast sockets, no
// avahi/Bonjour daemon dependency, same code on every platform.
//
// One socket bound to port 5353 (joined to the mDNS group) both hears
// unsolicited announcements/goodbyes and receives responses to the periodic
// PTR queries this class sends (interval doubling 1s -> 60s, per RFC 6762).
// A service is added when a response carries PTR+SRV+A together — which the
// Core's python-zeroconf does, per DNS-SD's additional-records rule — and
// removed on a goodbye (PTR with TTL 0).
//
// Callbacks are invoked on the discovery thread and must not block; the
// ConnectionManager posts them onto the io_context.
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
  std::set<std::string> known_;  // instance names currently reported as added
  std::chrono::steady_clock::time_point nextQuery_;
  std::chrono::seconds queryInterval_{1};
};
