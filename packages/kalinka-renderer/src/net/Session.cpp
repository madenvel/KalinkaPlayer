#include "Session.h"

#include <spdlog/spdlog.h>
#include <sys/utsname.h>

#include "kalinka/renderer/v1/renderer.pb.h"

namespace asio = boost::asio;
namespace beast = boost::beast;
namespace websocket = beast::websocket;
using tcp = asio::ip::tcp;
namespace pb = kalinka::renderer::v1;

namespace {
constexpr uint32_t kProtocolVersion = 1;
constexpr auto kMaxRetryDelay = std::chrono::seconds(30);
constexpr auto kCloseDeadline = std::chrono::seconds(2);
}  // namespace

Session::Session(asio::io_context &ioc, CoreEndpoint endpoint,
                 const Identity &identity, std::string friendlyName)
    : ioc_(ioc), endpoint_(std::move(endpoint)), identity_(identity),
      friendlyName_(std::move(friendlyName)), resolver_(ioc),
      retryTimer_(ioc), closeTimer_(ioc) {}

void Session::start() { connect(); }

void Session::connect() {
  welcomed_ = false;
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

  utsname u{};
  if (uname(&u) == 0) {
    pb::Platform *platform = hello->mutable_platform();
    platform->set_os("linux");
    platform->set_os_version(u.release);
    platform->set_arch(u.machine);
    platform->set_hostname(u.nodename);
    platform->set_audio_backend("alsa");
  }

  writeBuffer_ = env.SerializeAsString();
  auto self = shared_from_this();
  ws_->async_write(asio::buffer(writeBuffer_),
                   [self](beast::error_code ec, std::size_t) {
                     if (self->stopping_) return;
                     if (ec) return self->fail("send hello", ec);
                     self->readLoop();
                   });
}

void Session::readLoop() {
  auto self = shared_from_this();
  ws_->async_read(readBuffer_, [self](beast::error_code ec, std::size_t) {
    if (self->stopping_) return;
    if (ec) return self->fail("read", ec);
    self->handleMessage(beast::buffers_to_string(self->readBuffer_.data()));
    self->readBuffer_.consume(self->readBuffer_.size());
    if (!self->gaveUp_) {
      self->readLoop();
    }
  });
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
    retryDelay_ = std::chrono::seconds(1);
    spdlog::info(
        "[{}] Registered: server '{}' version {} (api {}, protocol v{})",
        endpoint_.name, w.server_name(), w.server_version(), w.api_version(),
        w.protocol_version());
    break;
  }
  case pb::Envelope::kGoodbye: {
    const pb::Goodbye &g = env.goodbye();
    if (g.reason() == pb::Goodbye::REASON_VERSION_UNSUPPORTED) {
      spdlog::error("[{}] Server rejected protocol version; giving up: {}",
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
    writeBuffer_ = env.SerializeAsString();
    ws_->async_write(
        asio::buffer(writeBuffer_), [self](beast::error_code, std::size_t) {
          self->ws_->async_close(websocket::close_code::going_away,
                                 [self](beast::error_code) {
                                   beast::error_code ec;
                                   self->ws_->next_layer().close(ec);
                                   self->closeTimer_.cancel();
                                 });
        });
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
