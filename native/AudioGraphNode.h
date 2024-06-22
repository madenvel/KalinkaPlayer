#ifndef AUDIO_GRAPH_NODE_H
#define AUDIO_GRAPH_NODE_H

#include "AudioInfo.h"
#include "Buffer.h"

#include <condition_variable>
#include <iostream>
#include <list>
#include <mutex>
#include <optional>
#include <queue>

enum class AudioGraphNodeState {
  ERROR = -1,
  STOPPED,
  PREPARING,
  STREAMING,
  PAUSED,
  FINISHED,
  SOURCE_CHANGED
};

class StateMonitor;

class AudioGraphNode {
public:
  AudioGraphNode() : state(AudioGraphNodeState::STOPPED) {}

  virtual AudioGraphNodeState getState();

  virtual AudioGraphNodeState
  waitForStatus(std::stop_token stopToken,
                std::optional<AudioGraphNodeState> nextState = std::nullopt);

  virtual ~AudioGraphNode() = default;

protected:
  friend class StateMonitor;
  void setState(AudioGraphNodeState newState);

private:
  AudioGraphNodeState state;
  std::mutex mutex;
  std::condition_variable_any cv;

  std::list<StateMonitor *> monitors;
};

class StateMonitor {
public:
  friend class AudioGraphNode;

  StateMonitor(AudioGraphNode &ptr);

  ~StateMonitor();

  AudioGraphNodeState waitState();

  bool hasData();

private:
  std::queue<AudioGraphNodeState> queue_;
  AudioGraphNode &ptr;
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
  virtual StreamInfo getStreamInfo() = 0;
  virtual ~AudioGraphOutputNode() = default;
};

class AudioGraphInputNode : public virtual AudioGraphNode {
public:
  virtual void connectTo(AudioGraphOutputNode *outputNode) = 0;
  virtual void disconnect(AudioGraphOutputNode *outputNode) = 0;
  virtual ~AudioGraphInputNode() = default;
};

class AudioGraphEmitterNode : public AudioGraphInputNode {
public:
  virtual void pause(bool paused) {
    if (paused) {
      setState(AudioGraphNodeState::PAUSED);
    } else {
      setState(AudioGraphNodeState::STREAMING);
    }
  }

  virtual long getPosition() = 0;
  virtual StreamInfo getStreamInfo() = 0;
};

#endif