#pragma once

#include <boost/asio.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>

#include <chrono>
#include <cstdint>
#include <deque>
#include <memory>
#include <optional>
#include <string>

#include "../Identity.h"
#include "../discovery/Discovery.h"

// One WebSocket connection to one Core: connect, Hello, expect Welcome, then
// read-loop; reconnects with doubling backoff (1s..30s). Single-threaded
// io_context; handlers hold shared_from_this. Inbound messages go through a
// bounded inbox drained off the read path, so processing never stalls reads.
class Session : public std::enable_shared_from_this<Session> {
public:
  Session(boost::asio::io_context &ioc, CoreEndpoint endpoint,
          const Identity &identity, std::string friendlyName);

  void start();

  // Graceful shutdown: best-effort Goodbye + WS close with a 2s hard
  // deadline. Must be called on the io_context thread.
  void stop();

private:
  using WsStream =
      boost::beast::websocket::stream<boost::asio::ip::tcp::socket>;

  void connect();
  void sendHello();
  void readLoop();
  void enqueueMessage(std::string data);
  void drainInbox();
  void handleMessage(const std::string &data);
  void fail(const char *stage, const boost::beast::error_code &ec);
  void scheduleRetry();

  boost::asio::io_context &ioc_;
  CoreEndpoint endpoint_;
  const Identity &identity_;
  std::string friendlyName_;

  boost::asio::ip::tcp::resolver resolver_;
  std::optional<WsStream> ws_;  // recreated per connection attempt
  boost::beast::flat_buffer readBuffer_;
  std::string writeBuffer_;

  // Bounded inbox between the read loop and message processing.
  std::deque<std::string> inbox_;
  bool drainScheduled_ = false;

  boost::asio::steady_timer retryTimer_;
  boost::asio::steady_timer closeTimer_;
  std::chrono::seconds retryDelay_{1};

  uint64_t nextMessageId_ = 1;
  bool welcomed_ = false;
  bool stopping_ = false;
  bool gaveUp_ = false;  // e.g. protocol version rejected — no point retrying
};
