#ifndef AUDIO_GRAPH_NODE_H
#define AUDIO_GRAPH_NODE_H

#include "AudioInfo.h"
#include "Buffer.h"
#include "StreamState.h"

#include <condition_variable>
#include <iostream>
#include <list>
#include <mutex>
#include <optional>
#include <queue>
#include <thread>

class StateMonitor;

class AudioGraphNode {
public:
  AudioGraphNode() : state(AudioGraphNodeState::STOPPED) {}

  virtual StreamState getState();

  virtual StreamState
  waitForStatus(std::stop_token stopToken,
                std::optional<AudioGraphNodeState> nextState = std::nullopt);

  virtual ~AudioGraphNode();

protected:
  friend class StateMonitor;
  void setState(const StreamState &newState);

private:
  StreamState state;
  std::mutex mutex;
  std::condition_variable_any cv;
  bool done = false;

  std::list<StateMonitor *> monitors;
};

class StateMonitor {
public:
  friend class AudioGraphNode;

  StateMonitor(AudioGraphNode *ptr);
  StateMonitor(const StateMonitor &) = delete;
  StateMonitor &operator=(const StateMonitor &) = delete;
  ~StateMonitor();

  StreamState waitState();

  bool hasData();

  void stop();

protected:
  std::queue<StreamState> queue_;
  AudioGraphNode *ptr;

  bool stopped = false;
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