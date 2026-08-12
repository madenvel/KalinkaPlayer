#pragma once

#include <cstdint>
#include <functional>
#include <optional>
#include <string>

#include "../config/ConfigContributor.h"
#include "kalinka/renderer/v1/renderer.pb.h"

/// Session-scoped output policy, as carried on SessionOpen.
struct SessionVolumePolicy {
  /// Core routes volume downstream, so temporarily force fixed unity output.
  bool forceFixedOutput = false;
};

/**
 * @brief The seam between the protocol and the audio graph.
 *
 * Shaped like AudioPlayer, because that is what will sit behind it. Nothing
 * returns a result: a command either happens, and the state that follows says
 * so, or it fails, and the state says PLAYBACK_STATE_ERROR — the same way a
 * track that dies mid-stream is reported. That is how the native player already
 * behaves; append() starts playback and the outcome arrives later on the
 * StateMonitor.
 *
 * Commands are applied in the order they arrive on the session, one at a time.
 *
 * Also a ConfigContributor: the output driver, the device its enumerator
 * finds, and whatever else the backend needs are settings only the player can
 * declare, so it owns that section of the schema.
 *
 * @note The graph is not wired up yet; StubPlayer is what runs today.
 */
class Player : public ConfigContributor {
public:
  /// Envelope with its state payload set; the session layer stamps and sends it.
  using StateSink = std::function<void(kalinka::renderer::v1::Envelope &)>;

  virtual ~Player() = default;

  /**
   * @brief Where state goes.
   * @param sink Called on the io_context thread. Installed when a session is
   *             created and cleared — pass an empty function — when it ends,
   *             so state never outlives the session it belongs to.
   */
  virtual void setStateSink(StateSink sink) = 0;

  /**
   * @brief Play this source now, replacing the queue.
   *
   * It becomes the current source and everything queued behind it is dropped.
   * Playback starts on arrival — there is no separate start command, exactly as
   * AudioPlayer::append() starts on its own. Answers with PREPARING then
   * PLAYING, or ERROR if the source cannot be opened or decoded.
   *
   * @param source Where to play from, carrying the token every state message
   *               about it will be tagged with.
   */
  virtual void setSource(const kalinka::renderer::v1::Source &source) = 0;

  /**
   * @brief Queue a source behind the current one so the switch is gapless.
   *
   * Does not disturb what is playing; the switch is reported as SourceChanged
   * when it happens. A queued source that cannot be prepared is reported as
   * ERROR against its own token, without interrupting the current one.
   *
   * @param source Where to play from, and the token to report it by.
   */
  virtual void enqueueSource(const kalinka::renderer::v1::Source &source) = 0;

  /**
   * @brief Drop one source, queued or current.
   *
   * Dropping the current source ends it as if it had finished, so the next
   * queued source starts.
   *
   * @param sourceToken The token the source was given. Unknown tokens are
   *                    ignored — the Core may be dropping something already
   *                    gone.
   */
  virtual void removeSource(const std::string &sourceToken) = 0;

  /**
   * @brief Drop every source but keep the output device open.
   *
   * The next setSource() then starts without reopening it. Answers with
   * STOPPED.
   */
  virtual void clearQueue() = 0;

  /**
   * @brief Hold the current source where it is, keeping the queue and device.
   *
   * Answers with PAUSED.
   *
   * @note A long pause can outlive the HTTP stream behind it. Reopening it is
   *       the renderer's problem, and may fail on an expired URL.
   */
  virtual void pause() = 0;

  /**
   * @brief The inverse of pause(), and nothing more.
   *
   * A renderer holding no source has nothing to resume and stays STOPPED;
   * playing something is setSource(). Answers with PLAYING.
   */
  virtual void resume() = 0;

  /**
   * @brief Stop playback, drop every source and close the output device.
   *
   * Answers with STOPPED. Coming back from here is a setSource(), not a
   * resume().
   */
  virtual void stop() = 0;

  /**
   * @brief Set the output volume.
   *
   * Answers with VolumeChanged. A renderer whose backend is fixed or absent
   * reports supported=false and changes nothing.
   *
   * @param percent 0..100 on whatever volume backend the renderer runs.
   */
  virtual void setVolume(uint32_t percent) = 0;

  /**
   * @brief Apply a volume policy before one session can play.
   *
   * A fixed-output override is held in memory and undone by
   * endSessionVolume(). Nothing is written to renderer configuration: a Core
   * that routes volume to an amp must not leave this renderer fixed for whoever
   * uses it next. Without the override, the renderer's own mode is authoritative.
   *
   * @param volume Session policy supplied by the Core.
   * @param error  Why the safety policy could not be applied.
   * @return false when playback must not start because the policy could not be
   *         enforced.
   */
  virtual bool beginSessionVolume(const SessionVolumePolicy &,
                                  std::string &error) {
    error = "player cannot enforce the session volume policy";
    return false;
  }

  /// Restore the configured volume mode. Idempotent.
  virtual void endSessionVolume() {}

  /**
   * @brief Jump within the current source.
   *
   * Ignored when nothing is playing.
   *
   * @param positionMs Where to jump to. The position actually reached comes
   *                   back in the next state message and can differ from this.
   */
  virtual void seek(uint64_t positionMs) = 0;

  /**
   * @brief Everything the Core needs to catch up, in one message.
   *
   * Must be answerable at any time, including before anything has played.
   *
   * @param out Filled with the current state, position advanced to now.
   */
  virtual void
  fillSnapshot(kalinka::renderer::v1::StateSnapshot &out) const = 0;
};
