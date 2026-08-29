#include "SocketPlan.h"

#include <algorithm>
#include <set>

namespace socket_plan {

Plan plan(const std::vector<Held> &held,
          const std::vector<Candidate> &desired) {
  Plan out;
  std::set<std::string> kept;
  for (size_t i = 0; i < held.size(); ++i) {
    const auto &h = held[i];
    if (h.interface.empty()) {
      if (!desired.empty()) {
        out.close.push_back(i);
      }
      continue;
    }
    const auto candidate =
        std::find_if(desired.begin(), desired.end(), [&](const Candidate &c) {
          return c.interface == h.interface;
        });
    const bool keep =
        candidate != desired.end() &&
        std::find(candidate->addresses.begin(), candidate->addresses.end(),
                  h.ip) != candidate->addresses.end();
    if (keep) {
      kept.insert(h.interface);
    } else {
      out.close.push_back(i);
    }
  }
  for (const auto &candidate : desired) {
    if (!kept.contains(candidate.interface)) {
      out.open.push_back(candidate);
    }
  }
  return out;
}

}  // namespace socket_plan
