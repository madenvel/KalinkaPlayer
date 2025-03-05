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
  auto combinedToken = combineStopTokens(stopToken, seekSignal.getStopToken());
  auto availableSize = buffer.waitForData(combinedToken.get_token(), size);
  return availableSize;
}

size_t Mp3StreamDecoder::waitForDataFor(std::stop_token stopToken,
                                        std::chrono::milliseconds timeout,
                                        size_t size) {
  auto combinedToken = combineStopTokens(stopToken, seekSignal.getStopToken());
  return buffer.waitForDataFor(combinedToken.get_token(), timeout, size);
}

size_t Mp3StreamDecoder::seekTo(size_t absolutePosition) {
  if (getState().state == AudioGraphNodeState::ERROR || seekSignal.getValue()) {
    return -1;
  }
  auto token = decodingThread.get_stop_token();

  if (!initCompleteSignal.waitValue(token).value_or(false)) {
    return -1;
  }
  spdlog::trace("Mp3StreamDecoder::seekTo({})", absolutePosition);
  seekSignal.sendValue(absolutePosition);
  auto retVal = seekSignal.getResponse(token);
  spdlog::trace("Mp3StreamDecoder::seekTo({}) -> {}", absolutePosition, retVal);
  return retVal;
}

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
    std::shared_ptr<AudioGraphOutputNode> outputNode) {
  if (inputNode != this->inputNode) {
    return;
  }

  if (decodingThread.joinable()) {
    decodingThread.request_stop();
    decodingThread.join();
  }

  this->inputNode = nullptr;
}

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

    int ret = mp3dec_ex_open_cb(&mp3, &io, MP3D_SEEK_TO_SAMPLE);
    spdlog::trace("mp3dec_ex_open_cb ret: {}", ret);

    if (ret != 0) {
      initCompleteSignal.sendValue(false);
      spdlog::error("Failed to open MP3 decoder: {}", ret);
      throw std::runtime_error("Failed to open MP3 decoder");
    };

    initCompleteSignal.sendValue(true);

    constexpr size_t frameBufferSize = MINIMP3_MAX_SAMPLES_PER_FRAME;
    std::array<mp3d_sample_t, frameBufferSize> frameBuffer;

    while (!token.stop_requested()) {
      while (!token.stop_requested()) {
        if (seekSignal.getValue().has_value()) {
          handleSeekSignal(&mp3);
          continue;
        }
        size_t size = mp3dec_ex_read(&mp3, frameBuffer.data(), frameBufferSize);

        if (seekSignal.getValue().has_value()) {
          continue;
        }

        if (size == 0) {
          buffer.setEof();
          break;
        } else if (size == MP3D_E_MEMORY) {
          throw std::runtime_error("Failed to allocate memory for MP3 frame");
        }

        setState(StreamState{
            AudioGraphNodeState::STREAMING, currentPos,
            StreamInfo{
                .format =
                    StreamAudioFormat{
                        .sampleRate = static_cast<unsigned int>(mp3.info.hz),
                        .channels =
                            static_cast<unsigned int>(mp3.info.channels),
                        .bitsPerSample = 16,
                        .sampleFormat = AudioSampleFormat::PCM16_LE},
                .streamType = StreamType::FRAMES,
                .streamSize = mp3.samples}});

        const auto sizeInBytes = size * sizeof(mp3d_sample_t);
        size_t bytesWritten = 0;
        auto combinedToken =
            combineStopTokens(token, seekSignal.getStopToken());
        while (bytesWritten < sizeInBytes) {
          spdlog::trace("Mp3StreamDecoder::threadRun wait for space {}",
                        sizeInBytes - bytesWritten);
          auto spaceAvailable = buffer.waitForSpace(combinedToken.get_token());

          if (spaceAvailable == 0 ||
              combinedToken.get_token().stop_requested()) {
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
      seekSignal.waitValue(token);
    }
  } catch (std::exception &ex) {
    std::string message =
        std::string("Mp3 decoder thread exception: ") + ex.what();
    spdlog::error(message);
    setState({AudioGraphNodeState::ERROR, message});
  }

  mp3dec_ex_close(&mp3);
  buffer.setEof();
  if (seekSignal.getValue().has_value()) {
    seekSignal.respond(-1);
  }
  initCompleteSignal.respond(false);
  setState(StreamState{AudioGraphNodeState::STOPPED});
  spdlog::trace("Mp3StreamDecoder::threadRun finished");
}

void Mp3StreamDecoder::onEmptyBuffer(Buffer<uint8_t> &buffer) {
  if (buffer.isEof() && getState().state != AudioGraphNodeState::ERROR) {
    setState(StreamState{AudioGraphNodeState::FINISHED});
  }
}

size_t Mp3StreamDecoder::readCallback(void *buf, size_t size) {
  spdlog::trace("Mp3StreamDecoder::readCallback({})", size);

  auto stopToken = decodingThread.get_stop_token();
  auto combinedToken = combineStopTokens(stopToken, seekSignal.getStopToken());
  auto token = initCompleteSignal.getValue().value_or(false)
                   ? combinedToken.get_token()
                   : stopToken;
  auto dataAvailable = inputNode->waitForData(token, size);
  spdlog::trace("Mp3StreamDecoder::readCallback({}) -> {}", size,
                dataAvailable);
  if (dataAvailable == 0 || token.stop_requested()) {
    spdlog::trace("Mp3StreamDecoder::readCallback() returning");
    return 0;
  }

  auto actuallyRead =
      inputNode->read(static_cast<uint8_t *>(buf), dataAvailable);
  spdlog::trace("Mp3StreamDecoder::readCallback({}) -> {}", size, actuallyRead);
  return actuallyRead;
}

int Mp3StreamDecoder::seekCallback(uint64_t position) {
  spdlog::trace("Mp3StreamDecoder::seekCallback({})", position);
  auto actualPosition = inputNode->seekTo(position);
  spdlog::trace("Mp3StreamDecoder::seekCallback({}) -> {}", position,
                actualPosition);
  return position - actualPosition;
}

void Mp3StreamDecoder::handleSeekSignal(void *mp3dec_ex) {
  mp3dec_ex_t *mp3 = static_cast<mp3dec_ex_t *>(mp3dec_ex);
  auto seekPos = seekSignal.getValue().value();

  spdlog::trace("Mp3StreamDecoder::handleSeekSignal seek to {}", seekPos);
  setState(StreamState{AudioGraphNodeState::PREPARING});

  buffer.clear();
  if (seekPos >= mp3->samples) {
    spdlog::trace(
        "Mp3StreamDecoder::handleSeekSignal seekPos is too large {} -> {}",
        seekPos, mp3->samples);
    seekPos = mp3->samples;
    seekSignal.respond(seekPos);
    return;
  }

  if (!mp3->indexes_built) {
    spdlog::trace("Index is not built, seeking to {}", seekPos);
  }

  seekSignal.respond(seekPos);
  buffer.resetEof();
  int ret = mp3dec_ex_seek(mp3, seekPos);
  if (ret != 0) {
    spdlog::error("MP3 decoder seek failure: {}", ret);
    throw std::runtime_error("MP3 decoder seek failure");
  }

  currentPos = seekPos;
  spdlog::trace("Mp3StreamDecoder::handleSeekSignal seek to {} -> {}", seekPos,
                currentPos);
}
