#include "FlacStreamDecoder.h"
#include "AudioFormat.h"

#include <functional>
#include <iostream>
#include <vector>

FlacStreamDecoder::FlacStreamDecoder(size_t bufferSize)
    : buffer(bufferSize, std::bind(&FlacStreamDecoder::onEmptyBuffer, this,
                                   std::placeholders::_1)) {
  init();
}

void FlacStreamDecoder::connectTo(
    std::shared_ptr<AudioGraphOutputNode> inputNode) {
  if (inputNode == nullptr) {
    throw std::runtime_error("Input node is null");
  }

  if (this->inputNode != nullptr) {
    throw std::runtime_error("Input node is already connected");
  }

  this->inputNode = inputNode;

  if (!decodingThread.joinable()) {
    setState(StreamState(AudioGraphNodeState::PREPARING));
    decodingThread =
        std::jthread(std::bind_front(&FlacStreamDecoder::thread_run, this));
  }
}

void FlacStreamDecoder::disconnect(
    std::shared_ptr<AudioGraphOutputNode> inputNode) {
  if (inputNode != this->inputNode) {
    return;
  }

  if (decodingThread.joinable()) {
    decodingThread.request_stop();
    setState(StreamState(AudioGraphNodeState::STOPPED));
    decodingThread.join();
  }

  this->inputNode = nullptr;
}

::FLAC__StreamDecoderWriteStatus
FlacStreamDecoder::write_callback(const ::FLAC__Frame *frame,
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
  size_t bytesWritten = 0;
  while (bytesWritten < sizeInBytes) {
    auto spaceAvailable =
        this->buffer.waitForSpace(decodingThread.get_stop_token());

    if (decodingThread.get_stop_token().stop_requested()) {
      return FLAC__STREAM_DECODER_WRITE_STATUS_ABORT;
    }

    bytesWritten += this->buffer.write(
        reinterpret_cast<uint8_t *>(data.data()) + bytesWritten,
        std::min(spaceAvailable, sizeInBytes - bytesWritten));
  }

  return FLAC__STREAM_DECODER_WRITE_STATUS_CONTINUE;
}
::FLAC__StreamDecoderReadStatus
FlacStreamDecoder::read_callback(FLAC__byte buffer[], size_t *bytes) {
  std::stop_token token = decodingThread.get_stop_token();
  *bytes = inputNode->read(buffer, *bytes);
  auto inputNodeStatus = inputNode->getState().state;
  if (token.stop_requested()) {
    return FLAC__STREAM_DECODER_READ_STATUS_ABORT;
  }
  if (inputNodeStatus == AudioGraphNodeState::FINISHED && *bytes == 0) {
    return FLAC__STREAM_DECODER_READ_STATUS_END_OF_STREAM;
  }

  return FLAC__STREAM_DECODER_READ_STATUS_CONTINUE;
}

void FlacStreamDecoder::error_callback(
    ::FLAC__StreamDecoderErrorStatus status) {
  setState({AudioGraphNodeState::ERROR,
            std::string("Flac decoder error: ") +
                FLAC__StreamDecoderErrorStatusString[status]});
}

void FlacStreamDecoder::metadata_callback(
    const ::FLAC__StreamMetadata *metadata) {
  if (metadata->type == FLAC__METADATA_TYPE_STREAMINFO) {
    flacStreamInfo = metadata->data.stream_info;
  }
}

void FlacStreamDecoder::throwOnFlacError(bool retval) {
  if (!retval) {
    State state = get_state();
    switch (state) {
    case FLAC__STREAM_DECODER_OGG_ERROR:
    case FLAC__STREAM_DECODER_SEEK_ERROR:
    case FLAC__STREAM_DECODER_MEMORY_ALLOCATION_ERROR:
      throw std::runtime_error("Flac decoder error: " +
                               std::string(state.as_cstring()));
    default:
      break;
    }
  }
}

void FlacStreamDecoder::setStreamingState() {
  if (!flacStreamInfo.has_value()) {
    throw std::runtime_error("Stream info not available");
  }

  StreamInfo streamInfo = {
      .format =
          StreamAudioFormat{.sampleRate = flacStreamInfo.value().sample_rate,
                            .channels = flacStreamInfo.value().channels,
                            .bitsPerSample =
                                flacStreamInfo.value().bits_per_sample},
      .totalSamples = flacStreamInfo.value().total_samples,
      .durationMs = static_cast<unsigned int>(
          flacStreamInfo.value().total_samples * 1000 /
          static_cast<unsigned long long>(flacStreamInfo.value().sample_rate))};

  uint64_t decodePosition = 0;
  if (!get_decode_position(&decodePosition)) {
    decodePosition = 0;
  }

  setState({AudioGraphNodeState::STREAMING, static_cast<long>(decodePosition),
            streamInfo});
}

void FlacStreamDecoder::thread_run(std::stop_token token) {
  try {
    if (get_state() == FLAC__STREAM_DECODER_UNINITIALIZED) {
      throw std::runtime_error("Flac decoder not initialized");
    }
    if (token.stop_requested()) {
      return;
    }

    bool retval = process_until_end_of_metadata();
    throwOnFlacError(retval);

    bool streamingStateSet = false;

    while (!token.stop_requested()) {
      State state = get_state();
      if (state == FLAC__STREAM_DECODER_END_OF_STREAM ||
          state == FLAC__STREAM_DECODER_ABORTED) {
        break;
      }
      retval = process_single();
      throwOnFlacError(retval);
      if (!streamingStateSet) {
        setStreamingState();
        streamingStateSet = true;
      }
    }
  } catch (std::exception &ex) {
    std::string message =
        std::string("Flac decoder thread exception: ") + ex.what();
    std::cerr << message << std::endl;
    if (getState().state != AudioGraphNodeState::ERROR) {
      setState({AudioGraphNodeState::ERROR, message});
    }
  }
  buffer.setEof();
}

void FlacStreamDecoder::onEmptyBuffer(DequeBuffer<uint8_t> &buffer) {
  if (buffer.isEof() && getState().state != AudioGraphNodeState::ERROR) {
    uint64_t decodePosition = 0;
    if (!get_decode_position(&decodePosition)) {
      decodePosition = 0;
    }
    setState(
        {AudioGraphNodeState::FINISHED, static_cast<long>(decodePosition)});
  }
}

FlacStreamDecoder::~FlacStreamDecoder() {
  if (decodingThread.joinable()) {
    decodingThread.request_stop();
    decodingThread.join();
    decodingThread = std::jthread();
  }
}

size_t FlacStreamDecoder::read(void *data, size_t size) {
  return buffer.read(static_cast<uint8_t *>(data), size);
}
size_t FlacStreamDecoder::waitForData(std::stop_token stopToken, size_t size) {
  return buffer.waitForData(stopToken, size);
}
