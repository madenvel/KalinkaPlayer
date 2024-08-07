#ifndef AUDIO_PLAYER_H
#define AUDIO_PLAYER_H

#include <list>
#include <memory>
#include <string>

struct StreamState;
class AudioGraphEmitterNode;
class AudioStreamSwitcher;
struct StreamNodes;
class StateMonitor;

class AudioPlayer {
public:
  AudioPlayer(const std::string &audioDevice);
  ~AudioPlayer();

  // Open a stream and start playing it immediately.
  // Cleans the list of the next streams to be played.
  void play(const std::string &url);

  // Open a stream and set it to be played next after the current one is
  // finished.
  void playNext(const std::string &url);

  // Stop playback and close the device
  void stop();

  // Pause the streaming.
  // Note that if paused for too long, the http stream may be closed by the
  // server. The API doesn't support reconnection but even if it did, the link
  // might expired.
  void pause(bool paused);

  size_t seek(size_t positionMs);

  // Retrieves the current state of the node (non-blocking)
  StreamState getState();

  // Returns all state changes one by one as they occur.
  // Waits for the state change if no new state has been set yet.
  std::unique_ptr<StateMonitor> monitor();

private:
  std::shared_ptr<AudioGraphEmitterNode> audioEmitter;
  std::shared_ptr<AudioStreamSwitcher> streamSwitcher;
  std::list<StreamNodes> streamNodesList;

  void disconnectAllStreams();
  void cleanUpFinishedStreams();
};

#endif