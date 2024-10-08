#ifndef FILE_INPUT_NODE_H
#define FILE_INPUT_NODE_H

#include "AudioGraphNode.h"

#include <fstream>

class FileInputNode : public AudioGraphOutputNode {
private:
  std::ifstream in;
  size_t fileSize;

public:
  FileInputNode(const std::string &filename)
      : in(filename, std::ios::in | std::ios::binary) {
    if (!in.is_open()) {
      throw std::runtime_error("Failed to open file");
    }
    setState(StreamState(AudioGraphNodeState::PREPARING));
    in.seekg(0, std::ios::end);
    fileSize = in.tellg();
    in.seekg(0, std::ios::beg);
    setState(StreamState(
        AudioGraphNodeState::STREAMING, 0,
        StreamInfo{.streamType = StreamType::BYTES, .streamSize = fileSize}));
  }

  virtual size_t read(void *data, size_t size) override {
    in.read(static_cast<char *>(data), size);
    // Except in the constructors of std::strstreambuf, negative values of
    // std::streamsize are never used.
    size_t bytesRead = static_cast<size_t>(in.gcount());
    if (bytesRead < size) {
      setState(StreamState(AudioGraphNodeState::FINISHED));
    }
    return bytesRead;
  }

  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) override {
    return std::min(fileSize - in.tellg(), size);
  }

  virtual size_t waitForDataFor(std::stop_token stopToken,
                                std::chrono::milliseconds timeout,
                                size_t size) override {
    (void)timeout;
    return waitForData(stopToken, size);
  }

  virtual size_t seekTo(size_t absolutePosition) override {
    absolutePosition = std::min(absolutePosition, fileSize);
    setState(StreamState(AudioGraphNodeState::PREPARING));
    in.clear();
    in.seekg(absolutePosition, std::ios::beg);
    if (absolutePosition < fileSize) {
      setState(StreamState(
          AudioGraphNodeState::STREAMING, absolutePosition,
          StreamInfo{.streamType = StreamType::BYTES, .streamSize = fileSize}));
    } else {
      setState(StreamState(
          AudioGraphNodeState::FINISHED, absolutePosition,
          StreamInfo{.streamType = StreamType::BYTES, .streamSize = fileSize}));
    }
    return in.tellg();
  }

  virtual ~FileInputNode() = default;
};

#endif