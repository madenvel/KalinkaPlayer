#pragma once

#include <string>
#include <vector>

/**
 * @brief The browse-socket reconcile decision, free of syscalls.
 *
 * MdnsDiscovery rescans the interfaces while it runs and asks here what to do
 * with the sockets it holds: which stay untouched, which close, which
 * interfaces get a socket opened. Keeping the decision pure keeps it testable;
 * the syscalls stay in MdnsDiscovery.
 */
namespace socket_plan {

struct Held {
  /// Empty for the wildcard fallback socket.
  std::string interface;
  std::string ip;
};

struct Candidate {
  std::string interface;
  /// In enumeration order; the first address that opens wins.
  std::vector<std::string> addresses;
};

struct Plan {
  /// Indices into the held list, ascending.
  std::vector<size_t> close;
  std::vector<Candidate> open;
};

/**
 * @brief Diff held sockets against the interfaces eligible right now.
 *
 * A held socket survives untouched when its interface is still eligible and
 * its address is still among that interface's — group membership is not worth
 * churning over enumeration order. The wildcard fallback survives only while
 * no real interface is eligible.
 */
Plan plan(const std::vector<Held> &held, const std::vector<Candidate> &desired);

}  // namespace socket_plan
