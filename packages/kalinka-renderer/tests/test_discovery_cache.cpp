#include <gtest/gtest.h>

#include "../src/discovery/DiscoveryCache.h"

namespace {

using Clock = DiscoveryCache::Clock;
using namespace std::chrono_literals;

// What a Core's own records promise: python-zeroconf answers with an hours-long
// PTR and a two-minute SRV.
constexpr auto kAnnouncedTtl = 120s;

const std::string kCore = "A Core._kalinkaplayer._tcp.local.";

Clock::time_point start() { return Clock::time_point{} + 1h; }

TEST(DiscoveryCache, AnInstanceIsHeldForAsLongAsItKeepsAnswering) {
  DiscoveryCache cache;
  auto now = start();
  cache.keep(kCore, true, kAnnouncedTtl, now);

  // Answering every minute, as the browse loop asks.
  for (int minute = 0; minute < 30; ++minute) {
    now += 60s;
    cache.touch(kCore, kAnnouncedTtl, now);
    ASSERT_TRUE(cache.lapsed(now).empty()) << "dropped a Core that answered";
  }
  EXPECT_EQ(cache.announced(kCore), std::optional<bool>(true));
}

TEST(DiscoveryCache, AnInstanceThatStopsAnsweringLapses) {
  DiscoveryCache cache;
  const auto now = start();
  cache.keep(kCore, true, kAnnouncedTtl, now);

  EXPECT_TRUE(cache.lapsed(now + DiscoveryCache::kMinLifetime - 1s).empty());
  const auto gone = cache.lapsed(now + DiscoveryCache::kMinLifetime);
  ASSERT_EQ(gone.size(), 1u);
  EXPECT_EQ(gone[0].instance, kCore);
  EXPECT_TRUE(gone[0].announced) << "the endpoint was reported; withdraw it";
  EXPECT_TRUE(cache.empty()) << "lapsing must not report the same one twice";
}

// A lost multicast packet must not cost a live Core its connection.
TEST(DiscoveryCache, SeveralQueriesMustGoUnansweredBeforeAnythingLapses) {
  EXPECT_GE(DiscoveryCache::kMinLifetime, 3 * 60s);
}

TEST(DiscoveryCache, ARefreshIsWantedWellBeforeTheLifetimeRunsOut) {
  DiscoveryCache cache;
  const auto now = start();
  cache.keep(kCore, true, kAnnouncedTtl, now);

  const auto refresh = cache.nextRefresh();
  ASSERT_TRUE(refresh.has_value());
  EXPECT_GT(*refresh, now);
  EXPECT_LE(*refresh, now + DiscoveryCache::kMinLifetime - 30s)
      << "too late to fit another query in";
}

TEST(DiscoveryCache, AGoodbyeIsTheFastPathAndLeavesNothingBehind) {
  DiscoveryCache cache;
  const auto now = start();
  cache.keep(kCore, true, kAnnouncedTtl, now);

  cache.drop(kCore);

  EXPECT_FALSE(cache.announced(kCore).has_value());
  EXPECT_TRUE(cache.lapsed(now + 1h).empty())
      << "already reported gone; it must not be reported again";
}

TEST(DiscoveryCache, AnInstanceNeverAnnouncedLapsesQuietly) {
  DiscoveryCache cache;
  const auto now = start();
  cache.keep(kCore, false, kAnnouncedTtl, now);  // no renderer support

  const auto gone = cache.lapsed(now + DiscoveryCache::kMaxLifetime);
  ASSERT_EQ(gone.size(), 1u);
  EXPECT_FALSE(gone[0].announced) << "nothing reported; nothing to withdraw";
}

TEST(DiscoveryCache, AnHoursLongTtlStillGivesUpWithinTheCeiling) {
  DiscoveryCache cache;
  const auto now = start();
  cache.keep(kCore, true, 4500s, now);  // the PTR ttl python-zeroconf sends

  EXPECT_EQ(cache.lapsed(now + DiscoveryCache::kMaxLifetime).size(), 1u);
}

TEST(DiscoveryCache, TouchingSomethingUnknownRemembersNothing) {
  DiscoveryCache cache;
  cache.touch(kCore, kAnnouncedTtl, start());
  EXPECT_TRUE(cache.empty());
}

TEST(DiscoveryCache, TouchingKeepsWhatWasBelieved) {
  DiscoveryCache cache;
  auto now = start();
  cache.keep(kCore, false, kAnnouncedTtl, now);

  now += 60s;
  cache.touch(kCore, kAnnouncedTtl, now);

  EXPECT_EQ(cache.announced(kCore), std::optional<bool>(false));
  EXPECT_TRUE(cache.lapsed(now + DiscoveryCache::kMinLifetime - 1s).empty())
      << "the lifetime should have restarted";
}

TEST(DiscoveryCache, TheEarliestRefreshWins) {
  DiscoveryCache cache;
  const auto now = start();
  cache.keep("Old._kalinkaplayer._tcp.local.", true, kAnnouncedTtl, now);
  cache.keep("New._kalinkaplayer._tcp.local.", true, kAnnouncedTtl, now + 30s);

  const auto refresh = cache.nextRefresh();
  ASSERT_TRUE(refresh.has_value());
  EXPECT_LT(*refresh, now + 30s + DiscoveryCache::kMinLifetime);
}

}  // namespace
