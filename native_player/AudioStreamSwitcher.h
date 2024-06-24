#ifndef AUDIO_GRAPH_CONTROLLER_H
#define AUDIO_GRAPH_CONTROLLER_H

#include "AudioGraphNode.h"

#include <list>

class AudioStreamSwitcher : public AudioGraphOutputNode,
                            public AudioGraphInputNode {
public:
  AudioStreamSwitcher();
  virtual ~AudioStreamSwitcher() = default;

  virtual void
  connectTo(std::shared_ptr<AudioGraphOutputNode> inputNode) override;
  virtual void
  disconnect(std::shared_ptr<AudioGraphOutputNode> inputNode) override;

  virtual size_t read(void *data, size_t size) override;

  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) override;

  virtual StreamState getState() override;
  virtual StreamState waitForStatus(
      std::stop_token stopToken,
      std::optional<AudioGraphNodeState> nextStatus = std::nullopt) override;

private:
  std::list<std::shared_ptr<AudioGraphOutputNode>> inputNodes;
  std::shared_ptr<AudioGraphOutputNode> currentInputNode = nullptr;

  std::mutex mutex;
  std::stop_source stopSource;

  void switchToNextSource();
};

#endif