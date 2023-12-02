#include "FlacDecoder.h"
#include "AudioFormat.h"

#include <functional>
#include <iostream>
#include <vector>

FlacDecoder::FlacDecoder() { init(); }

void FlacDecoder::metadata_callback(const ::FLAC__StreamMetadata *pMetadata) {
  /* print some stats */
  if (pMetadata->type == FLAC__METADATA_TYPE_STREAMINFO) {
    streamInfo = pMetadata->data.stream_info;
    streamInfoReady = true;
  }
}

void FlacDecoder::error_callback(::FLAC__StreamDecoderErrorStatus status) {
  if (!out.expired()) {
    out.lock()->setStreamError(std::make_exception_ptr(
        std::runtime_error("Flac decoder error: " + std::to_string(status))));
  }
}

::FLAC__StreamDecoderWriteStatus
FlacDecoder::write_callback(const ::FLAC__Frame *frame,
                            const FLAC__int32 *const buffer[]) {
  const auto blockSize = frame->header.blocksize;
  const auto blockNum = frame->header.channels;
  if (blockNum != 2) {
    return FLAC__STREAM_DECODER_WRITE_STATUS_ABORT;
  }

  AudioFormat format = bitsToFormat(frame->header.bits_per_sample);
  data.resize(samplesToBytes(blockSize, format));
  convertToFormat(data.data(), buffer, blockSize, format);

  auto const sizeInBytes = data.size();
  std::stop_token token = decodingThread.get_stop_token();
  if (token.stop_requested()) {
    std::cout << "flac decoder thread token stop requested" << std::endl;
  }
  if (!out.expired()) {
    out.lock()->write(reinterpret_cast<uint8_t *>(data.data()), sizeInBytes,
                      token);
  }
  if (token.stop_requested()) {
    return FLAC__STREAM_DECODER_WRITE_STATUS_ABORT;
  }

  return FLAC__STREAM_DECODER_WRITE_STATUS_CONTINUE;
}
::FLAC__StreamDecoderReadStatus FlacDecoder::read_callback(FLAC__byte buffer[],
                                                           size_t *bytes) {
  std::stop_token token = decodingThread.get_stop_token();

  if (!in.expired()) {
    auto inNode = in.lock();
    if (inNode->eof()) {
      *bytes = 0;
      return FLAC__STREAM_DECODER_READ_STATUS_END_OF_STREAM;
    }
    if (inNode->error()) {
      *bytes = 0;
      if (!out.expired()) {
        out.lock()->setStreamError(inNode->error());
      }
      return FLAC__STREAM_DECODER_READ_STATUS_ABORT;
    }

    *bytes = inNode->read(buffer, *bytes, token);

    if (token.stop_requested() || inNode->error()) {
      *bytes = 0;
      return FLAC__STREAM_DECODER_READ_STATUS_ABORT;
    }
  }

  return FLAC__STREAM_DECODER_READ_STATUS_CONTINUE;
}

void FlacDecoder::start() {
  if (!decodingThread.joinable()) {
    decodingThread =
        std::jthread(std::bind_front(&FlacDecoder::thread_run, this));
  }
}

void FlacDecoder::thread_run(std::stop_token token) {
  try {
    if (get_state() == FLAC__STREAM_DECODER_UNINITIALIZED) {
      throw std::runtime_error("Flac decoder not initialized");
    }
    while (!token.stop_requested()) {
      State state = get_state();
      if (state == FLAC__STREAM_DECODER_END_OF_STREAM) {
        if (!out.expired()) {
          out.lock()->setStreamFinished();
        }
        break;
      }
      bool retval = process_single();
      if (!retval) {
        state = get_state();
        switch (state) {
        case FLAC__STREAM_DECODER_OGG_ERROR:
        case FLAC__STREAM_DECODER_SEEK_ERROR:
        case FLAC__STREAM_DECODER_ABORTED:
        case FLAC__STREAM_DECODER_MEMORY_ALLOCATION_ERROR:
          throw std::runtime_error("Flac decoder error: " +
                                   std::string(state.as_cstring()));
        }
      }
    }
  } catch (...) {
    auto exception = std::current_exception();
    if (!out.expired()) {
      out.lock()->setStreamError(exception);
    }
  }
}

void FlacDecoder::stop() {
  if (decodingThread.joinable() &&
      !decodingThread.get_stop_source().stop_requested()) {
    decodingThread.request_stop();
    decodingThread.join();
  }
}

void FlacDecoder::connectIn(std::shared_ptr<ProcessNode> inputNode) {
  in = inputNode;
}

void FlacDecoder::connectOut(std::shared_ptr<ProcessNode> outputNode) {
  out = outputNode;
}