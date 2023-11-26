#ifndef FLAC_DECODER_H
#define FLAC_DECODER_H

#include "ProcessNode.h"

#include <FLAC++/decoder.h>
#include <memory>
#include <thread>
#include <vector>

class FlacDecoder : public FLAC::Decoder::Stream, ProcessNode {
  std::jthread decodingThread;
  bool streamInfoReady = false;
  FLAC__StreamMetadata_StreamInfo streamInfo;
  std::weak_ptr<ProcessNode> in;
  std::weak_ptr<ProcessNode> out;
  std::vector<uint8_t> data;

public:
  FlacDecoder();

  const FLAC__StreamMetadata_StreamInfo &getStreamInfo() const {
    return streamInfo;
  }

  bool hasStreamInfo() const { return streamInfoReady; }

  virtual void connectIn(std::shared_ptr<ProcessNode> inputNode) override;
  virtual void connectOut(std::shared_ptr<ProcessNode> outputNode) override;

  virtual void start() override;
  virtual void stop() override;

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
  FlacDecoder(const FlacDecoder &);
  FlacDecoder &operator=(const FlacDecoder &);

  void thread_run(std::stop_token token);
};

#endif