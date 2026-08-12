#include <gtest/gtest.h>

#include <string>
#include <utility>
#include <vector>

#include "../src/discovery/CoreGrouper.h"

namespace {

const std::string kEth = "My Kalinka Service (eth0)._kalinkaplayer._tcp.local.";
const std::string kWlan =
    "My Kalinka Service (wlan0)._kalinkaplayer._tcp.local.";
// What a Core announced before it advertised server_id: one unsuffixed
// instance.
const std::string kLegacy = "My Kalinka Service._kalinkaplayer._tcp.local.";
const std::string kServerId = "9f1c9f2e-1111-2222-3333-444455556666";

CoreEndpoint endpoint(const std::string &host, const std::string &serverId) {
  return CoreEndpoint{"", host, 8000, "My Kalinka Service", serverId};
}

struct Forwarded {
  std::vector<CoreEndpoint> added;
  std::vector<CoreEndpoint> replaced;
  std::vector<std::string> removed;
  CoreGrouper grouper{
      [this](CoreEndpoint ep) { added.push_back(std::move(ep)); },
      [this](CoreEndpoint ep) { replaced.push_back(std::move(ep)); },
      [this](std::string key) { removed.push_back(std::move(key)); }};
};

TEST(CoreGrouper, InstancesSharingAServerIdAreOneCore) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));

  ASSERT_EQ(f.added.size(), 1u);
  EXPECT_EQ(f.added[0].key, kServerId);
  EXPECT_EQ(f.added[0].host, "192.168.1.20");
  EXPECT_TRUE(f.replaced.empty());
  EXPECT_TRUE(f.removed.empty());
}

TEST(CoreGrouper, LosingAnAlternateLeavesTheForwardedEndpointStanding) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));

  f.grouper.remove(kWlan);

  EXPECT_EQ(f.added.size(), 1u);
  EXPECT_TRUE(f.replaced.empty());
  EXPECT_TRUE(f.removed.empty());
}

TEST(CoreGrouper, LosingTheForwardedEndpointFailsOverToTheAlternate) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));

  f.grouper.remove(kEth);

  EXPECT_TRUE(f.removed.empty());
  ASSERT_EQ(f.replaced.size(), 1u);
  EXPECT_EQ(f.replaced[0].key, kServerId);
  EXPECT_EQ(f.replaced[0].host, "10.20.0.15");
  EXPECT_EQ(f.added.size(), 1u);
}

TEST(CoreGrouper, LosingTheLastInstanceWithdrawsTheCore) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));

  f.grouper.remove(kEth);
  f.grouper.remove(kWlan);

  ASSERT_EQ(f.removed.size(), 1u);
  EXPECT_EQ(f.removed[0], kServerId);
  EXPECT_EQ(f.added.size(), 1u) << "nothing new was added during failover";
  EXPECT_EQ(f.replaced.size(), 1u) << "the first removal used the alternate";
}

TEST(CoreGrouper, ACoreWithoutAServerIdIsKeyedByItsInstance) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", ""));

  ASSERT_EQ(f.added.size(), 1u);
  EXPECT_EQ(f.added[0].key, kEth);
  EXPECT_TRUE(f.replaced.empty());

  f.grouper.remove(kEth);
  ASSERT_EQ(f.removed.size(), 1u);
  EXPECT_EQ(f.removed[0], kEth);
}

TEST(CoreGrouper, DistinctServerIdsAreDistinctCores) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", "core-a"));
  f.grouper.add(kWlan, endpoint("10.20.0.15", "core-b"));

  ASSERT_EQ(f.added.size(), 2u);
  EXPECT_EQ(f.added[0].key, "core-a");
  EXPECT_EQ(f.added[1].key, "core-b");
  EXPECT_TRUE(f.replaced.empty());
}

TEST(CoreGrouper, AnInstanceGainingAServerIdMovesToItsCoreGroup) {
  // An upgraded Core: the same instance re-resolves, now with a server_id.
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", ""));
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  ASSERT_EQ(f.removed.size(), 1u);
  EXPECT_EQ(f.removed[0], kEth) << "the instance-keyed endpoint is withdrawn";
  ASSERT_EQ(f.added.size(), 2u);
  EXPECT_EQ(f.added[1].key, kServerId);
  EXPECT_TRUE(f.replaced.empty());
}

TEST(CoreGrouper, ReResolvingAnInstanceForwardsNothingNew) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  EXPECT_EQ(f.added.size(), 1u);
  EXPECT_TRUE(f.replaced.empty());
  EXPECT_TRUE(f.removed.empty());
}

TEST(CoreGrouper, TheActiveInstanceChangingAddressIsReForwarded) {
  // A Core restarted with a new DHCP lease, no goodbye sent.
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  f.grouper.add(kEth, endpoint("192.168.1.99", kServerId));

  EXPECT_TRUE(f.removed.empty());
  ASSERT_EQ(f.replaced.size(), 1u);
  EXPECT_EQ(f.replaced[0].key, kServerId);
  EXPECT_EQ(f.replaced[0].host, "192.168.1.99");
  EXPECT_EQ(f.added.size(), 1u);
}

TEST(CoreGrouper, AChangedPortOnTheActiveInstanceIsReForwarded) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  auto moved = endpoint("192.168.1.20", kServerId);
  moved.port = 9000;
  f.grouper.add(kEth, std::move(moved));

  EXPECT_EQ(f.added.size(), 1u);
  ASSERT_EQ(f.replaced.size(), 1u);
  EXPECT_EQ(f.replaced[0].port, 9000);
}

TEST(CoreGrouper, ANameOnlyChangeIsRecordedSilently) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  auto renamed = endpoint("192.168.1.20", kServerId);
  renamed.name = "My Renamed Service";
  f.grouper.add(kEth, std::move(renamed));

  EXPECT_EQ(f.added.size(), 1u);
  EXPECT_TRUE(f.replaced.empty());
  EXPECT_TRUE(f.removed.empty());
}

TEST(CoreGrouper, AnAlternateChangingAddressIsHeldForTheNextFailover) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));

  f.grouper.add(kWlan, endpoint("10.20.0.99", kServerId));
  EXPECT_EQ(f.added.size(), 1u) << "the active connection stands";
  EXPECT_TRUE(f.replaced.empty());

  f.grouper.remove(kEth);
  EXPECT_EQ(f.added.size(), 1u);
  ASSERT_EQ(f.replaced.size(), 1u);
  EXPECT_EQ(f.replaced[0].host, "10.20.0.99");
}

TEST(CoreGrouper, AnInstanceReturningAfterFailoverBecomesAnAlternate) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));
  f.grouper.remove(kEth);  // failed over to wlan0

  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  EXPECT_EQ(f.added.size(), 1u) << "the wlan0 connection stands";
  ASSERT_EQ(f.replaced.size(), 1u);

  f.grouper.remove(kWlan);
  EXPECT_EQ(f.added.size(), 1u);
  ASSERT_EQ(f.replaced.size(), 2u) << "and eth0 is there to fail over to";
  EXPECT_EQ(f.replaced[1].host, "192.168.1.20");
}

// A legacy Core killed uncleanly, restarted advertising server_id: same
// listener, different key. The stale record must go before its reconnect
// loop and the new connection fight over the renderer_id.
TEST(CoreGrouper, TheSameEndpointUnderANewIdentitySupersedesTheStaleRecord) {
  Forwarded f;
  f.grouper.add(kLegacy, endpoint("192.168.50.85", ""));

  f.grouper.add(kEth, endpoint("192.168.50.85", kServerId));

  ASSERT_EQ(f.removed.size(), 1u);
  EXPECT_EQ(f.removed[0], kLegacy);
  ASSERT_EQ(f.added.size(), 2u);
  EXPECT_EQ(f.added[1].key, kServerId);
  EXPECT_TRUE(f.replaced.empty());
}

TEST(CoreGrouper, AnUncleanDowngradeSupersedesTheServerIdRecord) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.50.85", kServerId));

  f.grouper.add(kLegacy, endpoint("192.168.50.85", ""));

  ASSERT_EQ(f.removed.size(), 1u);
  EXPECT_EQ(f.removed[0], kServerId);
  ASSERT_EQ(f.added.size(), 2u);
  EXPECT_EQ(f.added[1].key, kLegacy);
}

TEST(CoreGrouper, AnotherPortOnTheSameHostIsAnotherCore) {
  Forwarded f;
  f.grouper.add(kLegacy, endpoint("192.168.50.85", ""));

  auto second = endpoint("192.168.50.85", kServerId);
  second.port = 9000;
  f.grouper.add(kEth, std::move(second));

  EXPECT_TRUE(f.removed.empty());
  EXPECT_EQ(f.added.size(), 2u);
}

// After a DHCP reassignment the address may belong to a different Core whose
// other addresses are still live: only the matching record goes, and its
// group falls back to what it still has.
TEST(CoreGrouper, OnlyTheMatchingMemberOfAnotherGroupIsSuperseded) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", "other-core"));
  f.grouper.add(kWlan, endpoint("10.20.0.15", "other-core"));

  f.grouper.add(kLegacy, endpoint("192.168.1.20", ""));

  EXPECT_TRUE(f.removed.empty());
  ASSERT_EQ(f.replaced.size(), 1u) << "other-core fell back to its alternate";
  EXPECT_EQ(f.replaced[0].key, "other-core");
  EXPECT_EQ(f.replaced[0].host, "10.20.0.15");
  ASSERT_EQ(f.added.size(), 2u);
  EXPECT_EQ(f.added[1].key, kLegacy);
}

TEST(CoreGrouper, RemovingAnUnknownInstanceIsIgnored) {
  Forwarded f;
  f.grouper.remove(kEth);

  EXPECT_TRUE(f.added.empty());
  EXPECT_TRUE(f.replaced.empty());
  EXPECT_TRUE(f.removed.empty());
}

}  // namespace
