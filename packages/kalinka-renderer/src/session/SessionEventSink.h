#pragma once

#include <memory>
#include <string>

#include "SessionTransport.h"

namespace kalinka::renderer::v1 {
class Command;
}

/**
 * @brief The session as a connection sees it: commands in, connection news in.
 *
 * Implemented by Session. A connection routes everything session-bound through
 * this interface and gates on sessionId() — a command naming anything else is
 * rejected before the session ever sees it.
 *
 * @note Every call is made on the io_context thread; no locking anywhere.
 */
class SessionEventSink {
public:
  virtual ~SessionEventSink() = default;

  /// The id the gate compares against; fixed for the session's lifetime.
  virtual const std::string &sessionId() const = 0;

  /**
   * @brief Use this connection as a route to the session's owner.
   *
   * Called by the connection that opened the session and by any later
   * connection that finds, after its handshake, that it carries the owner —
   * which is how a session survives a reconnect. Answers by sending the full
   * state snapshot on the new route, so the Core behind it never starts blind.
   *
   * Attaching a connection that is already a route refreshes it — and the
   * snapshot — rather than doubling it, so a retried open is harmless. A
   * session that has already ended ignores the call: its routes were told via
   * SessionTransport::onSessionClosed() and there is nothing to attach to.
   *
   * @param transport Held weakly, newest preferred.
   */
  virtual void attach(std::weak_ptr<SessionTransport> transport) = 0;

  /**
   * @brief Run one command from the session's owner.
   *
   * The connection has already gated it, so nothing is refused here — what a
   * command did is reported as state, so there is no per-command
   * acknowledgement.
   *
   * @param command What to do. Commands run in arrival order, one at a time.
   */
  virtual void onCommand(const kalinka::renderer::v1::Command &command) = 0;

  /**
   * @brief One route to the owner is gone.
   *
   * Losing the last one arms the release rules: a session whose owner is away
   * does not outlive its grace period, and never outlives playback. Losing
   * one of several changes nothing — the owner is still reachable.
   *
   * @param transport Which connection, so the right route is dropped. One that
   *                  was never attached is ignored.
   */
  virtual void onConnectionClosed(const SessionTransport *transport) = 0;
};
