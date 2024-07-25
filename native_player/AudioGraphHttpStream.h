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
  std::jthread readerThread;
  std::string url;
  DequeBuffer<uint8_t> buffer;
  std::stop_source stopSource;
  size_t contentLength = 1;
  size_t offset = 0;
  bool acceptRange = false;

  void reader(std::stop_token token);
  void readContentChunks(std::stop_token token);
  int readSingleChunk(size_t offset, size_t size, std::stop_token stopToken);
  size_t WriteCallback(void *contents, size_t size, size_t nmemb);
  void emptyBufferCallback(DequeBuffer<uint8_t> &buffer);
  size_t headerCallback(char *buffer, size_t size, size_t nitems);
  void readHeader();
};

#endif