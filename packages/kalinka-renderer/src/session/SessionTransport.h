#pragma once

namespace kalinka::renderer::v1 {
class Envelope;
}

/**
 * @brief The way back to the Core: state out, and news that the session ended.
 *
 * Implemented by CoreConnection and held weakly by Session, so a connection
 * that dropped simply stops being a route.
 *
 * @note Every call is made on the io_context thread; no locking anywhere.
 */
class SessionTransport {
public:
  virtual ~SessionTransport() = default;

  /**
   * @brief Stamp message_id and send.
   *
   * @param env Payload and session_id already set by the caller.
   * @return true when the message was accepted for delivery — queued behind
   *         whatever is in flight, not confirmed received; state is
   *         fire-and-forget end to end. false when this connection cannot
   *         carry it at all, so the caller can try another route.
   */
  virtual bool sendSessionMessage(kalinka::renderer::v1::Envelope &env) = 0;

  /**
   * @brief The session this connection was attached to has ended.
   *
   * The connection forgets it; commands arriving after this are rejected. When
   * the renderer itself gave up on the session, this is also where a
   * SessionClosed(REASON_RENDERER_ERROR) would go out — nothing produces that
   * today.
   */
  virtual void onSessionClosed() = 0;
};
