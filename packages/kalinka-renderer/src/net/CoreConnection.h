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
#include "../RendererServices.h"
#include "../discovery/Discovery.h"
#include "../session/SessionEventSink.h"
#include "../session/SessionTransport.h"

/**
 * @brief One WebSocket connection to one Core.
 *
 * Connect, Hello, expect Welcome, then read-loop, reconnecting with doubling
 * backoff (1s..30s) until told to stop — or until the Core rejects the protocol
 * version or replaces this renderer_id, where retrying would only repeat
 * itself. Inbound messages go through a bounded inbox drained off the read
 * path, so processing never stalls reads, and outbound ones through a bounded
 * write queue, because Beast allows one write at a time.
 *
 * While this connection carries the Core that owns the session, it holds that
 * session and is attached to it: session commands are gated here and routed in
 * through SessionEventSink, state comes back out through SessionTransport.
 * This class parses and frames; the session decides.
 *
 * @note Single-threaded io_context. Every async handler holds
 *       shared_from_this(), so a completion cannot outlive its connection.
 */
class CoreConnection : public std::enable_shared_from_this<CoreConnection>,
                       public SessionTransport {
public:
  /**
   * @param identity Outlives this connection; every reconnect re-announces it.
   * @param services The session, command and config planes, shared with every
   *                 other connection.
   */
  CoreConnection(boost::asio::io_context &ioc, CoreEndpoint endpoint,
                 const Identity &identity, std::string friendlyName,
                 RendererServices services);

  /// Begin connecting. Nothing happens until this is called.
  void start();

  /**
   * @brief Graceful shutdown: best-effort Goodbye, then a WebSocket close.
   *
   * Idempotent, and bounded by a 2s hard deadline so it cannot hang on a peer
   * that has stopped reading.
   *
   * @note Must be called on the io_context thread.
   */
  void stop();

  /// SessionTransport: false unless this connection is up and past Welcome.
  bool sendSessionMessage(kalinka::renderer::v1::Envelope &env) override;

  /// SessionTransport: the session ended; forget it.
  void onSessionClosed() override;

private:
  using WsStream =
      boost::beast::websocket::stream<boost::asio::ip::tcp::socket>;

  void connect();
  void sendHello();
  void readLoop();
  void enqueueMessage(std::string data);
  void drainInbox();
  void handleMessage(const std::string &data);
  void handleSessionOpen(const std::string &sessionId);
  void handleSessionClose(const std::string &sessionId);
  void handleCommand(const kalinka::renderer::v1::Envelope &env);
  void handleConfigRequest(const kalinka::renderer::v1::Envelope &env);
  void handleConfigUpdate(const kalinka::renderer::v1::Envelope &env);
  void sendReply(kalinka::renderer::v1::Envelope &out, uint64_t inReplyTo);
  void sendSerialized(std::string data);
  void writeNext();
  void adoptSession();
  void detachSession();
  void fail(const char *stage, const boost::beast::error_code &ec);
  void scheduleRetry();

  boost::asio::io_context &ioc_;
  CoreEndpoint endpoint_;
  const Identity &identity_;
  std::string friendlyName_;
  RendererServices services_;

  boost::asio::ip::tcp::resolver resolver_;
  std::optional<WsStream> ws_;  // recreated per connection attempt
  boost::beast::flat_buffer readBuffer_;

  // Bounded inbox between the read loop and message processing.
  std::deque<std::string> inbox_;
  bool drainScheduled_ = false;

  // Beast allows one write at a time; messages queue behind the one in flight.
  // Bounded like the inbox: a peer that stops reading must not grow it without
  // limit.
  std::deque<std::string> writeQueue_;
  bool writing_ = false;
  bool closeAfterWrite_ = false;

  boost::asio::steady_timer retryTimer_;
  boost::asio::steady_timer closeTimer_;
  std::chrono::seconds retryDelay_{1};

  uint64_t nextMessageId_ = 1;
  std::string serverId_;  // from Welcome; owner id for sessions this Core opens
  // The session this connection carries, while its Core owns one. Held so
  // commands route without a lookup; the gate is sessionId().
  std::shared_ptr<SessionEventSink> session_;
  bool welcomed_ = false;
  bool failing_ = false;  // read and write can both fail; retry once, not twice
  bool stopping_ = false;
  bool gaveUp_ = false;  // e.g. protocol version rejected — no point retrying
};
