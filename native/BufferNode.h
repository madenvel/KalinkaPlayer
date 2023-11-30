#ifndef BUFFER_NODE_H
#define BUFFER_NODE_H

#include "Buffer.h"
#include "ProcessNode.h"

class BufferNode : public ProcessNode {
public:
  enum BlockModeFlags {
    BlockOnWrite = 0x1,
    BlockOnRead = 0x2,
  };

private:
  DequeBuffer<uint8_t> buffer;
  int flags;

  std::exception_ptr error_;

public:
  BufferNode(size_t bufferSize, int flags) : buffer(bufferSize), flags(flags) {}

  virtual size_t write(const void *data, size_t bytes,
                       std::stop_token stopToken = std::stop_token()) override;
  virtual size_t read(void *data, size_t bytes,
                      std::stop_token stopToken = std::stop_token()) override;
  virtual bool eof() override;

  virtual void setStreamFinished() override;

  virtual std::exception_ptr error() const override;
  virtual void setStreamError(std::exception_ptr error) override;
};

#endif