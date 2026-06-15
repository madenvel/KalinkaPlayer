#ifndef AUDIO_PLAYER_H
#define AUDIO_PLAYER_H

#include <list>
#include <memory>
#include <string>

#include "AlsaAudioEmitter.h"
#include "AlsaVolumeControl.h"
#include "Config.h"

struct StreamState;
class AudioStreamSwitcher;
struct StreamNodes;
class StateMonitor;

enum AudioFormat { FormatFlac = 0, FormatMpeg };

using StreamId = size_t;

class AudioPlayer {
public:
  AudioPlayer(const Config &config);
  ~AudioPlayer();

  // Append a stream to the playback queue. Returns a StreamId that can be used
  // to remove the stream later. Playback starts automatically if no non-finished
  // stream is currently active (e.g. after stop() or clearAll()).
  StreamId append(const std::string &url,
                  const AudioFormat format = AudioFormat::FormatFlac);

  // Remove a stream from the playback queue by StreamId.
  void remove(StreamId id);

  // Remove all streams from the queue but keep the stream switcher connected
  // to the emitter. Unlike stop(), the audio device stays open and playback
  // resumes automatically when a new stream is appended.
  void clearAll();

  // Stop playback and close the device.
  void stop();

  // Pause the playback.
  // Note that if paused for too long, the http stream may be closed by the
  // server. The API supports reconnection but depending on the URL, it might
  // expire by the time the player tries to reconnect.
  void pause();

  // Resume the playback after pause().
  void resume();

  size_t seek(size_t positionMs);

  // Retrieves the current state of the node (non-blocking)
  StreamState getState();

  // Returns all state changes one by one as they occur.
  // Waits for the state change if no new state has been set yet.
  std::unique_ptr<StateMonitor> monitor();

  // Volume control for the currently selected ALSA output, honoring
  // output.alsa.volume_mode (auto/hardware/software/fixed). Values are 0..100
  // percent. getVolume() reports supported=false when no backend applies (e.g.
  // mode=fixed, or mode=hardware on a card with no mixer).
  VolumeState getVolume();
  void setVolume(int percent);

  // Monitor external hardware-mixer changes so the UI can track a knob / amixer
  // / another app. Inert unless the active backend is hardware. Mirrors
  // monitor() for stream state.
  std::unique_ptr<VolumeMonitor> volumeMonitor();

private:
  Config config;
  std::shared_ptr<AlsaAudioEmitter> audioEmitter;
  std::shared_ptr<AudioStreamSwitcher> streamSwitcher;
  std::list<StreamNodes> streamNodesList;
  StreamId nextStreamId = 0;

  std::unique_ptr<AlsaVolumeControl> volumeControl;
  VolumeMode volumeMode = VolumeMode::Auto;
  int softwarePercent = 100;

  VolumeBackend activeBackend() const;

  void disconnectAllStreams();
  void cleanUpFinishedStreams();
};

#endif