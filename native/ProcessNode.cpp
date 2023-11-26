#include "ProcessNode.h"

size_t ProcessNode::write(const void *data, size_t bytes,
                          std::stop_token stopToken) {
  throw NotImplemented();
}

size_t ProcessNode::read(void *data, size_t bytes, std::stop_token stopToken) {
  throw NotImplemented();
}

bool ProcessNode::eof() { throw NotImplemented(); }

void ProcessNode::start() { throw NotImplemented(); }

void ProcessNode::stop() { throw NotImplemented(); }

void ProcessNode::connectIn(std::shared_ptr<ProcessNode> inputNode) {
  throw NotImplemented();
}
void ProcessNode::connectOut(std::shared_ptr<ProcessNode> outputNode) {
  throw NotImplemented();
}

void ProcessNode::setStreamFinished() { throw NotImplemented(); }
