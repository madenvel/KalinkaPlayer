#include "CoreGrouper.h"

#include <spdlog/spdlog.h>

#include <utility>

CoreGrouper::CoreGrouper(AddFn onAdd, ReplaceFn onReplace, RemoveFn onRemove)
    : onAdd_(std::move(onAdd)), onReplace_(std::move(onReplace)),
      onRemove_(std::move(onRemove)) {}

void CoreGrouper::add(const std::string &instance, CoreEndpoint endpoint) {
  const std::string key =
      endpoint.serverId.empty() ? instance : endpoint.serverId;
  endpoint.key = key;

  const auto known = groupOf_.find(instance);
  if (known != groupOf_.end() && known->second != key) {
    // The Core behind this instance changed identity — an upgrade started
    // advertising server_id, say. Withdraw it from the group it was in.
    remove(instance);
  }
  groupOf_[instance] = key;

  auto &group = groups_[key];
  if (group.members.empty()) {
    group.active = instance;
    group.members[instance] = std::move(endpoint);
    onAdd_(group.members[instance]);
    return;
  }

  const auto member = group.members.find(instance);
  const bool moved = instance == group.active &&
                     member != group.members.end() &&
                     (member->second.host != endpoint.host ||
                      member->second.port != endpoint.port);
  group.members[instance] = std::move(endpoint);
  if (moved) {
    const CoreEndpoint &fresh = group.members.at(instance);
    spdlog::info("[Discovery] '{}' moved to {}:{}; reconnecting", fresh.name,
                 fresh.host, fresh.port);
    onReplace_(fresh);
  }
}

void CoreGrouper::remove(const std::string &instance) {
  const auto known = groupOf_.find(instance);
  if (known == groupOf_.end()) {
    return;
  }
  const std::string key = known->second;
  groupOf_.erase(known);

  auto &group = groups_.at(key);
  group.members.erase(instance);
  if (group.members.empty()) {
    groups_.erase(key);
    onRemove_(key);
    return;
  }
  if (group.active != instance) {
    return;  // an alternate went; the forwarded endpoint stands
  }
  group.active = group.members.begin()->first;
  const CoreEndpoint fallback = group.members.at(group.active);
  spdlog::info("[Discovery] '{}' lost its endpoint; failing over to {}:{}",
               fallback.name, fallback.host, fallback.port);
  onReplace_(fallback);
}
