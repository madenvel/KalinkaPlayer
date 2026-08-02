#include "Session.h"

#include <spdlog/spdlog.h>
#include <sys/utsname.h>

#include "../Protocol.h"
#include "kalinka/renderer/v1/renderer.pb.h"

namespace asio = boost::asio;
namespace beast = boost::beast;
namespace websocket = beast::websocket;
using tcp = asio::ip::tcp;
namespace pb = kalinka::renderer::v1;

namespace {
constexpr uint32_t kProtocolVersion = kRendererProtocolVersion;
constexpr auto kMaxRetryDelay = std::chrono::seconds(30);
constexpr auto kCloseDeadline = std::chrono::seconds(2);
constexpr size_t kInboxLimit = 256;

// Liveness: Beast answers server pings, pings when idle, drops a silent peer.
boost::beast::websocket::stream_base::timeout wsTimeouts() {
  boost::beast::websocket::stream_base::timeout t{};
  t.handshake_timeout = std::chrono::seconds(10);
  t.idle_timeout = std::chrono::seconds(60);
  t.keep_alive_pings = true;
  return t;
}
}  // namespace

Session::Session(asio::io_context &ioc, CoreEndpoint endpoint,
                 const Identity &identity, std::string friendlyName,
                 SessionManager &sessions)
    : ioc_(ioc), endpoint_(std::move(endpoint)), identity_(identity),
      friendlyName_(std::move(friendlyName)), sessions_(sessions),
      resolver_(ioc), retryTimer_(ioc), closeTimer_(ioc) {}

void Session::start() { connect(); }

void Session::connect() {
  welcomed_ = false;
  serverId_.clear();
  inbox_.clear();  // discard messages from a previous connection
  writeQueue_.clear();
  writing_ = false;
  ws_.emplace(ioc_);
  auto self = shared_from_this();
  resolver_.async_resolve(
      endpoint_.host, std::to_string(endpoint_.port),
      [self](beast::error_code ec, tcp::resolver::results_type results) {
        if (self->stopping_) return;
        if (ec) return self->fail("resolve", ec);
        asio::async_connect(
            self->ws_->next_layer(), results,
            [self](beast::error_code ec, const tcp::endpoint &) {
              if (self->stopping_) return;
              if (ec) return self->fail("connect", ec);
              self->ws_->binary(true);
              self->ws_->set_option(wsTimeouts());
              const std::string host =
                  self->endpoint_.host + ":" +
                  std::to_string(self->endpoint_.port);
              self->ws_->async_handshake(
                  host, "/renderer/ws", [self](beast::error_code ec) {
                    if (self->stopping_) return;
                    if (ec) return self->fail("ws handshake", ec);
                    self->sendHello();
                  });
            });
      });
}

void Session::sendHello() {
  pb::Envelope env;
  env.set_message_id(nextMessageId_++);
  pb::Hello *hello = env.mutable_hello();
  hello->mutable_protocol_versions()->set_min(kProtocolVersion);
  hello->mutable_protocol_versions()->set_max(kProtocolVersion);
  hello->set_renderer_id(identity_.rendererId);
  hello->set_instance_id(identity_.instanceId);
  hello->set_friendly_name(friendlyName_);
  hello->set_software_version(KALINKA_RENDERER_VERSION);
  hello->set_kind(pb::RENDERER_KIND_NATIVE);
  if (const auto &active = sessions_.active(); active) {
    // Reported to every Core; only its owner acts on it.
    hello->set_active_session_id(active->sessionId);
    hello->set_session_owner_server_id(active->ownerServerId);
  }

  utsname u{};
  if (uname(&u) == 0) {
    pb::Platform *platform = hello->mutable_platform();
    platform->set_os("linux");
    platform->set_os_version(u.release);
    platform->set_arch(u.machine);
    platform->set_hostname(u.nodename);
    platform->set_audio_backend("alsa");
  }

  sendSerialized(env.SerializeAsString());
  readLoop();
}

void Session::sendSerialized(std::string data) {
  writeQueue_.push_back(std::move(data));
  if (!writing_) {
    writeNext();
  }
}

void Session::writeNext() {
  if (writeQueue_.empty()) {
    writing_ = false;
    if (closeAfterWrite_) {
      auto self = shared_from_this();
      ws_->async_close(websocket::close_code::going_away,
                       [self](beast::error_code) {
                         beast::error_code ec;
                         self->ws_->next_layer().close(ec);
                         self->closeTimer_.cancel();
                       });
    }
    return;
  }
  writing_ = true;
  auto self = shared_from_this();
  ws_->async_write(asio::buffer(writeQueue_.front()),
                   [self](beast::error_code ec, std::size_t) {
                     self->writeQueue_.pop_front();
                     if (ec) {
                       self->writing_ = false;
                       if (!self->stopping_) self->fail("write", ec);
                       return;
                     }
                     self->writeNext();
                   });
}

void Session::readLoop() {
  auto self = shared_from_this();
  ws_->async_read(readBuffer_, [self](beast::error_code ec, std::size_t) {
    if (self->stopping_) return;
    if (ec) return self->fail("read", ec);
    self->enqueueMessage(
        beast::buffers_to_string(self->readBuffer_.data()));
    self->readBuffer_.consume(self->readBuffer_.size());
    if (!self->gaveUp_) {
      self->readLoop();
    }
  });
}

void Session::enqueueMessage(std::string data) {
  if (inbox_.size() >= kInboxLimit) {
    spdlog::error("[{}] Inbox full ({} messages); dropping incoming message",
                  endpoint_.name, inbox_.size());
    return;
  }
  inbox_.push_back(std::move(data));
  if (!drainScheduled_) {
    drainScheduled_ = true;
    asio::post(ioc_, [self = shared_from_this()] { self->drainInbox(); });
  }
}

void Session::drainInbox() {
  drainScheduled_ = false;
  while (!inbox_.empty() && !stopping_ && !gaveUp_) {
    const std::string data = std::move(inbox_.front());
    inbox_.pop_front();
    handleMessage(data);
  }
  inbox_.clear();
}

void Session::handleMessage(const std::string &data) {
  pb::Envelope env;
  if (!env.ParseFromString(data)) {
    spdlog::warn("[{}] Dropping unparseable {}-byte message", endpoint_.name,
                 data.size());
    return;
  }
  switch (env.payload_case()) {
  case pb::Envelope::kWelcome: {
    const pb::Welcome &w = env.welcome();
    welcomed_ = true;
    serverId_ = w.server_id();
    retryDelay_ = std::chrono::seconds(1);
    spdlog::info(
        "[{}] Registered: server '{}' version {} (api {}, protocol v{})",
        endpoint_.name, w.server_name(), w.server_version(), w.api_version(),
        w.protocol_version());
    break;
  }
  case pb::Envelope::kSessionOpen:
    handleSessionOpen(env.session_open().session_id());
    break;
  case pb::Envelope::kSessionClose:
    handleSessionClose(env.session_close().session_id());
    break;
  case pb::Envelope::kGoodbye: {
    const pb::Goodbye &g = env.goodbye();
    if (g.reason() == pb::Goodbye::REASON_VERSION_UNSUPPORTED) {
      spdlog::error("[{}] Server rejected protocol version; giving up: {}",
                    endpoint_.name, g.detail());
      gaveUp_ = true;
      beast::error_code ec;
      ws_->next_layer().close(ec);
    } else if (g.reason() == pb::Goodbye::REASON_REPLACED) {
      // Duplicated renderer_id; reconnecting would displace it back and forth.
      spdlog::error(
          "[{}] Replaced by another connection with the same renderer_id; "
          "giving up (duplicated renderer identity?): {}",
          endpoint_.name, g.detail());
      gaveUp_ = true;
      beast::error_code ec;
      ws_->next_layer().close(ec);
    } else {
      spdlog::info("[{}] Server said goodbye (reason {})", endpoint_.name,
                   static_cast<int>(g.reason()));
    }
    break;
  }
  default:
    spdlog::debug("[{}] Ignoring message with payload case {}", endpoint_.name,
                  static_cast<int>(env.payload_case()));
    break;
  }
}

void Session::handleSessionOpen(const std::string &sessionId) {
  pb::Envelope env;
  env.set_message_id(nextMessageId_++);
  pb::SessionOpenResult *result = env.mutable_session_open_result();
  result->set_session_id(sessionId);

  if (!welcomed_) {
    result->set_accepted(false);
    result->set_error(pb::SessionOpenResult::ERROR_INTERNAL);
    result->set_detail("session opened before the handshake completed");
  } else {
    std::string busyOwner;
    if (sessions_.open(sessionId, serverId_, busyOwner)) {
      result->set_accepted(true);
    } else {
      result->set_accepted(false);
      result->set_error(pb::SessionOpenResult::ERROR_BUSY);
      result->set_detail("another playback session is running");
      result->set_owner_server_id(busyOwner);
    }
  }
  sendSerialized(env.SerializeAsString());
}

void Session::handleSessionClose(const std::string &sessionId) {
  sessions_.close(sessionId);
  pb::Envelope env;
  env.set_message_id(nextMessageId_++);
  pb::SessionClosed *closed = env.mutable_session_closed();
  closed->set_session_id(sessionId);
  closed->set_reason(pb::SessionClosed::REASON_ACK);
  sendSerialized(env.SerializeAsString());
}

void Session::fail(const char *stage, const beast::error_code &ec) {
  spdlog::warn("[{}] {} failed: {}", endpoint_.name, stage, ec.message());
  beast::error_code ignored;
  ws_->next_layer().close(ignored);
  scheduleRetry();
}

void Session::scheduleRetry() {
  if (stopping_ || gaveUp_) {
    return;
  }
  spdlog::info("[{}] Reconnecting in {}s", endpoint_.name,
               retryDelay_.count());
  retryTimer_.expires_after(retryDelay_);
  retryDelay_ = std::min(retryDelay_ * 2, kMaxRetryDelay);
  auto self = shared_from_this();
  retryTimer_.async_wait([self](const boost::system::error_code &ec) {
    if (ec || self->stopping_) return;
    self->connect();
  });
}

void Session::stop() {
  if (stopping_) {
    return;
  }
  stopping_ = true;
  retryTimer_.cancel();
  resolver_.cancel();

  if (!ws_ || !ws_->next_layer().is_open()) {
    return;
  }
  auto self = shared_from_this();
  if (welcomed_ && ws_->is_open()) {
    // Best-effort Goodbye + close; the deadline timer guarantees exit.
    pb::Envelope env;
    env.set_message_id(nextMessageId_++);
    env.mutable_goodbye()->set_reason(pb::Goodbye::REASON_SHUTDOWN);
    closeAfterWrite_ = true;
    sendSerialized(env.SerializeAsString());
    closeTimer_.expires_after(kCloseDeadline);
    closeTimer_.async_wait([self](const boost::system::error_code &ec) {
      if (!ec) {
        beast::error_code ignored;
        self->ws_->next_layer().close(ignored);
      }
    });
  } else {
    // Mid-connect/handshake: just tear the socket down.
    beast::error_code ec;
    ws_->next_layer().close(ec);
  }
}
