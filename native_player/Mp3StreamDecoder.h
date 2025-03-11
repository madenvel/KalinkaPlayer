#ifndef MP3STREAMDECODER_H
#define MP3STREAMDECODER_H

#include "AudioGraphNode.h"

#include "Utils.h"
#include <thread>

class Mp3StreamDecoder : public AudioGraphOutputNode,
                         public AudioGraphInputNode {
public:
  Mp3StreamDecoder(size_t bufferSize);
  virtual ~Mp3StreamDecoder();

  size_t read(void *data, size_t size) override;

  size_t waitForData(std::stop_token stopToken = std::stop_token(),
                     size_t size = 1) override;

  size_t waitForDataFor(std::stop_token stopToken,
                        std::chrono::milliseconds timeout,
                        size_t size) override;

  size_t seekTo(size_t absolutePosition) override;

  void connectTo(std::shared_ptr<AudioGraphOutputNode> inputNode) override;

  void disconnect(std::shared_ptr<AudioGraphOutputNode> inputNode) override;

private:
  std::jthread decodingThread;
  std::shared_ptr<AudioGraphOutputNode> inputNode;
  Buffer<uint8_t> buffer;

  Signal<size_t> seekSignal;
  Signal<bool> initCompleteSignal;

  long currentPos = 0;

  void threadRun(std::stop_token token);
  void onEmptyBuffer(Buffer<uint8_t> &buffer);

  typedef size_t (*MP3D_READ_CB)(void *buf, size_t size, void *user_data);
  typedef int (*MP3D_SEEK_CB)(uint64_t position, void *user_data);

  size_t readCallback(void *buf, size_t size);
  int seekCallback(uint64_t position);
  void handleSeekSignal(void *mp3dec_ex);
};

#endif // MP3STREAMDECODER_H