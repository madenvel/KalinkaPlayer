#ifndef AUDIO_GRAPH_NODE_H
#define AUDIO_GRAPH_NODE_H

#include "AudioInfo.h"
#include "Buffer.h"
#include "StreamState.h"

#include <iostream>
#include <list>
#include <mutex>
#include <optional>
#include <queue>
#include <thread>
#include <unordered_map>

class AudioGraphNode {
public:
  AudioGraphNode() : state(AudioGraphNodeState::STOPPED) {}

  virtual StreamState getState();

  virtual int
  onStateChange(std::function<bool(AudioGraphNode *, StreamState)> callback);
  virtual void removeStateChangeCallback(int id);

  virtual void acceptSourceChange() {};
  virtual ~AudioGraphNode();

protected:
  void setState(const StreamState &newState);

private:
  std::mutex mutex;
  StreamState state;

  std::unordered_map<int, std::function<bool(AudioGraphNode *, StreamState)>>
      stateChangeCallbacks;
  int callbackId = 0;
};

class AudioGraphOutputNode : public virtual AudioGraphNode {
public:
  // Reads the data to the buffer and returns the number of bytes read.
  // Sets the state to FINISHED if there's no more data expected.
  // Note: the state may need additiona `read` call to be set to FINISHED,
  //       Even though the buffer is already empty.
  virtual size_t read(void *data, size_t size) = 0;
  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) = 0;
  virtual ~AudioGraphOutputNode() = default;
};

class AudioGraphInputNode : public virtual AudioGraphNode {
public:
  virtual void connectTo(std::shared_ptr<AudioGraphOutputNode> outputNode) = 0;
  virtual void disconnect(std::shared_ptr<AudioGraphOutputNode> outputNode) = 0;
  virtual ~AudioGraphInputNode() = default;
};

class AudioGraphEmitterNode : public AudioGraphInputNode {
public:
  virtual void pause(bool paused) = 0;
};

#endif