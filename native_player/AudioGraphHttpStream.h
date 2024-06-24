#ifndef AUDIO_GRAPH_HTTP_H
#define AUDIO_GRAPH_HTTP_H

#include "AudioGraphNode.h"
#include "Buffer.h"

#include <curlpp/Easy.hpp>

class AudioGraphHttpStream : public AudioGraphOutputNode {
public:
  AudioGraphHttpStream(const std::string &url, size_t bufferSize);
  virtual size_t read(void *data, size_t size) override;
  virtual size_t waitForData(std::stop_token stopToken, size_t size) override;

  virtual ~AudioGraphHttpStream();

private:
  curlpp::Easy request;
  std::jthread readerThread;
  std::string url;
  DequeBuffer<uint8_t> buffer;
  std::stop_source stopSource;

  void reader(std::stop_token token);
  size_t WriteCallback(void *contents, size_t size, size_t nmemb);
  void emptyBufferCallback(DequeBuffer<uint8_t> &buffer);
};

#endif