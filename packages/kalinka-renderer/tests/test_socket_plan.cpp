#include <gtest/gtest.h>

#include "../src/discovery/SocketPlan.h"

namespace {

using socket_plan::Candidate;
using socket_plan::Held;
using socket_plan::plan;

TEST(SocketPlan, SteadyStateTouchesNothing) {
  const auto p = plan({{"eth0", "10.10.10.1"}, {"wlan0", "192.168.50.248"}},
                      {{"eth0", {"10.10.10.1"}}, {"wlan0", {"192.168.50.248"}}});
  EXPECT_TRUE(p.close.empty());
  EXPECT_TRUE(p.open.empty());
}

TEST(SocketPlan, LateInterfaceOpensWithoutChurningTheLiveOne) {
  const auto p = plan({{"eth0", "10.10.10.1"}},
                      {{"eth0", {"10.10.10.1"}}, {"wlan0", {"192.168.50.248"}}});
  EXPECT_TRUE(p.close.empty());
  ASSERT_EQ(p.open.size(), 1u);
  EXPECT_EQ(p.open[0].interface, "wlan0");
}

TEST(SocketPlan, GoneInterfaceCloses) {
  const auto p = plan({{"eth0", "10.10.10.1"}, {"wlan0", "192.168.50.248"}},
                      {{"eth0", {"10.10.10.1"}}});
  EXPECT_EQ(p.close, std::vector<size_t>{1});
  EXPECT_TRUE(p.open.empty());
}

TEST(SocketPlan, ChangedAddressReplacesTheSocket) {
  const auto p =
      plan({{"wlan0", "192.168.50.248"}}, {{"wlan0", {"10.0.0.7"}}});
  EXPECT_EQ(p.close, std::vector<size_t>{0});
  ASSERT_EQ(p.open.size(), 1u);
  EXPECT_EQ(p.open[0].interface, "wlan0");
}

TEST(SocketPlan, AnyStillPresentAddressKeepsTheSocket) {
  // Enumeration order is not identity: the held address being second in the
  // candidate list is no reason to churn group membership.
  const auto p = plan({{"eth0", "10.10.10.2"}},
                      {{"eth0", {"10.10.10.1", "10.10.10.2"}}});
  EXPECT_TRUE(p.close.empty());
  EXPECT_TRUE(p.open.empty());
}

TEST(SocketPlan, WildcardYieldsToRealInterfaces) {
  const auto p = plan({{"", ""}}, {{"wlan0", {"192.168.50.248"}}});
  EXPECT_EQ(p.close, std::vector<size_t>{0});
  ASSERT_EQ(p.open.size(), 1u);
  EXPECT_EQ(p.open[0].interface, "wlan0");
}

TEST(SocketPlan, WildcardSurvivesWhileNothingIsEligible) {
  const auto p = plan({{"", ""}}, {});
  EXPECT_TRUE(p.close.empty());
  EXPECT_TRUE(p.open.empty());
}

TEST(SocketPlan, EverythingGoneClosesAll) {
  const auto p = plan({{"eth0", "10.10.10.1"}, {"wlan0", "192.168.50.248"}}, {});
  EXPECT_EQ(p.close, (std::vector<size_t>{0, 1}));
  EXPECT_TRUE(p.open.empty());
}

}  // namespace
