#include <gtest/gtest.h>

#include <boost/asio.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>

#include <chrono>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#include "net/WsTransport.h"

namespace asio = boost::asio;
namespace beast = boost::beast;
namespace websocket = beast::websocket;
using tcp = asio::ip::tcp;
using namespace std::chrono_literals;

namespace {

/// A WebSocket server on a loopback port: accepts, reads, can talk back and
/// hang up. Everything runs on the test's io_context.
class LoopbackServer {
public:
  explicit LoopbackServer(asio::io_context &ioc)
      : ioc_(ioc), acceptor_(ioc, {asio::ip::address_v4::loopback(), 0}) {
    accept();
  }

  uint16_t port() const { return acceptor_.local_endpoint().port(); }
  int accepted = 0;
  std::vector<std::string> received;

  void send(const std::string &data) {
    session_->ws.binary(true);
    session_->ws.async_write(asio::buffer(data),
                             [](beast::error_code, std::size_t) {});
  }

  void drop() {
    beast::error_code ec;
    session_->ws.next_layer().close(ec);
  }

  bool open() const { return session_ && session_->ws.is_open(); }

private:
  struct PeerSession {
    explicit PeerSession(asio::io_context &ioc) : ws(tcp::socket(ioc)) {}
    websocket::stream<tcp::socket> ws;
    beast::flat_buffer buffer;
  };

  void accept() {
    acceptor_.async_accept([this](beast::error_code ec, tcp::socket socket) {
      if (ec) {
        return;
      }
      session_ = std::make_shared<PeerSession>(ioc_);
      session_->ws.next_layer() = std::move(socket);
      auto held = session_;
      held->ws.async_accept([this, held](beast::error_code ec) {
        if (!ec) {
          ++accepted;
          read(held);
        }
      });
      accept();  // the transport reconnects; keep accepting
    });
  }

  void read(std::shared_ptr<PeerSession> session) {
    session->ws.async_read(
        session->buffer, [this, session](beast::error_code ec, std::size_t) {
          if (ec) {
            return;
          }
          received.push_back(beast::buffers_to_string(session->buffer.data()));
          session->buffer.consume(session->buffer.size());
          read(session);
        });
  }

  asio::io_context &ioc_;
  tcp::acceptor acceptor_;
  std::shared_ptr<PeerSession> session_;
};

}  // namespace

class WsTransportTest : public ::testing::Test {
protected:
  std::shared_ptr<WsTransport> makeTransport() {
    auto transport = std::make_shared<WsTransport>(
        ioc,
        CoreEndpoint{"loopback", "127.0.0.1", server.port(), "loopback"},
        10ms);
    transport->bind({
        [this] { ++ups; },
        [this](const std::string &data) { messages.push_back(data); },
        [this] { ++downs; },
    });
    return transport;
  }

  /// Drive the io_context until @p done or the deadline; false on timeout.
  bool runUntil(const std::function<bool()> &done,
                std::chrono::milliseconds budget = 2s) {
    const auto deadline = std::chrono::steady_clock::now() + budget;
    while (!done() && std::chrono::steady_clock::now() < deadline) {
      ioc.run_one_for(50ms);
    }
    return done();
  }

  asio::io_context ioc;
  LoopbackServer server{ioc};
  int ups = 0;
  int downs = 0;
  std::vector<std::string> messages;
};

TEST_F(WsTransportTest, MessagesFlowBothWaysOnceUp) {
  auto transport = makeTransport();
  transport->start();
  ASSERT_TRUE(runUntil([&] { return ups == 1; }));

  EXPECT_TRUE(transport->send("from-renderer"));
  ASSERT_TRUE(runUntil([&] { return !server.received.empty(); }));
  EXPECT_EQ(server.received[0], "from-renderer");

  server.send("from-core");
  ASSERT_TRUE(runUntil([&] { return !messages.empty(); }));
  EXPECT_EQ(messages[0], "from-core");

  transport->stop({});
}

TEST_F(WsTransportTest, SendIsRefusedWhileDown) {
  auto transport = makeTransport();
  EXPECT_FALSE(transport->send("too early"));
  transport->stop({});
}

TEST_F(WsTransportTest, ADropReportsDownAndReconnects) {
  auto transport = makeTransport();
  transport->start();
  ASSERT_TRUE(runUntil([&] { return ups == 1; }));

  server.drop();

  ASSERT_TRUE(runUntil([&] { return downs == 1; }));
  ASSERT_TRUE(runUntil([&] { return ups == 2; }));  // 10ms retry
  transport->stop({});
}

TEST_F(WsTransportTest, GiveUpReportsDownAndStaysDown) {
  auto transport = makeTransport();
  transport->start();
  ASSERT_TRUE(runUntil([&] { return ups == 1; }));

  transport->giveUp();

  EXPECT_EQ(downs, 1);
  // With a 10ms retry a reconnect would land well inside this window.
  ioc.run_for(200ms);
  EXPECT_EQ(ups, 1);
}

TEST_F(WsTransportTest, StopFlushesTheFinalFrame) {
  auto transport = makeTransport();
  transport->start();
  ASSERT_TRUE(runUntil([&] { return ups == 1; }));

  transport->stop("goodbye");

  ASSERT_TRUE(runUntil([&] { return !server.received.empty(); }));
  EXPECT_EQ(server.received[0], "goodbye");
  EXPECT_EQ(downs, 1);
  ioc.run_for(100ms);
  EXPECT_EQ(ups, 1);  // stopped means stopped
}
