#pragma once

#include <map>
#include <string>

#include "Player.h"

/**
 * @brief Stands in for the audio graph until it is wired up.
 *
 * Anything that would produce sound puts the renderer in PLAYBACK_STATE_ERROR,
 * so the Core hears about it the same way it will hear about a real playback
 * failure, while stop() and clearQueue() are honest no-ops that return it to
 * STOPPED. Configuration is real as far as it goes: the fields are declared and
 * written, but the device list is empty because there is no enumerator behind
 * it yet, and values live only in memory.
 */
class StubPlayer : public Player {
public:
  void setStateSink(StateSink sink) override;

  void setSource(const kalinka::renderer::v1::Source &source) override;
  void enqueueSource(const kalinka::renderer::v1::Source &source) override;
  void removeSource(const std::string &sourceToken) override;
  void clearQueue() override;
  void pause() override;
  void resume() override;
  void stop() override;
  void setVolume(uint32_t percent) override;
  void seek(uint64_t positionMs) override;
  void fillConfig(kalinka::renderer::v1::ConfigSection &out) const override;
  bool applyConfig(const std::string &path, const std::string &value,
                   std::string &error) override;

  void fillSnapshot(kalinka::renderer::v1::StateSnapshot &out) const override;

private:
  void notImplemented(const char *command);
  void reportStopped();

  StateSink sink_;
  bool failed_ = false;
  // Held in memory only: persisting them belongs with the config file the real
  // player will read at startup.
  std::map<std::string, std::string> settings_{{"output.driver", "alsa"},
                                               {"output.device", "default"}};
};
