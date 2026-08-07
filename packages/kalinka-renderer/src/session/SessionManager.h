#pragma once

#include <boost/asio.hpp>

#include <chrono>
#include <memory>
#include <string>

#include "../player/Player.h"
#include "Session.h"

/**
 * @brief Hands out the renderer's one playback session.
 *
 * The slot is single, and may be empty: open() creates the session or refuses
 * because another one is running or the player cannot apply its start policy.
 * The manager tracks the running session; the session itself decides how it
 * ends and reports back here, so current() is never stale.
 *
 * @note Must be held in a shared_ptr (it is one of RendererServices);
 *       sessions report their end through a weak reference to it.
 * @note Lives on the io_context thread; no locking.
 */
class SessionManager : public std::enable_shared_from_this<SessionManager> {
public:
  /**
   * @param ioc        The io_context sessions run their grace timer on.
   * @param ownerGrace How long a session survives its owner going away
   *                   before being closed.
   * @param player     The audio seam every session is created over.
   */
  SessionManager(boost::asio::io_context &ioc, std::chrono::seconds ownerGrace,
                 std::shared_ptr<Player> player);

  /**
   * @brief Claim the audio graph for a Core.
   *
   * Idempotent for the owner: the same id from the same Core is a retried open
   * and returns the running session again. The same id from anyone else is a
   * replay of what Hello broadcasts, and must not hand the graph over.
   *
   * @param busyOwner Filled with the server_id holding the session when this
   *                  returns nullptr because the renderer is busy.
   * @param volume    Volume policy that must be applied before commands run.
   * @param error     Filled when the player's start policy fails.
   * @return The session — the caller attaches its connection to it — or
   *         nullptr when it cannot be opened.
   */
  std::shared_ptr<Session> open(const std::string &sessionId,
                                const std::string &ownerServerId,
                                std::string &busyOwner,
                                const SessionVolume &volume = {},
                                std::string *error = nullptr);

  /**
   * @brief End the session. Only its owner may do this.
   *
   * @return true when @p sessionId was the running session and @p
   *         requesterServerId owns it; false — and nothing happens — otherwise,
   *         including for an id that already ended.
   */
  bool close(const std::string &sessionId,
             const std::string &requesterServerId);

  /// Close the running session, if any: the process is exiting, and playback
  /// must not outlive it — the owner grace is for owners, not for shutdown.
  void shutdown();

  /// The running session, if there is one. Broadcast in Hello.
  std::shared_ptr<Session> current() const { return current_; }

  /**
   * @brief The running session, when @p serverId owns it.
   *
   * How a connection that just finished its handshake finds the session it
   * should attach to — the reconnect half of session restore.
   */
  std::shared_ptr<Session> ownedBy(const std::string &serverId) const;

private:
  void forget(const std::string &sessionId);

  boost::asio::io_context &ioc_;
  std::chrono::seconds ownerGrace_;
  std::shared_ptr<Player> player_;
  std::shared_ptr<Session> current_;
};
