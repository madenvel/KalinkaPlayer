#ifndef PROCESS_NODE_H
#define PROCESS_NODE_H

#include <memory>
#include <stdexcept>
#include <stop_token>

struct NotImplemented : std::runtime_error {
  NotImplemented() : std::runtime_error("Function not implemented") {}
};

class ProcessNode {
public:
  virtual size_t write(const void *data, size_t bytes,
                       std::stop_token stopToken = std::stop_token());
  virtual size_t read(void *data, size_t bytes,
                      std::stop_token stopToken = std::stop_token());
  virtual bool eof();

  virtual void connectIn(std::shared_ptr<ProcessNode> inputNode);
  virtual void connectOut(std::shared_ptr<ProcessNode> outputNode);
  virtual void setStreamFinished();

  virtual void start();
  virtual void stop();

  virtual ~ProcessNode() = default;
};

#endif