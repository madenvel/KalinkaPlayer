#include <gtest/gtest.h>

#include <boost/asio.hpp>

#include <chrono>
#include <memory>

#include "fakes.h"
#include "session/SessionManager.h"

using namespace std::chrono_literals;

class SessionManagerTest : public ::testing::Test {
protected:
  // shared_ptr because sessions report their end through weak_from_this().
  std::shared_ptr<SessionManager>
  makeManager(std::chrono::seconds grace = 60s) {
    return std::make_shared<SessionManager>(ioc, grace, player);
  }

  boost::asio::io_context ioc;
  std::shared_ptr<FakePlayer> player = std::make_shared<FakePlayer>();
};

TEST_F(SessionManagerTest, OpenFillsTheSlot) {
  auto manager = makeManager();
  std::string busyOwner;

  auto session = manager->open("sid-1", "owner-1", busyOwner);

  ASSERT_NE(session, nullptr);
  EXPECT_EQ(session->sessionId(), "sid-1");
  EXPECT_EQ(session->ownerServerId(), "owner-1");
  EXPECT_EQ(manager->current(), session);
}

TEST_F(SessionManagerTest, ReopenByTheOwnerReturnsTheSameSession) {
  auto manager = makeManager();
  std::string busyOwner;
  auto first = manager->open("sid-1", "owner-1", busyOwner);

  auto retried = manager->open("sid-1", "owner-1", busyOwner);

  EXPECT_EQ(retried, first);
}

TEST_F(SessionManagerTest, SecondSessionIsRefusedAndNamesTheOwner) {
  auto manager = makeManager();
  std::string busyOwner;
  manager->open("sid-1", "owner-1", busyOwner);

  EXPECT_EQ(manager->open("sid-2", "owner-2", busyOwner), nullptr);
  EXPECT_EQ(busyOwner, "owner-1");

  // The running id from anyone else is a replay of Hello, not a claim.
  EXPECT_EQ(manager->open("sid-1", "owner-2", busyOwner), nullptr);
  EXPECT_EQ(busyOwner, "owner-1");
}

TEST_F(SessionManagerTest, OnlyTheOwnerMayClose) {
  auto manager = makeManager();
  std::string busyOwner;
  manager->open("sid-1", "owner-1", busyOwner);

  EXPECT_FALSE(manager->close("sid-1", "owner-2"));
  EXPECT_FALSE(manager->close("sid-other", "owner-1"));
  EXPECT_NE(manager->current(), nullptr);

  EXPECT_TRUE(manager->close("sid-1", "owner-1"));
  EXPECT_EQ(manager->current(), nullptr);
  EXPECT_FALSE(manager->close("sid-1", "owner-1"));  // already ended
}

TEST_F(SessionManagerTest, TheSlotIsFreeAgainAfterClose) {
  auto manager = makeManager();
  std::string busyOwner;
  manager->open("sid-1", "owner-1", busyOwner);
  manager->close("sid-1", "owner-1");

  auto next = manager->open("sid-2", "owner-2", busyOwner);

  ASSERT_NE(next, nullptr);
  EXPECT_EQ(manager->current(), next);
}

TEST_F(SessionManagerTest, OwnedByFindsOnlyTheOwner) {
  auto manager = makeManager();
  std::string busyOwner;
  auto session = manager->open("sid-1", "owner-1", busyOwner);

  EXPECT_EQ(manager->ownedBy("owner-1"), session);
  EXPECT_EQ(manager->ownedBy("owner-2"), nullptr);
  EXPECT_EQ(manager->ownedBy(""), nullptr);
}

TEST_F(SessionManagerTest, ShutdownClosesTheRunningSession) {
  auto manager = makeManager();
  std::string busyOwner;
  auto session = manager->open("sid-1", "owner-1", busyOwner);
  auto transport = std::make_shared<FakeTransport>();
  session->attach(transport);

  manager->shutdown();

  EXPECT_EQ(manager->current(), nullptr);
  EXPECT_EQ(transport->sessionClosed, 1);
  EXPECT_EQ(player->calls.back(), "stop");
}

TEST_F(SessionManagerTest, ShutdownDoesNotWaitForTheOwnerGrace) {
  auto manager = makeManager(60s);
  std::string busyOwner;
  auto session = manager->open("sid-1", "owner-1", busyOwner);
  auto transport = std::make_shared<FakeTransport>();
  session->attach(transport);
  session->onConnectionClosed(transport.get());  // grace timer armed

  manager->shutdown();

  EXPECT_EQ(manager->current(), nullptr);
  EXPECT_EQ(player->calls.back(), "stop");
  ioc.run();  // returns at once only if shutdown cancelled the 60s grace
}

TEST_F(SessionManagerTest, ShutdownWithoutASessionIsANoOp) {
  auto manager = makeManager();

  manager->shutdown();

  EXPECT_EQ(manager->current(), nullptr);
  EXPECT_TRUE(player->calls.empty());
}

TEST_F(SessionManagerTest, ASessionEndingOnItsOwnClearsTheSlot) {
  auto manager = makeManager(0s);
  std::string busyOwner;
  auto session = manager->open("sid-1", "owner-1", busyOwner);
  auto transport = std::make_shared<FakeTransport>();
  session->attach(transport);

  // Owner gone and not back within the grace period: the session closes itself
  // and the renderer is free for whoever asks next.
  session->onConnectionClosed(transport.get());
  ioc.run();

  EXPECT_EQ(manager->current(), nullptr);
  EXPECT_EQ(transport->sessionClosed, 0);  // its route was already gone
}
