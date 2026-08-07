#include "WsTransport.h"

#include <spdlog/spdlog.h>

namespace asio = boost::asio;
namespace beast = boost::beast;
namespace websocket = beast::websocket;
using tcp = asio::ip::tcp;

namespace {
constexpr auto kMaxRetryDelay = std::chrono::seconds(30);
constexpr auto kCloseDeadline = std::chrono::seconds(2);
constexpr size_t kInboxLimit = 256;
constexpr size_t kWriteQueueLimit = 256;

// Liveness: Beast answers server pings, pings when idle, drops a silent peer.
websocket::stream_base::timeout wsTimeouts() {
  websocket::stream_base::timeout t{};
  t.handshake_timeout = std::chrono::seconds(10);
  t.idle_timeout = std::chrono::seconds(60);
  t.keep_alive_pings = true;
  return t;
}
}  // namespace

WsTransport::WsTransport(asio::io_context &ioc, CoreEndpoint endpoint,
                         std::chrono::milliseconds initialRetryDelay)
    : ioc_(ioc), endpoint_(std::move(endpoint)), resolver_(ioc),
      retryTimer_(ioc), closeTimer_(ioc),
      initialRetryDelay_(initialRetryDelay), retryDelay_(initialRetryDelay) {}

void WsTransport::bind(Events events) { events_ = std::move(events); }

void WsTransport::start() { connect(); }

void WsTransport::connect() {
  markDown();
  failing_ = false;
  const uint64_t gen = ++generation_;
  inbox_.clear();  // discard messages from a previous connection
  // A read that failed mid-message leaves partial bytes behind; they must not
  // prefix the next connection's first message.
  readBuffer_.consume(readBuffer_.size());
  writeQueue_.clear();
  writing_ = false;
  ws_.emplace(ioc_);
  auto self = shared_from_this();
  resolver_.async_resolve(
      endpoint_.host, std::to_string(endpoint_.port),
      [self, gen](beast::error_code ec, tcp::resolver::results_type results) {
        if (self->stopping_ || gen != self->generation_) return;
        if (ec) return self->fail("resolve", ec);
        asio::async_connect(
            self->ws_->next_layer(), results,
            [self, gen](beast::error_code ec, const tcp::endpoint &) {
              if (self->stopping_ || gen != self->generation_) return;
              if (ec) return self->fail("connect", ec);
              self->ws_->binary(true);
              self->ws_->set_option(wsTimeouts());
              const std::string host =
                  self->endpoint_.host + ":" +
                  std::to_string(self->endpoint_.port);
              self->ws_->async_handshake(
                  host, "/renderer/ws", [self, gen](beast::error_code ec) {
                    if (self->stopping_ || gen != self->generation_) return;
                    if (ec) return self->fail("ws handshake", ec);
                    self->up_ = true;
                    self->retryDelay_ = self->initialRetryDelay_;
                    if (self->events_.onUp) {
                      self->events_.onUp();
                    }
                    self->readLoop();
                  });
            });
      });
}

void WsTransport::readLoop() {
  auto self = shared_from_this();
  const uint64_t gen = generation_;
  ws_->async_read(readBuffer_, [self, gen](beast::error_code ec, std::size_t) {
    if (self->stopping_ || gen != self->generation_) return;
    if (ec) return self->fail("read", ec);
    self->enqueueMessage(beast::buffers_to_string(self->readBuffer_.data()));
    self->readBuffer_.consume(self->readBuffer_.size());
    if (!self->gaveUp_) {
      self->readLoop();
    }
  });
}

void WsTransport::enqueueMessage(std::string data) {
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

void WsTransport::drainInbox() {
  drainScheduled_ = false;
  while (!inbox_.empty() && !stopping_ && !gaveUp_) {
    const std::string data = std::move(inbox_.front());
    inbox_.pop_front();
    if (events_.onMessage) {
      events_.onMessage(data);
    }
  }
  inbox_.clear();
}

bool WsTransport::send(std::string data) {
  if (stopping_ || gaveUp_ || !up_ || !ws_ || !ws_->is_open()) {
    return false;
  }
  if (writeQueue_.size() >= kWriteQueueLimit) {
    spdlog::error("[{}] Write queue full ({} messages); dropping outgoing "
                  "message and dropping the connection",
                  endpoint_.name, writeQueue_.size());
    beast::error_code ec;
    ws_->next_layer().close(ec);
    return false;
  }
  writeQueue_.push_back(std::move(data));
  if (!writing_) {
    writeNext();
  }
  return true;
}

void WsTransport::writeNext() {
  if (writeQueue_.empty()) {
    writing_ = false;
    if (closeAfterWrite_) {
      closeAfterWrite_ = false;
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
  // No stopping_ check below: stop() relies on this loop to flush the final
  // frame; only a superseded stream's completion must bail.
  const uint64_t gen = generation_;
  ws_->async_write(asio::buffer(writeQueue_.front()),
                   [self, gen](beast::error_code ec, std::size_t) {
                     if (gen != self->generation_) return;
                     if (!self->writeQueue_.empty()) {
                       self->writeQueue_.pop_front();
                     }
                     self->writing_ = false;
                     if (ec) {
                       // Drop the rest; stop() still needs its close to
                       // happen.
                       self->writeQueue_.clear();
                       if (self->closeAfterWrite_) {
                         self->closeAfterWrite_ = false;
                         beast::error_code ignored;
                         self->ws_->next_layer().close(ignored);
                         self->closeTimer_.cancel();
                       } else {
                         self->fail("write", ec);
                       }
                       return;
                     }
                     self->writeNext();
                   });
}

void WsTransport::markDown() {
  if (!up_) {
    return;
  }
  up_ = false;
  if (events_.onDown) {
    events_.onDown();
  }
}

void WsTransport::fail(const char *stage, const beast::error_code &ec) {
  // A dropped link fails the pending read and the pending write; both land
  // here, and one drop must schedule exactly one retry.
  if (stopping_ || gaveUp_ || failing_) {
    return;
  }
  failing_ = true;
  markDown();
  spdlog::warn("[{}] {} failed: {}", endpoint_.name, stage, ec.message());
  beast::error_code ignored;
  ws_->next_layer().close(ignored);
  scheduleRetry();
}

void WsTransport::scheduleRetry() {
  if (stopping_ || gaveUp_) {
    return;
  }
  spdlog::info("[{}] Reconnecting in {}ms", endpoint_.name,
               retryDelay_.count());
  retryTimer_.expires_after(retryDelay_);
  retryDelay_ = std::min<std::chrono::milliseconds>(retryDelay_ * 2,
                                                    kMaxRetryDelay);
  auto self = shared_from_this();
  retryTimer_.async_wait([self](const boost::system::error_code &ec) {
    // gaveUp_ too: giving up between the schedule and the firing must win.
    if (ec || self->stopping_ || self->gaveUp_) return;
    self->connect();
  });
}

void WsTransport::giveUp() {
  if (gaveUp_) {
    return;
  }
  gaveUp_ = true;
  markDown();
  if (ws_) {
    beast::error_code ec;
    ws_->next_layer().close(ec);
  }
}

void WsTransport::stop(std::string finalFrame) {
  if (stopping_) {
    return;
  }
  stopping_ = true;
  retryTimer_.cancel();
  resolver_.cancel();

  if (!ws_ || !ws_->next_layer().is_open()) {
    markDown();
    return;
  }
  auto self = shared_from_this();
  if (up_ && !finalFrame.empty() && ws_->is_open()) {
    // Best-effort final frame + close; the deadline timer guarantees exit.
    closeAfterWrite_ = true;
    if (writeQueue_.size() < kWriteQueueLimit) {
      writeQueue_.push_back(std::move(finalFrame));
    }
    if (!writing_) {
      writeNext();  // writes what is queued, then closes
    }
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
  markDown();
}
