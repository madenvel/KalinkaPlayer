#ifndef AUDIO_GRAPH_HTTP_H
#define AUDIO_GRAPH_HTTP_H

#include "AudioGraphNode.h"
#include "Buffer.h"

#include "Utils.h"
#include <curlpp/Easy.hpp>

class AudioGraphHttpStream : public AudioGraphOutputNode {
public:
  AudioGraphHttpStream(const std::string &url, size_t bufferSize,
                       size_t chunkSize = 0);
  virtual size_t read(void *data, size_t size) override;
  virtual size_t waitForData(std::stop_token stopToken, size_t size) override;
  virtual size_t waitForDataFor(std::stop_token stopToken,
                                std::chrono::milliseconds timeout,
                                size_t size) override;
  virtual size_t seekTo(size_t absolutePosition) override;

  virtual ~AudioGraphHttpStream();

private:
  std::jthread readerThread;
  std::string url;
  DequeBuffer<uint8_t> buffer;
  size_t contentLength = 1;
  size_t offset = 0;
  Signal<size_t> seekRequestSignal;
  size_t chunkSize = 0;
  bool acceptRange = false;
  bool setStreamingState = true;

  void reader(std::stop_token token);
  void readContentChunks(std::stop_token token);
  int readSingleChunk(std::stop_token stopToken);
  size_t WriteCallback(void *contents, size_t size, size_t nmemb);
  void emptyBufferCallback(DequeBuffer<uint8_t> &buffer);
  size_t headerCallback(char *buffer, size_t size, size_t nitems);

  void readHeader();

  void handleSeekSignal(size_t position);

  std::mutex mutex;
  std::condition_variable_any cv;
  curlpp::Easy request;
};

#endif