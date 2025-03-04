#define MINIMP3_IMPLEMENTATION
#define MINIMP3_ONLY_SIMD

#include "Mp3StreamDecoder.h"
#include "Log.h"
#include "PerfMon.h"

#include "minimp3/minimp3_ex.h"

Mp3StreamDecoder::Mp3StreamDecoder(size_t bufferSize)
    : buffer(bufferSize, std::bind(&Mp3StreamDecoder::onEmptyBuffer, this,
                                   std::placeholders::_1)) {}

Mp3StreamDecoder::~Mp3StreamDecoder() {
  if (decodingThread.joinable()) {
    decodingThread.request_stop();
    decodingThread.join();
    decodingThread = std::jthread();
  }
}

size_t Mp3StreamDecoder::read(void *data, size_t size) {
  return buffer.read(static_cast<uint8_t *>(data), size);
}

size_t Mp3StreamDecoder::waitForData(std::stop_token stopToken, size_t size) {
  auto availableSize = buffer.waitForData(stopToken, size);
  return availableSize;
}

size_t Mp3StreamDecoder::waitForDataFor(std::stop_token stopToken,
                                        std::chrono::milliseconds timeout,
                                        size_t size) {
  return buffer.waitForDataFor(stopToken, timeout, size);
}

size_t Mp3StreamDecoder::seekTo(size_t absolutePosition) { return size_t(); }

void Mp3StreamDecoder::connectTo(
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
        std::jthread(std::bind_front(&Mp3StreamDecoder::threadRun, this));
  }
}

void Mp3StreamDecoder::disconnect(
    std::shared_ptr<AudioGraphOutputNode> outputNode) {}

void Mp3StreamDecoder::threadRun(std::stop_token token) {

  mp3dec_ex_t mp3;
  mp3dec_io_t io = {
      .read =
          [](void *buf, size_t size, void *user_data) {
            return static_cast<Mp3StreamDecoder *>(user_data)->readCallback(
                buf, size);
          },
      .read_data = this,
      .seek =
          [](uint64_t position, void *user_data) {
            return static_cast<Mp3StreamDecoder *>(user_data)->seekCallback(
                position);
          },
      .seek_data = this};

  try {

    if (mp3dec_ex_open_cb(&mp3, &io, MP3D_SEEK_TO_SAMPLE) != 0) {
      throw std::runtime_error("Failed to open MP3 decoder");
    };

    constexpr size_t frameBufferSize = MINIMP3_MAX_SAMPLES_PER_FRAME;
    std::array<mp3d_sample_t, frameBufferSize> frameBuffer;

    while (!token.stop_requested()) {
      size_t size = mp3dec_ex_read(&mp3, frameBuffer.data(), frameBufferSize);
      spdlog::trace("Mp3StreamDecoder::threadRun size: {}", size);

      if (size == 0) {
        break;
      } else if (size == MP3D_E_MEMORY) {
        throw std::runtime_error("Failed to allocate memory for MP3 frame");
      }

      setState(StreamState{
          AudioGraphNodeState::STREAMING, 0,
          StreamInfo{
              .format =
                  StreamAudioFormat{
                      .sampleRate = static_cast<unsigned int>(mp3.info.hz),
                      .channels = static_cast<unsigned int>(mp3.info.channels),
                      .bitsPerSample = 16,
                      .sampleFormat = AudioSampleFormat::PCM16_LE},
              .streamType = StreamType::FRAMES,
              .streamSize = mp3.samples}});

      const auto sizeInBytes = size * sizeof(mp3d_sample_t);
      size_t bytesWritten = 0;
      while (bytesWritten < sizeInBytes) {
        spdlog::trace("Mp3StreamDecoder::threadRun wait for space {}",
                      sizeInBytes - bytesWritten);
        auto spaceAvailable = buffer.waitForSpace(token);

        if (spaceAvailable == 0 || token.stop_requested()) {
          break;
        }

        auto dataWritten = buffer.write(
            reinterpret_cast<uint8_t *>(frameBuffer.data()) + bytesWritten,
            sizeInBytes - bytesWritten);
        spdlog::trace("Mp3StreamDecoder::threadRun dataWritten: {}",
                      dataWritten);
        bytesWritten += dataWritten;
      }
    }
  } catch (std::exception &ex) {
    std::string message =
        std::string("Mp3 decoder thread exception: ") + ex.what();
    spdlog::error(message);
    setState({AudioGraphNodeState::ERROR, message});
  }

  mp3dec_ex_close(&mp3);
  buffer.setEof();
}

void Mp3StreamDecoder::onEmptyBuffer(Buffer<uint8_t> &buffer) {
  if (buffer.isEof() && getState().state != AudioGraphNodeState::ERROR) {
    setState(StreamState{AudioGraphNodeState::FINISHED});
  }
}

size_t Mp3StreamDecoder::readCallback(void *buf, size_t size) {
  auto stopToken = decodingThread.get_stop_token();
  auto dataAvailable = inputNode->waitForData(stopToken, size);

  if (dataAvailable == 0 || stopToken.stop_requested()) {
    return 0;
  }

  return inputNode->read(static_cast<uint8_t *>(buf), dataAvailable);
}

int Mp3StreamDecoder::seekCallback(uint64_t position) { return 0; }
