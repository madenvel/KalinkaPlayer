#ifndef ERROR_FAKE_NODE_H
#define ERROR_FAKE_NODE_H

#include "AudioGraphNode.h"

class ErrorFakeNode : public AudioGraphOutputNode {
public:
  ErrorFakeNode() : AudioGraphOutputNode() {
    setState(StreamState{AudioGraphNodeState::ERROR, "Fake error message"});
  }
  virtual size_t read(void *data, size_t size) override { return 0; }
  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) override {
    return 0;
  }
};

#endif // ERROR_FAKE_NODE_H