#ifndef FLAC_STREAM_DECODER_H
#define FLAC_STREAM_DECODER_H

#include "AudioGraphNode.h"

#include "Buffer.h"
#include <FLAC++/decoder.h>
#include <memory>
#include <thread>
#include <vector>

class FlacStreamDecoder : public AudioGraphOutputNode,
                          public AudioGraphInputNode,
                          public FLAC::Decoder::Stream {
public:
  FlacStreamDecoder(size_t bufferSize);

  virtual void
  connectTo(std::shared_ptr<AudioGraphOutputNode> inputNode) override;
  virtual void
  disconnect(std::shared_ptr<AudioGraphOutputNode> inputNode) override;

  virtual size_t read(void *data, size_t size) override;
  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) override;

  virtual size_t waitForDataFor(std::stop_token stopToken,
                                std::chrono::milliseconds timeout,
                                size_t size) override;

  virtual size_t seekTo(size_t absolutePosition) override;

  virtual ~FlacStreamDecoder();

protected:
  virtual ::FLAC__StreamDecoderWriteStatus
  write_callback(const ::FLAC__Frame *frame,
                 const FLAC__int32 *const buffer[]) override;
  virtual ::FLAC__StreamDecoderReadStatus read_callback(FLAC__byte buffer[],
                                                        size_t *bytes) override;

  virtual void error_callback(::FLAC__StreamDecoderErrorStatus status) override;

  virtual void
  metadata_callback(const ::FLAC__StreamMetadata *metadata) override;

private:
  std::jthread decodingThread;
  std::optional<FLAC__StreamMetadata_StreamInfo> flacStreamInfo;
  std::vector<uint8_t> data;
  DequeBuffer<uint8_t> buffer;

  std::shared_ptr<AudioGraphOutputNode> inputNode;

  void thread_run(std::stop_token token);
  void onEmptyBuffer(DequeBuffer<uint8_t> &buffer);
  void throwOnFlacError(bool retval);
  void setStreamingState();
};

#endif