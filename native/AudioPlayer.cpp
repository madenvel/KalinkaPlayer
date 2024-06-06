#include "AudioPlayer.h"
#include "AlsaPlayNode.h"
#include "BufferNode.h"
#include "FlacDecoder.h"

#include <memory>
#include <unistd.h>

using namespace std::placeholders;

AudioPlayer::AudioPlayer() {}

AudioPlayer::~AudioPlayer() {
  cbThreadPool.stop();
  ThreadPool::stop();
}

AudioPlayer::Context::Context(std::string url, ContextStateCallback stateCb,
                              std::shared_ptr<AlsaDevice> alsaDevice)
    : httpNode(std::make_shared<HttpRequestNode>(url)),
      decoder(std::make_shared<FlacDecoder>()), alsaDevice(alsaDevice),
      stateCb(stateCb), sm(std::make_shared<StateMachine>(stateCb)) {}

void AudioPlayer::Context::prepare(size_t level1Buffer, size_t level2Buffer,
                                   std::stop_token token) {
  decodedData = std::make_shared<BufferNode>(
      level2Buffer, BufferNode::BlockModeFlags::BlockOnWrite |
                        BufferNode::BlockModeFlags::BlockOnRead);
  // Buffer for downloaded flac data
  flacBuffer = std::make_shared<BufferNode>(
      level1Buffer, BufferNode::BlockModeFlags::BlockOnWrite |
                        BufferNode::BlockModeFlags::BlockOnRead);

  // Connect the nodes
  decoder->connectIn(flacBuffer);
  decoder->connectOut(decodedData);
  httpNode->connectOut(flacBuffer);
  sm->updateState(State::BUFFERING, 0);
  httpNode->start();
  decoder->process_until_end_of_metadata();

  if (sm->lastState().state == State::ERROR) {
    return;
  }

  if (!decoder->hasStreamInfo()) {
    sm->updateState(State::ERROR, 0, "Stream info is not available");
    return;
  }
  const auto &streamInfo = decoder->getStreamInfo();
  sm->updateState(
      State::READY, 0, std::nullopt,
      AudioInfo{streamInfo.sample_rate, streamInfo.channels,
                streamInfo.bits_per_sample, streamInfo.total_samples,
                streamInfo.total_samples * 1000 /
                    static_cast<unsigned long long>(streamInfo.sample_rate)});
  decoder->start();
  decodedData->waitForFull(token);
}

AudioInfo AudioPlayer::Context::getStreamInfo() {
  return AudioInfo{
      .sampleRate = 0, .channels = 0, .bitsPerSample = 0, .durationMs = 0};
}

void AudioPlayer::Context::play() {
  if (sm->lastState().state != State::READY) {
    return;
  }
  alsaPlay = std::make_shared<AlsaPlayNode>(alsaDevice, sm);
  alsaPlay->connectIn(decodedData);
  alsaPlay->start();
}

void AudioPlayer::Context::pause(bool paused) {
  if (alsaPlay) {
    alsaPlay->pause(paused);
  }
}

int AudioPlayer::prepare(const char *url, size_t level1BufferSize,
                         size_t level2BufferSize) {
  int contextId = ++newContextId;
  std::string urlCopy(url);
  auto task = [this, contextId, urlCopy, level1BufferSize,
               level2BufferSize](std::stop_token token) {
    contexts[contextId] = std::make_unique<Context>(
        urlCopy,
        std::bind(&AudioPlayer::onStateChangeCb_internal, this, contextId, _1),
        alsaDevice);
    auto &context = contexts[contextId];
    context->prepare(level1BufferSize, level2BufferSize, token);
  };

  enqueue(task);

  return contextId;
}

void AudioPlayer::play(int contextId) {
  auto task = [this, contextId](std::stop_token) {
    if (contextId == currentContextId) {
      return;
    }
    if (contexts.count(contextId)) {
      contexts.erase(currentContextId);
      contexts[contextId]->play();
      currentContextId = contextId;
    }
  };

  enqueue(task);
}

void AudioPlayer::stop() {
  auto task = [this](std::stop_token) { contexts.erase(currentContextId); };
  enqueue(task);
}

void AudioPlayer::pause(bool paused) {
  auto task = [this, paused](std::stop_token) {
    if (contexts.count(currentContextId)) {
      contexts[currentContextId]->pause(paused);
    }
  };
  enqueue(task);
}
void AudioPlayer::seek(int time) {}

void AudioPlayer::removeContext(int contextId) {
  auto task = [this, contextId](std::stop_token) { contexts.erase(contextId); };
  enqueue(task);
}

void AudioPlayer::onStateChangeCb_internal(int contextId,
                                           const StateInfo state) {
  if (stateCb == nullptr) {
    return;
  }

  auto task = [this, contextId, state](std::stop_token) {
    stateCb(contextId, state);
  };
  cbThreadPool.enqueue(task);
}

void AudioPlayer::setStateCallback(StateCallback cb) {
  auto task = [this, cb](std::stop_token) { stateCb = cb; };
  enqueue(task);
}
