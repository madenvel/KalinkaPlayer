#include <gtest/gtest.h>

#include <string>
#include <utility>
#include <vector>

#include "../src/discovery/CoreGrouper.h"

namespace {

const std::string kEth = "My Kalinka Service (eth0)._kalinkaplayer._tcp.local.";
const std::string kWlan =
    "My Kalinka Service (wlan0)._kalinkaplayer._tcp.local.";
const std::string kServerId = "9f1c9f2e-1111-2222-3333-444455556666";

CoreEndpoint endpoint(const std::string &host, const std::string &serverId) {
  return CoreEndpoint{"", host, 8000, "My Kalinka Service", serverId};
}

struct Forwarded {
  std::vector<CoreEndpoint> added;
  std::vector<std::string> removed;
  CoreGrouper grouper{
      [this](CoreEndpoint ep) { added.push_back(std::move(ep)); },
      [this](std::string key) { removed.push_back(std::move(key)); }};
};

TEST(CoreGrouper, InstancesSharingAServerIdAreOneCore) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));

  ASSERT_EQ(f.added.size(), 1u);
  EXPECT_EQ(f.added[0].key, kServerId);
  EXPECT_EQ(f.added[0].host, "192.168.1.20");
  EXPECT_TRUE(f.removed.empty());
}

TEST(CoreGrouper, LosingAnAlternateLeavesTheForwardedEndpointStanding) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));

  f.grouper.remove(kWlan);

  EXPECT_EQ(f.added.size(), 1u);
  EXPECT_TRUE(f.removed.empty());
}

TEST(CoreGrouper, LosingTheForwardedEndpointFailsOverToTheAlternate) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));

  f.grouper.remove(kEth);

  ASSERT_EQ(f.removed.size(), 1u);
  EXPECT_EQ(f.removed[0], kServerId);
  ASSERT_EQ(f.added.size(), 2u);
  EXPECT_EQ(f.added[1].key, kServerId);
  EXPECT_EQ(f.added[1].host, "10.20.0.15");
}

TEST(CoreGrouper, LosingTheLastInstanceWithdrawsTheCore) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));

  f.grouper.remove(kEth);
  f.grouper.remove(kWlan);

  ASSERT_EQ(f.removed.size(), 2u);
  EXPECT_EQ(f.removed[1], kServerId);
  EXPECT_EQ(f.added.size(), 2u) << "nothing left to fail over to";
}

TEST(CoreGrouper, ACoreWithoutAServerIdIsKeyedByItsInstance) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", ""));

  ASSERT_EQ(f.added.size(), 1u);
  EXPECT_EQ(f.added[0].key, kEth);

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
}

TEST(CoreGrouper, ReResolvingAnInstanceForwardsNothingNew) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  EXPECT_EQ(f.added.size(), 1u);
  EXPECT_TRUE(f.removed.empty());
}

TEST(CoreGrouper, TheActiveInstanceChangingAddressIsReForwarded) {
  // A Core restarted with a new DHCP lease, no goodbye sent.
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  f.grouper.add(kEth, endpoint("192.168.1.99", kServerId));

  ASSERT_EQ(f.removed.size(), 1u);
  EXPECT_EQ(f.removed[0], kServerId);
  ASSERT_EQ(f.added.size(), 2u);
  EXPECT_EQ(f.added[1].key, kServerId);
  EXPECT_EQ(f.added[1].host, "192.168.1.99");
}

TEST(CoreGrouper, AChangedPortOnTheActiveInstanceIsReForwarded) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  auto moved = endpoint("192.168.1.20", kServerId);
  moved.port = 9000;
  f.grouper.add(kEth, std::move(moved));

  ASSERT_EQ(f.added.size(), 2u);
  EXPECT_EQ(f.added[1].port, 9000);
}

TEST(CoreGrouper, ANameOnlyChangeIsRecordedSilently) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  auto renamed = endpoint("192.168.1.20", kServerId);
  renamed.name = "My Renamed Service";
  f.grouper.add(kEth, std::move(renamed));

  EXPECT_EQ(f.added.size(), 1u);
  EXPECT_TRUE(f.removed.empty());
}

TEST(CoreGrouper, AnAlternateChangingAddressIsHeldForTheNextFailover) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));

  f.grouper.add(kWlan, endpoint("10.20.0.99", kServerId));
  EXPECT_EQ(f.added.size(), 1u) << "the active connection stands";

  f.grouper.remove(kEth);
  ASSERT_EQ(f.added.size(), 2u);
  EXPECT_EQ(f.added[1].host, "10.20.0.99");
}

TEST(CoreGrouper, AnInstanceReturningAfterFailoverBecomesAnAlternate) {
  Forwarded f;
  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));
  f.grouper.add(kWlan, endpoint("10.20.0.15", kServerId));
  f.grouper.remove(kEth);  // failed over to wlan0

  f.grouper.add(kEth, endpoint("192.168.1.20", kServerId));

  EXPECT_EQ(f.added.size(), 2u) << "the wlan0 connection stands";

  f.grouper.remove(kWlan);
  ASSERT_EQ(f.added.size(), 3u) << "and eth0 is there to fail over to";
  EXPECT_EQ(f.added[2].host, "192.168.1.20");
}

TEST(CoreGrouper, RemovingAnUnknownInstanceIsIgnored) {
  Forwarded f;
  f.grouper.remove(kEth);

  EXPECT_TRUE(f.added.empty());
  EXPECT_TRUE(f.removed.empty());
}

}  // namespace
