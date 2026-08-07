#pragma once

#include <boost/asio.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>

#include <chrono>
#include <deque>
#include <functional>
#include <memory>
#include <optional>
#include <string>

#include "../discovery/Discovery.h"

/**
 * @brief The link to one Core: sockets, framing and backoff — nothing else.
 *
 * Connects, completes the WebSocket handshake, then read-loops, reconnecting
 * with doubling backoff (initialRetryDelay..30s) until stopped or told to give
 * up. Inbound messages go through a bounded inbox drained off the read path,
 * so processing never stalls reads, and outbound ones through a bounded write
 * queue, because Beast allows one write at a time. What the bytes mean is not
 * this class's business — that is the ProtocolSession bound to it.
 *
 * @note Single-threaded io_context. Every async handler holds
 *       shared_from_this(), so a completion cannot outlive its transport, and
 *       carries the generation of the stream that queued it, so a superseded
 *       stream's completions do nothing.
 */
class WsTransport : public std::enable_shared_from_this<WsTransport> {
public:
  /// What the protocol above wants to hear. All calls on the io_context
  /// thread.
  struct Events {
    /// Connected and handshaken; the link carries messages from here on.
    std::function<void()> onUp;
    /// One complete binary message.
    std::function<void(const std::string &)> onMessage;
    /// The link is lost. A reconnect is already scheduled unless the loss was
    /// deliberate (stop or giveUp).
    std::function<void()> onDown;
  };

  WsTransport(boost::asio::io_context &ioc, CoreEndpoint endpoint,
              std::chrono::milliseconds initialRetryDelay =
                  std::chrono::seconds(1));

  /// Must be called once, before start().
  void bind(Events events);

  /// Begin connecting. Nothing happens until this is called. Calling it again
  /// supersedes the current connection with a fresh one.
  void start();

  /**
   * @brief Graceful shutdown: best-effort final frame, then a WebSocket close.
   *
   * Idempotent, and bounded by a 2s hard deadline so it cannot hang on a peer
   * that has stopped reading.
   *
   * @param finalFrame Sent before closing when the link is up; empty means
   *                   just close.
   * @note Must be called on the io_context thread.
   */
  void stop(std::string finalFrame);

  /**
   * @brief Queue one message behind whatever is in flight.
   *
   * @return true when accepted for delivery — queued, not confirmed received.
   *         false when the link is down, or the write queue overflowed (the
   *         connection is then dropped: a peer that stops reading gets a
   *         reconnect, not unbounded memory).
   */
  bool send(std::string data);

  /// Drop the link and never retry — the protocol decided retrying would only
  /// repeat itself. Reports onDown like any other loss.
  void giveUp();

private:
  using WsStream =
      boost::beast::websocket::stream<boost::asio::ip::tcp::socket>;

  void connect();
  void readLoop();
  void enqueueMessage(std::string data);
  void drainInbox();
  void writeNext();
  void markDown();
  void fail(const char *stage, const boost::beast::error_code &ec);
  void scheduleRetry();

  boost::asio::io_context &ioc_;
  CoreEndpoint endpoint_;
  Events events_;

  boost::asio::ip::tcp::resolver resolver_;
  std::optional<WsStream> ws_;  // recreated per connection attempt
  // Bumped per connect(). The replaced stream's handlers still complete
  // (cancelled); treating one as a failure of the new stream starts a
  // reconnect loop that kills every connection it makes.
  uint64_t generation_ = 0;
  boost::beast::flat_buffer readBuffer_;

  // Bounded inbox between the read loop and message processing.
  std::deque<std::string> inbox_;
  bool drainScheduled_ = false;

  // Beast allows one write at a time; messages queue behind the one in
  // flight. Bounded like the inbox.
  std::deque<std::string> writeQueue_;
  bool writing_ = false;
  bool closeAfterWrite_ = false;

  boost::asio::steady_timer retryTimer_;
  boost::asio::steady_timer closeTimer_;
  const std::chrono::milliseconds initialRetryDelay_;
  std::chrono::milliseconds retryDelay_;

  bool up_ = false;       // handshaken and not yet lost
  bool failing_ = false;  // read and write can both fail; retry once, not twice
  bool stopping_ = false;
  bool gaveUp_ = false;
};
