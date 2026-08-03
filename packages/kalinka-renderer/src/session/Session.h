#pragma once

#include <boost/asio.hpp>

#include <chrono>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#include "../player/Player.h"
#include "SessionEventSink.h"
#include "SessionTransport.h"

/**
 * @brief One Core's exclusive claim on the audio graph, for as long as it
 *        lasts.
 *
 * Ties the connection and the player together: commands arriving on the
 * owner's connection become Player calls, and the player's state goes back out
 * through that connection. The session outlives any one connection — a dropped
 * link leaves playback running, and the owner picks the session back up by
 * attaching the connection it returns on.
 *
 * The session also ends *without* the owner's involvement, so a Core that was
 * reinstalled — or that lost its server_id and now presents a new one — cannot
 * leave the renderer claimed forever:
 *   - owner gone while nothing is playing: closed immediately;
 *   - owner gone while playing:            closed after ownerGrace.
 * Closing stops the player: the audio graph belongs to whoever holds the
 * session, however the session ends.
 *
 * @note Playback is not implemented yet, so the state is hard-wired to Stopped
 *       and only the first rule can fire in practice. setPlaybackState() is the
 *       hook the player will call once the audio graph is wired up, at which
 *       point the grace timer starts doing real work.
 * @note Lives on the io_context thread; no locking.
 */
class Session : public SessionEventSink,
                public std::enable_shared_from_this<Session> {
public:
  enum class PlaybackState { Stopped, Playing };

  /**
   * @brief The only way to construct: installs the player's state sink, which
   *        needs a live shared_ptr to this.
   *
   * @param ioc        The io_context every handler runs on.
   * @param ownerGrace How long a playing session survives its owner going away
   *                   before being closed.
   * @param player     The audio seam; the session owns its lifecycle from here.
   * @param onEnded    Tells whoever tracks the running session that this one is
   *                   over, however it ended.
   */
  static std::shared_ptr<Session> create(boost::asio::io_context &ioc,
                                         std::string sessionId,
                                         std::string ownerServerId,
                                         std::chrono::seconds ownerGrace,
                                         std::shared_ptr<Player> player,
                                         std::function<void()> onEnded);

  const std::string &sessionId() const override { return sessionId_; }
  const std::string &ownerServerId() const { return ownerServerId_; }

  void attach(std::weak_ptr<SessionTransport> transport) override;
  void onCommand(const kalinka::renderer::v1::Command &command) override;
  void onConnectionClosed(const SessionTransport *transport) override;

  /**
   * @brief End the session: stop the player, tell every attached connection,
   *        then report onEnded. Idempotent.
   */
  void close(const char *reason);

  /**
   * @brief Send full state to the owner.
   *
   * Sent whenever a connection attaches, so a Core never starts blind, and
   * whenever it asks with RequestSnapshot.
   */
  void publishSnapshot();

  /// The player reporting what it is doing, which decides the release rules.
  void setPlaybackState(PlaybackState state);

private:
  Session(boost::asio::io_context &ioc, std::string sessionId,
          std::string ownerServerId, std::chrono::seconds ownerGrace,
          std::shared_ptr<Player> player, std::function<void()> onEnded);

  /**
   * @brief Send a state message to the owner.
   *
   * @param env Payload already set; the session id is stamped here.
   * @return false when no attached connection can carry it. The message is
   *         then dropped rather than queued: a fresh snapshot goes out when
   *         the owner reattaches, so stale state has no value.
   */
  bool publish(kalinka::renderer::v1::Envelope &env);

  bool attached() const;
  void ownerLost();

  const std::string sessionId_;
  const std::string ownerServerId_;
  std::chrono::seconds ownerGrace_;
  boost::asio::steady_timer graceTimer_;
  std::shared_ptr<Player> player_;
  std::function<void()> onEnded_;
  // Routes to the owner, newest last. Usually one; more only while the owner
  // flaps between interfaces and two connections briefly overlap.
  std::vector<std::weak_ptr<SessionTransport>> transports_;
  PlaybackState playbackState_ = PlaybackState::Stopped;
  bool closed_ = false;
};
