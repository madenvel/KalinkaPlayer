#include "MdnsDiscovery.h"

#include <arpa/inet.h>
#include <ifaddrs.h>
#include <spdlog/fmt/ranges.h>
#include <spdlog/spdlog.h>
#include <sys/select.h>

#include <algorithm>
#include <cstring>
#include <map>
#include <set>
#include <vector>

#include "../Protocol.h"
#include "mdns.h"

namespace {

constexpr const char *kServiceType = "_kalinkaplayer._tcp.local.";
constexpr auto kMaxQueryInterval = std::chrono::seconds(60);
constexpr size_t kBufferSize = 8192;

// Records of one received mDNS message, keyed by owner name, so a
// PTR + SRV + TXT + A response resolves in a single pass.
struct Packet {
  // Source IP of the datagram: the server's address on the interface it
  // answered from, i.e. the network we actually share with it.
  std::string sourceIp;
  // instance name -> ttl (0 = goodbye)
  std::map<std::string, uint32_t> ptrInstances;
  // instance name -> (target host, port)
  std::map<std::string, std::pair<std::string, uint16_t>> srv;
  // host name -> all announced IPv4 addresses (one per server interface)
  std::map<std::string, std::vector<std::string>> addresses;
  // instances whose TXT record appeared, and their renderer_proto value
  std::set<std::string> txtSeen;
  std::map<std::string, int> rendererProto;
};

std::string extractName(const void *data, size_t size, size_t offset) {
  char buf[256];
  const mdns_string_t s =
      mdns_string_extract(data, size, &offset, buf, sizeof(buf));
  return std::string(s.str, s.length);
}

int recordCallback(int /*sock*/, const struct sockaddr *from,
                   size_t /*addrlen*/, mdns_entry_type_t entry,
                   uint16_t /*query_id*/, uint16_t rtype, uint16_t /*rclass*/,
                   uint32_t ttl, const void *data, size_t size,
                   size_t name_offset, size_t /*name_length*/,
                   size_t record_offset, size_t record_length,
                   void *user_data) {
  if (entry == MDNS_ENTRYTYPE_QUESTION) {
    return 0;
  }
  auto *packet = static_cast<Packet *>(user_data);
  if (packet->sourceIp.empty() && from && from->sa_family == AF_INET) {
    char ip[INET_ADDRSTRLEN];
    const auto *sin = reinterpret_cast<const sockaddr_in *>(from);
    if (inet_ntop(AF_INET, &sin->sin_addr, ip, sizeof(ip))) {
      packet->sourceIp = ip;
    }
  }
  char namebuf[256];

  switch (rtype) {
  case MDNS_RECORDTYPE_PTR: {
    // Only our service type; the 5353-bound socket sees all mDNS traffic.
    if (extractName(data, size, name_offset) != kServiceType) {
      break;
    }
    const mdns_string_t instance = mdns_record_parse_ptr(
        data, size, record_offset, record_length, namebuf, sizeof(namebuf));
    packet->ptrInstances[std::string(instance.str, instance.length)] = ttl;
    break;
  }
  case MDNS_RECORDTYPE_SRV: {
    const std::string owner = extractName(data, size, name_offset);
    const mdns_record_srv_t srv = mdns_record_parse_srv(
        data, size, record_offset, record_length, namebuf, sizeof(namebuf));
    packet->srv[owner] = {std::string(srv.name.str, srv.name.length),
                          srv.port};
    break;
  }
  case MDNS_RECORDTYPE_TXT: {
    const std::string owner = extractName(data, size, name_offset);
    if (!owner.ends_with(std::string(".") + kServiceType)) {
      break;
    }
    packet->txtSeen.insert(owner);
    mdns_record_txt_t txt[16];
    const size_t count = mdns_record_parse_txt(
        data, size, record_offset, record_length, txt, std::size(txt));
    for (size_t i = 0; i < count; ++i) {
      if (std::string(txt[i].key.str, txt[i].key.length) ==
          kRendererProtoTxtKey) {
        packet->rendererProto[owner] =
            std::atoi(std::string(txt[i].value.str, txt[i].value.length)
                          .c_str());
      }
    }
    break;
  }
  case MDNS_RECORDTYPE_A: {
    const std::string owner = extractName(data, size, name_offset);
    sockaddr_in addr{};
    mdns_record_parse_a(data, size, record_offset, record_length, &addr);
    char ip[INET_ADDRSTRLEN];
    if (inet_ntop(AF_INET, &addr.sin_addr, ip, sizeof(ip))) {
      auto &list = packet->addresses[owner];
      if (std::find(list.begin(), list.end(), ip) == list.end()) {
        list.emplace_back(ip);
      }
    }
    break;
  }
  default:
    break;
  }
  return 0;
}

// Pick the reachable address when the Core announces one A record per
// interface (it may sit on networks we cannot route to, e.g. an internal
// bridge). Preference order:
//   1. the datagram's source address — the Core's IP on the network the
//      answer actually travelled over;
//   2. an address on the same subnet as one of our own interfaces;
//   3. the first announced address (nothing better to go on).
std::string pickAddress(const std::vector<std::string> &candidates,
                        const std::string &sourceIp) {
  if (std::find(candidates.begin(), candidates.end(), sourceIp) !=
      candidates.end()) {
    return sourceIp;
  }

  ifaddrs *ifaddr = nullptr;
  if (getifaddrs(&ifaddr) == 0) {
    for (const ifaddrs *ifa = ifaddr; ifa; ifa = ifa->ifa_next) {
      if (!ifa->ifa_addr || !ifa->ifa_netmask ||
          ifa->ifa_addr->sa_family != AF_INET) {
        continue;
      }
      const auto local =
          reinterpret_cast<const sockaddr_in *>(ifa->ifa_addr)->sin_addr.s_addr;
      const auto mask = reinterpret_cast<const sockaddr_in *>(ifa->ifa_netmask)
                            ->sin_addr.s_addr;
      for (const auto &candidate : candidates) {
        in_addr addr{};
        if (inet_pton(AF_INET, candidate.c_str(), &addr) == 1 &&
            (addr.s_addr & mask) == (local & mask)) {
          freeifaddrs(ifaddr);
          return candidate;
        }
      }
    }
    freeifaddrs(ifaddr);
  }

  spdlog::warn(
      "[Discovery] None of the announced addresses ({}) is on a local "
      "subnet; trying {}",
      fmt::join(candidates, ", "), candidates.front());
  return candidates.front();
}

// "My Kalinka Service._kalinkaplayer._tcp.local." -> "My Kalinka Service"
std::string displayName(const std::string &instance) {
  const auto pos = instance.find(std::string(".") + kServiceType);
  return pos == std::string::npos ? instance : instance.substr(0, pos);
}

}  // namespace

MdnsDiscovery::MdnsDiscovery(AddFn onAdd, RemoveFn onRemove)
    : onAdd_(std::move(onAdd)), onRemove_(std::move(onRemove)) {}

MdnsDiscovery::~MdnsDiscovery() { stop(); }

bool MdnsDiscovery::start() {
  sockaddr_in saddr{};
  saddr.sin_family = AF_INET;
  saddr.sin_addr.s_addr = INADDR_ANY;
  saddr.sin_port = htons(MDNS_PORT);
  sock_ = mdns_socket_open_ipv4(&saddr);
  if (sock_ < 0) {
    spdlog::error("[Discovery] Could not open mDNS socket (port 5353): {}",
                  std::strerror(errno));
    return false;
  }
  nextQuery_ = std::chrono::steady_clock::now();
  thread_ = std::thread([this] { run(); });
  spdlog::info("[Discovery] Browsing for {} services", kServiceType);
  return true;
}

void MdnsDiscovery::stop() {
  if (sock_ < 0) {
    return;
  }
  stopping_ = true;
  if (thread_.joinable()) {
    thread_.join();
  }
  mdns_socket_close(sock_);
  sock_ = -1;
}

void MdnsDiscovery::run() {
  while (!stopping_) {
    const auto now = std::chrono::steady_clock::now();
    if (now >= nextQuery_) {
      sendQuery();
      nextQuery_ = now + queryInterval_;
      queryInterval_ = std::min(queryInterval_ * 2, kMaxQueryInterval);
    }

    // Wake at least twice a second so stop() is prompt.
    const auto until = std::chrono::duration_cast<std::chrono::milliseconds>(
        nextQuery_ - std::chrono::steady_clock::now());
    const auto wait = std::clamp(until, std::chrono::milliseconds(0),
                                 std::chrono::milliseconds(500));
    timeval tv{};
    tv.tv_sec = static_cast<time_t>(wait.count() / 1000);
    tv.tv_usec = static_cast<suseconds_t>((wait.count() % 1000) * 1000);
    fd_set readfds;
    FD_ZERO(&readfds);
    FD_SET(sock_, &readfds);
    if (select(sock_ + 1, &readfds, nullptr, nullptr, &tv) > 0 &&
        FD_ISSET(sock_, &readfds)) {
      drainSocket();
    }
  }
}

void MdnsDiscovery::sendQuery() {
  std::vector<char> buffer(kBufferSize);
  if (mdns_query_send(sock_, MDNS_RECORDTYPE_PTR, kServiceType,
                      std::strlen(kServiceType), buffer.data(), buffer.size(),
                      0) < 0) {
    spdlog::warn("[Discovery] Query send failed: {}", std::strerror(errno));
  }
}

void MdnsDiscovery::drainSocket() {
  std::vector<char> buffer(kBufferSize);
  for (;;) {
    Packet packet;
    // query_id 0: accept all responses, including unsolicited announcements.
    if (mdns_query_recv(sock_, buffer.data(), buffer.size(), recordCallback,
                        &packet, 0) == 0) {
      break;  // would block — no more datagrams
    }

    for (const auto &[instance, ttl] : packet.ptrInstances) {
      if (ttl == 0) {
        // Goodbye packet.
        const auto it = known_.find(instance);
        if (it != known_.end()) {
          const bool wasCapable = it->second;
          known_.erase(it);
          spdlog::info("[Discovery] Service '{}' disappeared",
                       displayName(instance));
          if (wasCapable) {
            onRemove_(instance);
          }
        }
        continue;
      }
      if (!packet.txtSeen.contains(instance)) {
        continue;  // capability not judgeable from this message
      }
      const auto proto = packet.rendererProto.find(instance);
      const bool capable =
          proto != packet.rendererProto.end() &&
          proto->second == static_cast<int>(kRendererProtocolVersion);
      const auto it = known_.find(instance);
      if (it != known_.end() && it->second == capable) {
        continue;  // no change
      }
      if (!capable) {
        const bool wasCapable = it != known_.end() && it->second;
        known_[instance] = false;
        if (wasCapable) {
          spdlog::warn("[Discovery] '{}' lost renderer support; disconnecting",
                       displayName(instance));
          onRemove_(instance);
        } else {
          spdlog::info(
              "[Discovery] '{}' has no renderer support (renderer_proto "
              "missing or != {}); will connect if it appears",
              displayName(instance), kRendererProtocolVersion);
        }
        continue;
      }
      const auto srv = packet.srv.find(instance);
      if (srv == packet.srv.end()) {
        continue;  // no SRV in this message; a later response will carry it
      }
      const auto addr = packet.addresses.find(srv->second.first);
      if (addr == packet.addresses.end() || addr->second.empty()) {
        continue;
      }
      known_[instance] = true;
      CoreEndpoint endpoint{instance,
                            pickAddress(addr->second, packet.sourceIp),
                            srv->second.second, displayName(instance)};
      spdlog::info("[Discovery] Found '{}' at {}:{}", endpoint.name,
                   endpoint.host, endpoint.port);
      onAdd_(std::move(endpoint));
    }
  }
}
