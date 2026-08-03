#pragma once

#include <boost/asio.hpp>

#include <chrono>
#include <map>
#include <memory>
#include <optional>
#include <string>

// The single playback session this renderer serves, shared by every Core
// connection (only one Core may drive the audio graph at a time). The session
// outlives the connection it was opened on: a dropped link leaves playback
// running and the owner picks the session back up when it returns.
//
// It is also released *without* the owner's involvement, so a Core that was
// reinstalled — or that lost its server_id and now presents a new one — cannot
// leave the renderer claimed forever:
//   - owner gone while nothing is playing: released immediately;
//   - owner gone while playing:            released after ownerGrace.
//
// NOTE: playback is not implemented yet, so the state is hard-wired to Stopped
// and only the first rule can fire in practice. setPlaybackState() is the hook
// the player will call once the audio graph is wired up, at which point the
// grace timer starts doing real work.
//
// Lives on the io_context thread; no locking.
class SessionManager
    : public std::enable_shared_from_this<SessionManager> {
public:
  struct Active {
    std::string sessionId;
    std::string ownerServerId;
  };

  enum class PlaybackState { Stopped, Playing };

  SessionManager(boost::asio::io_context &ioc,
                         std::chrono::seconds ownerGrace);

  // Welcome received / connection gone, reported per Core connection. Several
  // connections can carry the same server_id (one Core reachable on several
  // interfaces), so the owner counts as gone only when the last one drops.
  void coreConnected(const std::string &serverId);
  void coreDisconnected(const std::string &serverId);

  // Fails when another session is already running; busyOwner then carries the
  // server_id currently holding it.
  bool open(const std::string &sessionId, const std::string &ownerServerId,
            std::string &busyOwner);

  // True if sessionId was the active session and the requester owns it;
  // idempotent otherwise. Only the owner may end a session.
  bool close(const std::string &sessionId,
             const std::string &requesterServerId);

  const std::optional<Active> &active() const { return active_; }

  void setPlaybackState(PlaybackState state);

private:
  bool ownerConnected() const;
  void ownerLost();
  void release(const char *reason);

  boost::asio::io_context &ioc_;
  std::chrono::seconds ownerGrace_;
  boost::asio::steady_timer graceTimer_;
  std::map<std::string, int> connectedCores_;
  std::optional<Active> active_;
  PlaybackState playbackState_ = PlaybackState::Stopped;
};
