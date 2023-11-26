#include "BufferNode.h"

size_t BufferNode::write(const void *data, size_t bytes,
                         std::stop_token stopToken) {
  if (flags & BlockModeFlags::BlockOnWrite) {
    buffer.waitForSpace(stopToken, bytes);
  }
  if (stopToken.stop_requested()) {
    return 0;
  }
  return buffer.write(static_cast<const uint8_t *>(data), bytes);
}

size_t BufferNode::read(void *data, size_t bytes, std::stop_token stopToken) {
  if (flags & BlockModeFlags::BlockOnRead) {
    buffer.waitForData(stopToken, bytes);
  }
  if (stopToken.stop_requested()) {
    return 0;
  }
  return buffer.read(static_cast<uint8_t *>(data), bytes);
}

bool BufferNode::eof() { return (buffer.empty() && buffer.isEof()); }

void BufferNode::setStreamFinished() { buffer.setEof(); }
