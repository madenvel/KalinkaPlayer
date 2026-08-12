#pragma once

#include <functional>
#include <map>
#include <string>

#include "Discovery.h"

/**
 * @brief Groups per-interface service instances into Cores by server_id.
 *
 * A Core announces one single-address instance per interface, every one
 * carrying the same server_id TXT value. The first instance to resolve is
 * forwarded as the Core's endpoint; further ones are held as alternates. A
 * removal is forwarded only when the last instance is gone, and losing the
 * instance behind the forwarded endpoint while an alternate remains fails
 * over instead — a removal, then the alternate — moving the connection to an
 * address whose announcements still arrive.
 *
 * An instance without a server_id (a Core predating the TXT value) forms its
 * own group, keyed by instance name.
 *
 * @note Not thread-safe; owned by the discovery thread.
 */
class CoreGrouper {
public:
  using AddFn = std::function<void(CoreEndpoint)>;
  using RemoveFn = std::function<void(std::string key)>;

  CoreGrouper(AddFn onAdd, RemoveFn onRemove);

  /**
   * @brief Take a resolved instance; duplicates are expected and cheap.
   *
   * Forwards the endpoint, keyed by its Core, when it is the Core's first.
   * A host or port change on the member behind the forwarded endpoint is
   * re-forwarded (a removal, then the new endpoint) so the connection follows
   * a Core that moved address; everything else is recorded silently.
   * @p endpoint.key is overwritten with the group key; @p instance is the
   * handle remove() is called with.
   */
  void add(const std::string &instance, CoreEndpoint endpoint);

  /// Take an instance's goodbye or lapse. Unknown instances are ignored.
  void remove(const std::string &instance);

private:
  struct Group {
    std::map<std::string, CoreEndpoint> members;  // instance -> endpoint
    std::string active;  // the member behind the forwarded endpoint
  };

  AddFn onAdd_;
  RemoveFn onRemove_;
  std::map<std::string, Group> groups_;         // key: server_id or instance
  std::map<std::string, std::string> groupOf_;  // instance -> group key
};
