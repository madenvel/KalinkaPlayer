#ifndef AUDIO_PLAYER_H
#define AUDIO_PLAYER_H

#include <list>
#include <memory>
#include <string>

#include "Config.h"

struct StreamState;
class AudioGraphEmitterNode;
class AudioStreamSwitcher;
struct StreamNodes;
class StateMonitor;

class AudioPlayer {
public:
  AudioPlayer(const Config &config);
  ~AudioPlayer();

  // Open a stream and start playing it immediately.
  // Cleans the list of the next streams to be played.
  void play(const std::string &url);

  // Open a stream and set it to be played next after the current one is
  // finished.
  void playNext(const std::string &url);

  // Stop playback and close the device
  void stop();

  // Pause the playback.
  // Note that if paused for too long, the http stream may be closed by the
  // server. The API supports reconnection but depending on the URL, it might
  // expire by the time the player tries to reconnect.
  void pause(bool paused);

  size_t seek(size_t positionMs);

  // Retrieves the current state of the node (non-blocking)
  StreamState getState();

  // Returns all state changes one by one as they occur.
  // Waits for the state change if no new state has been set yet.
  std::unique_ptr<StateMonitor> monitor();

private:
  Config config;
  std::shared_ptr<AudioGraphEmitterNode> audioEmitter;
  std::shared_ptr<AudioStreamSwitcher> streamSwitcher;
  std::list<StreamNodes> streamNodesList;

  void disconnectAllStreams();
  void cleanUpFinishedStreams();
};

#endif