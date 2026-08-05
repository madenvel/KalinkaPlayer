#include "DiscoveryCache.h"

#include <algorithm>

DiscoveryCache::Clock::duration DiscoveryCache::lifetimeOf(
    std::chrono::seconds ttl) {
  return std::clamp(ttl, kMinLifetime, kMaxLifetime);
}

std::optional<bool> DiscoveryCache::announced(
    const std::string &instance) const {
  const auto it = entries_.find(instance);
  if (it == entries_.end()) {
    return std::nullopt;
  }
  return it->second.announced;
}

void DiscoveryCache::keep(const std::string &instance, bool announced,
                          std::chrono::seconds ttl, Clock::time_point now) {
  const auto lifetime = lifetimeOf(ttl);
  Entry &entry = entries_[instance];
  entry.announced = announced;
  entry.expiresAt = now + lifetime;
  entry.refreshAt = now + lifetime * kRefreshPercent / 100;
}

void DiscoveryCache::touch(const std::string &instance,
                           std::chrono::seconds ttl, Clock::time_point now) {
  const auto it = entries_.find(instance);
  if (it == entries_.end()) {
    return;
  }
  keep(instance, it->second.announced, ttl, now);
}

void DiscoveryCache::drop(const std::string &instance) {
  entries_.erase(instance);
}

std::vector<DiscoveryCache::Lapsed> DiscoveryCache::lapsed(
    Clock::time_point now) {
  std::vector<Lapsed> gone;
  for (auto it = entries_.begin(); it != entries_.end();) {
    if (it->second.expiresAt <= now) {
      gone.push_back({it->first, it->second.announced});
      it = entries_.erase(it);
    } else {
      ++it;
    }
  }
  return gone;
}

std::optional<DiscoveryCache::Clock::time_point> DiscoveryCache::nextRefresh()
    const {
  std::optional<Clock::time_point> earliest;
  for (const auto &[_, entry] : entries_) {
    if (!earliest || entry.refreshAt < *earliest) {
      earliest = entry.refreshAt;
    }
  }
  return earliest;
}
