#pragma once

#include <chrono>
#include <map>
#include <optional>
#include <string>
#include <vector>

/**
 * @brief What discovery believes is out there, and for how long.
 *
 * Every mDNS answer carries a lifetime, and a Core restarts it each time it
 * answers a query. One that goes without saying goodbye — power cut, killed
 * process, a laptop closed, Wi-Fi dropped — stops answering, its entry lapses
 * here, and the renderer stops retrying an endpoint that no longer exists. A
 * goodbye is still the fast path; this is what catches everything else, and
 * what catches a goodbye that was lost in flight (it is a single unacknowledged
 * multicast).
 *
 * The announced TTL is a starting point, not the rule. This is not a DNS cache
 * but the list of endpoints a renderer keeps a reconnect loop on, so a lifetime
 * is clamped: the ceiling stops a Core that advertises hours from being
 * retried for hours after it dies, and the floor is what makes the whole thing
 * safe — several queries in a row must go unanswered before an entry lapses, so
 * one lost multicast packet can never drop a Core that is alive and well.
 *
 * @note Not thread-safe; owned by the discovery thread.
 */
class DiscoveryCache {
public:
  using Clock = std::chrono::steady_clock;

  /// Comfortably more than MdnsDiscovery's query interval, so an entry outlives
  /// several unanswered rounds.
  static constexpr std::chrono::seconds kMinLifetime{240};
  static constexpr std::chrono::seconds kMaxLifetime{600};

  /// The share of a lifetime that passes before an entry wants refreshing —
  /// early enough that a couple of queries still fit before it lapses.
  static constexpr int kRefreshPercent = 80;

  struct Lapsed {
    std::string instance;
    /// Whether the endpoint had been reported, and so must now be withdrawn.
    bool announced;
  };

  /// What was last decided about this instance, or nullopt when it is new.
  std::optional<bool> announced(const std::string &instance) const;

  /// Take an answer as current belief and restart the lifetime.
  void keep(const std::string &instance, bool announced,
            std::chrono::seconds ttl, Clock::time_point now);

  /// Restart the lifetime of an entry already held, leaving the belief alone.
  /// An unknown instance is ignored: an answer that did not resolve into an
  /// endpoint is nothing to remember.
  void touch(const std::string &instance, std::chrono::seconds ttl,
             Clock::time_point now);

  /// Forget an instance outright — a goodbye, or one that lost renderer
  /// support. It will not lapse afterwards; it is simply gone.
  void drop(const std::string &instance);

  /// Remove and return every entry whose lifetime has run out.
  std::vector<Lapsed> lapsed(Clock::time_point now);

  /// When the earliest entry wants a refreshing query, or nullopt when nothing
  /// is held.
  std::optional<Clock::time_point> nextRefresh() const;

  bool empty() const { return entries_.empty(); }

private:
  struct Entry {
    bool announced = false;
    Clock::time_point refreshAt;
    Clock::time_point expiresAt;
  };

  static Clock::duration lifetimeOf(std::chrono::seconds ttl);

  std::map<std::string, Entry> entries_;
};
