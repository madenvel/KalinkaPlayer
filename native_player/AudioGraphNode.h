#ifndef AUDIO_GRAPH_NODE_H
#define AUDIO_GRAPH_NODE_H

#include "AudioInfo.h"
#include "Buffer.h"
#include "StreamState.h"

#include <mutex>
#include <thread>
#include <unordered_map>

class AudioGraphNode {
public:
  AudioGraphNode() : state(AudioGraphNodeState::STOPPED) {}

  // Get inner node state.
  // This function is not generating a new state, instead
  // it returns the last state set by the node.
  virtual StreamState getState();

  // Set the callback to be called when the state changes.
  // Returns the id of the callback.
  virtual int
  onStateChange(std::function<bool(AudioGraphNode *, StreamState)> callback);

  // Remove the callback with the given id.
  virtual void removeStateChangeCallback(int id);

  // Accept the source change. This is called when the source has changed and
  // the reader should accept the new source.
  // The data transfer stops until the source is accepted.
  // It has no effect if the node is not in SOURCE_CHANGED state.
  virtual void acceptSourceChange() {};
  virtual ~AudioGraphNode();

protected:
  // To be used by the derived classes to set the state.
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
  // Sets the state to FINISHED when all data has been read.
  virtual size_t read(void *data, size_t size) = 0;

  // Waits for data to be available and returns the number of bytes available.
  // The size parameter is the minimum number of bytes to wait for.
  // The function can return earlier if the stopToken is set or the source has
  // changed.
  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) = 0;

  virtual size_t waitForDataFor(std::stop_token stopToken,
                                std::chrono::milliseconds timeout,
                                size_t size) = 0;
  virtual size_t seekTo(size_t absolutePosition) { return -1; }

  virtual ~AudioGraphOutputNode() = default;
};

class AudioGraphInputNode : public virtual AudioGraphNode {
public:
  // Connect to reading node to the source of the data
  virtual void connectTo(std::shared_ptr<AudioGraphOutputNode> outputNode) = 0;

  // Disconnect previously connected node
  virtual void disconnect(std::shared_ptr<AudioGraphOutputNode> outputNode) = 0;
  virtual ~AudioGraphInputNode() = default;
};

class AudioGraphEmitterNode : public AudioGraphInputNode {
public:
  virtual void pause(bool paused) = 0;
  virtual size_t seek(size_t positionMs) = 0;
};

#endif