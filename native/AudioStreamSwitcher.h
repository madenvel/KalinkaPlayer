#ifndef AUDIO_GRAPH_CONTROLLER_H
#define AUDIO_GRAPH_CONTROLLER_H

#include "AudioGraphNode.h"

#include <list>

// TODO: fix thread safety of this code
class AudioStreamSwitcher : public AudioGraphOutputNode,
                            public AudioGraphInputNode {
public:
  AudioStreamSwitcher();
  virtual ~AudioStreamSwitcher() = default;

  virtual void connectTo(AudioGraphOutputNode *inputNode) override;
  virtual void disconnect(AudioGraphOutputNode *inputNode) override;

  virtual size_t read(void *data, size_t size) override;
  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) override;

  virtual StreamInfo getStreamInfo() override;

  virtual AudioGraphNodeState getState() override;
  virtual AudioGraphNodeState waitForStatus(
      std::stop_token stopToken,
      std::optional<AudioGraphNodeState> nextStatus = std::nullopt) override;

private:
  std::list<AudioGraphOutputNode *> inputNodes;
  AudioGraphOutputNode *currentInputNode = nullptr;
  bool changedSource = false;

  void switchToNextSource();
};

#endif