#include "MdnsDiscovery.h"

#include <arpa/inet.h>
#include <ifaddrs.h>
#include <net/if.h>
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
// TXT keys carried by every per-interface instance of one Core.
constexpr const char *kServerIdTxtKey = "server_id";
constexpr const char *kDisplayNameTxtKey = "display_name";
constexpr auto kMaxQueryInterval = std::chrono::seconds(60);
// Floor between refresh queries, so an entry that is overdue for one cannot
// turn the browse loop into a query flood.
constexpr auto kMinQueryGap = std::chrono::seconds(10);
constexpr size_t kBufferSize = 8192;

// Records of one received mDNS message, keyed by owner name, so a
// PTR + SRV + TXT + A response resolves in a single pass.
struct Packet {
  // Source IP of the datagram: the server's address on the interface it
  // answered from, i.e. the network we actually share with it.
  std::string sourceIp;
  // instance name -> ttl (0 = goodbye)
  std::map<std::string, uint32_t> ptrInstances;
  // instance name -> shortest ttl among its own records. The PTR may promise
  // hours while the SRV that locates the server expires in minutes; what we
  // rely on is the shorter of them.
  std::map<std::string, uint32_t> recordTtl;
  // instance name -> (target host, port)
  std::map<std::string, std::pair<std::string, uint16_t>> srv;
  // host name -> all announced IPv4 addresses (one per server interface)
  std::map<std::string, std::vector<std::string>> addresses;
  // instances whose TXT record appeared, and their renderer_proto value
  std::set<std::string> txtSeen;
  std::map<std::string, int> rendererProto;
  // instance name -> server_id / display_name TXT values, where announced
  std::map<std::string, std::string> txtServerId;
  std::map<std::string, std::string> txtDisplayName;
};

void noteTtl(Packet *packet, const std::string &instance, uint32_t ttl) {
  if (ttl == 0) {
    return;  // a goodbye says nothing about how long to keep anything
  }
  const auto [it, inserted] = packet->recordTtl.try_emplace(instance, ttl);
  if (!inserted) {
    it->second = std::min(it->second, ttl);
  }
}

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
    const std::string name(instance.str, instance.length);
    packet->ptrInstances[name] = ttl;
    noteTtl(packet, name, ttl);
    break;
  }
  case MDNS_RECORDTYPE_SRV: {
    const std::string owner = extractName(data, size, name_offset);
    const mdns_record_srv_t srv = mdns_record_parse_srv(
        data, size, record_offset, record_length, namebuf, sizeof(namebuf));
    packet->srv[owner] = {std::string(srv.name.str, srv.name.length),
                          srv.port};
    noteTtl(packet, owner, ttl);
    break;
  }
  case MDNS_RECORDTYPE_TXT: {
    const std::string owner = extractName(data, size, name_offset);
    if (!owner.ends_with(std::string(".") + kServiceType)) {
      break;
    }
    packet->txtSeen.insert(owner);
    noteTtl(packet, owner, ttl);
    mdns_record_txt_t txt[16];
    const size_t count = mdns_record_parse_txt(
        data, size, record_offset, record_length, txt, std::size(txt));
    for (size_t i = 0; i < count; ++i) {
      const std::string key(txt[i].key.str, txt[i].key.length);
      const std::string value(txt[i].value.str, txt[i].value.length);
      if (key == kRendererProtoTxtKey) {
        packet->rendererProto[owner] = std::atoi(value.c_str());
      } else if (key == kServerIdTxtKey) {
        packet->txtServerId[owner] = value;
      } else if (key == kDisplayNameTxtKey) {
        packet->txtDisplayName[owner] = value;
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

// One socket per multicast-capable interface: membership and query egress are
// per-interface properties, and a wildcard socket gets both only on the
// interface the kernel picks for it.
std::vector<int> openSockets() {
  std::vector<int> socks;
  std::set<std::string> openedInterfaces;
  ifaddrs *ifaddr = nullptr;
  if (getifaddrs(&ifaddr) == 0) {
    for (const ifaddrs *ifa = ifaddr; ifa; ifa = ifa->ifa_next) {
      if (!ifa->ifa_addr || ifa->ifa_addr->sa_family != AF_INET ||
          (ifa->ifa_flags & IFF_LOOPBACK) || !(ifa->ifa_flags & IFF_UP) ||
          !(ifa->ifa_flags & IFF_MULTICAST) ||
          openedInterfaces.contains(ifa->ifa_name)) {
        continue;
      }
      sockaddr_in saddr =
          *reinterpret_cast<const sockaddr_in *>(ifa->ifa_addr);
      saddr.sin_port = htons(MDNS_PORT);
      const int sock = mdns_socket_open_ipv4(&saddr);
      char ip[INET_ADDRSTRLEN];
      inet_ntop(AF_INET, &saddr.sin_addr, ip, sizeof(ip));
      if (sock < 0) {
        spdlog::warn("[Discovery] Could not open mDNS socket on {} ({}): {}",
                     ifa->ifa_name, ip, std::strerror(errno));
        continue;
      }
#ifdef IP_MULTICAST_ALL
      // Deliver only what arrives on this socket's own interface, so one
      // datagram is not drained once per socket.
      const int off = 0;
      setsockopt(sock, IPPROTO_IP, IP_MULTICAST_ALL, &off, sizeof(off));
#endif
      spdlog::info("[Discovery] Browsing on {} ({})", ifa->ifa_name, ip);
      socks.push_back(sock);
      // Insert only after success: if this address cannot be used, another
      // IPv4 address on the same interface still gets a chance.
      openedInterfaces.emplace(ifa->ifa_name);
    }
    freeifaddrs(ifaddr);
  }
  if (socks.empty()) {
    // No eligible interface (yet): a wildcard socket keeps things working
    // once the network comes up, as the single socket always did.
    sockaddr_in any{};
    any.sin_family = AF_INET;
    any.sin_addr.s_addr = INADDR_ANY;
    any.sin_port = htons(MDNS_PORT);
    const int sock = mdns_socket_open_ipv4(&any);
    if (sock >= 0) {
      socks.push_back(sock);
    }
  }
  return socks;
}

}  // namespace

MdnsDiscovery::MdnsDiscovery(AddFn onAdd, ReplaceFn onReplace,
                             RemoveFn onRemove)
    : grouper_(std::move(onAdd), std::move(onReplace),
               std::move(onRemove)) {}

MdnsDiscovery::~MdnsDiscovery() { stop(); }

bool MdnsDiscovery::start() {
  socks_ = openSockets();
  if (socks_.empty()) {
    spdlog::error("[Discovery] Could not open any mDNS socket (port 5353): {}",
                  std::strerror(errno));
    return false;
  }
  nextQuery_ = std::chrono::steady_clock::now();
  thread_ = std::thread([this] { run(); });
  spdlog::info("[Discovery] Browsing for {} services on {} socket(s)",
               kServiceType, socks_.size());
  return true;
}

void MdnsDiscovery::stop() {
  if (socks_.empty()) {
    return;
  }
  stopping_ = true;
  if (thread_.joinable()) {
    thread_.join();
  }
  for (const int sock : socks_) {
    mdns_socket_close(sock);
  }
  socks_.clear();
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
    int maxfd = -1;
    for (const int sock : socks_) {
      FD_SET(sock, &readfds);
      maxfd = std::max(maxfd, sock);
    }
    if (select(maxfd + 1, &readfds, nullptr, nullptr, &tv) > 0) {
      for (const int sock : socks_) {
        if (FD_ISSET(sock, &readfds)) {
          drainSocket(sock);
        }
      }
    }
    expireStale();
  }
}

void MdnsDiscovery::sendQuery() {
  lastQuery_ = std::chrono::steady_clock::now();
  std::vector<char> buffer(kBufferSize);
  for (const int sock : socks_) {
    if (mdns_query_send(sock, MDNS_RECORDTYPE_PTR, kServiceType,
                        std::strlen(kServiceType), buffer.data(), buffer.size(),
                        0) < 0) {
      spdlog::warn("[Discovery] Query send failed: {}", std::strerror(errno));
    }
  }
}

void MdnsDiscovery::drainSocket(int sock) {
  std::vector<char> buffer(kBufferSize);
  for (;;) {
    Packet packet;
    // query_id 0: accept all responses, including unsolicited announcements.
    if (mdns_query_recv(sock, buffer.data(), buffer.size(), recordCallback,
                        &packet, 0) == 0) {
      break;  // would block — no more datagrams
    }

    const auto now = std::chrono::steady_clock::now();
    for (const auto &[instance, ptrTtl] : packet.ptrInstances) {
      if (ptrTtl == 0) {
        // Goodbye packet.
        const auto announced = cache_.announced(instance);
        if (announced.has_value()) {
          cache_.drop(instance);
          spdlog::info("[Discovery] Service '{}' disappeared",
                       displayName(instance));
          if (*announced) {
            grouper_.remove(instance);
          }
        }
        continue;
      }
      // Any answer proves the service is still there, whatever else this
      // message does or does not settle.
      const auto found = packet.recordTtl.find(instance);
      const std::chrono::seconds ttl(
          found == packet.recordTtl.end() ? ptrTtl : found->second);
      cache_.touch(instance, ttl, now);

      if (!packet.txtSeen.contains(instance)) {
        continue;  // capability not judgeable from this message
      }
      const auto proto = packet.rendererProto.find(instance);
      const bool capable = proto != packet.rendererProto.end() &&
                           rendererProtocolSupported(proto->second);
      const auto announced = cache_.announced(instance);
      if (!capable) {
        if (announced.has_value() && !*announced) {
          continue;  // no change; the lifetime above is what this message added
        }
        const bool wasCapable = announced.value_or(false);
        cache_.keep(instance, false, ttl, now);
        if (wasCapable) {
          spdlog::warn("[Discovery] '{}' lost renderer support; disconnecting",
                       displayName(instance));
          grouper_.remove(instance);
        } else {
          spdlog::info(
              "[Discovery] '{}' has no renderer support (renderer_proto "
              "missing or outside {}-{}); will connect if it appears",
              displayName(instance), kMinRendererProtocolVersion,
              kMaxRendererProtocolVersion);
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
      const auto txtName = packet.txtDisplayName.find(instance);
      const auto serverId = packet.txtServerId.find(instance);
      CoreEndpoint endpoint{
          instance, pickAddress(addr->second, packet.sourceIp),
          srv->second.second,
          txtName != packet.txtDisplayName.end() && !txtName->second.empty()
              ? txtName->second
              : displayName(instance),
          serverId != packet.txtServerId.end() ? serverId->second : ""};
      if (!announced.value_or(false)) {
        cache_.keep(instance, true, ttl, now);
        spdlog::info("[Discovery] Found '{}' at {}:{}", displayName(instance),
                     endpoint.host, endpoint.port);
      }
      // Announced instances resolve again on every refresh: a changed address
      // must reach the grouper, which suppresses the no-ops.
      grouper_.add(instance, std::move(endpoint));
    }
  }
}

void MdnsDiscovery::expireStale() {
  const auto now = std::chrono::steady_clock::now();
  for (const auto &gone : cache_.lapsed(now)) {
    spdlog::info("[Discovery] '{}' stopped answering; forgetting it",
                 displayName(gone.instance));
    if (gone.announced) {
      grouper_.remove(gone.instance);
    }
  }
  // Ask again before anything we hold runs out, so a live Core is refreshed
  // rather than expired. Never sooner than kMinQueryGap after the last query:
  // an entry stays overdue for refreshing until an answer arrives, and without
  // the floor that would be a query every time round the loop.
  if (const auto refresh = cache_.nextRefresh()) {
    nextQuery_ =
        std::min(nextQuery_, std::max(*refresh, lastQuery_ + kMinQueryGap));
  }
}
