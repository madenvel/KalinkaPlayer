#pragma once

#include <optional>
#include <string>

// The single playback session this renderer serves, shared by every Core
// connection (only one Core may drive the audio graph at a time). The session
// outlives the connection it was opened on: a dropped link leaves playback
// running and the Core resumes the session when it returns.
//
// Lives on the io_context thread; no locking.
class SessionManager {
public:
  struct Active {
    std::string sessionId;
    std::string ownerServerId;
  };

  // Fails when another session is already running; busyOwner then carries the
  // server_id currently holding it.
  bool open(const std::string &sessionId, const std::string &ownerServerId,
            std::string &busyOwner);

  // True if sessionId was the active session (idempotent otherwise).
  bool close(const std::string &sessionId);

  const std::optional<Active> &active() const { return active_; }

private:
  std::optional<Active> active_;
};
