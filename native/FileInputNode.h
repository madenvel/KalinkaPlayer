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
    setState(AudioGraphNodeState::PREPARING);
    in.seekg(0, std::ios::end);
    fileSize = in.tellg();
    in.seekg(0, std::ios::beg);
    setState(AudioGraphNodeState::STREAMING);
  }

  virtual size_t read(void *data, size_t size) override {
    in.read(static_cast<char *>(data), size);
    // Except in the constructors of std::strstreambuf, negative values of
    // std::streamsize are never used.
    size_t bytesRead = static_cast<size_t>(in.gcount());
    if (bytesRead < size) {
      setState(AudioGraphNodeState::FINISHED);
    }
    return bytesRead;
  }

  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) {
    return std::min(fileSize - in.tellg(), size);
  }

  virtual StreamInfo getStreamInfo() override { return StreamInfo(); }

  virtual ~FileInputNode() = default;
};

#endif