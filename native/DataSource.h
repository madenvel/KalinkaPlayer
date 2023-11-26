#ifndef DATA_SOURCE_H
#define DATA_SOURCE_H

#include <curlpp/Easy.hpp>

#include "Buffer.h"
#include <atomic>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <thread>

#include "ProcessNode.h"

class HttpRequestNode : public ProcessNode {
  curlpp::Easy request;
  std::jthread readerThread;
  std::weak_ptr<ProcessNode> out;
  std::string url;
  size_t totalSize = 0;

public:
  HttpRequestNode(std::string url);
  virtual ~HttpRequestNode() = default;

  virtual void connectOut(std::shared_ptr<ProcessNode> outputNode) override;

  virtual void start() override;
  virtual void stop() override;

private:
  void startTransfer();
  void reader(std::stop_token token);
  size_t WriteCallback(void *contents, size_t size, size_t nmemb);
};

#endif