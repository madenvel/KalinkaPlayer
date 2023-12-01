#include "AudioPlayer.h"
#include "AlsaPlayNode.h"
#include "BufferNode.h"
#include "FlacDecoder.h"

#include <memory>
#include <unistd.h>

using namespace std::placeholders;

AudioPlayer::AudioPlayer()
    : ThreadPool(1), stateCb([](int, State, State) {}), progressCb(nullptr) {}

AudioPlayer::~AudioPlayer() {
  cbThreadPool.stop();
  ThreadPool::stop();
}

AudioPlayer::Context::Context(std::string url, ContextStateCallback stateCb,
                              ProgressUpdateCallback progressCb)
    : httpNode(std::make_shared<HttpRequestNode>(url)),
      decoder(std::make_shared<FlacDecoder>()), stateCb(stateCb),
      sm(std::make_shared<StateMachine>(stateCb)), progressCb(progressCb) {}

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
  httpNode->start();
  sm->updateState(State::BUFFERING);
  decoder->process_until_end_of_metadata();

  if (!decoder->hasStreamInfo()) {
    std::cerr << "Stream info is not available" << std::endl;
    sm->updateState(State::ERROR);
    return;
  }
  sm->updateState(State::READY);
  decoder->start();
  std::cerr << "Waiting for full buffer" << std::endl;
  decodedData->waitForFull(token);
}

void AudioPlayer::Context::play() {
  if (sm->state() != State::READY) {
    return;
  }
  alsaPlay = std::make_shared<AlsaPlayNode>(
      decoder->getStreamInfo().sample_rate,
      decoder->getStreamInfo().bits_per_sample,
      decoder->getStreamInfo().total_samples, sm, progressCb);
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
        std::bind(&AudioPlayer::onStateChangeCb_internal, this, contextId, _1,
                  _2),
        std::bind(&AudioPlayer::onProgressUpdateCb_internal, this, _1));
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

void AudioPlayer::onStateChangeCb_internal(int contextId, State oldState,
                                           State newState) {
  auto task = [this, contextId, oldState, newState](std::stop_token) {
    stateCb(contextId, oldState, newState);
  };
  cbThreadPool.enqueue(task);
}

void AudioPlayer::onProgressUpdateCb_internal(float progress) {
  if (progressCb == nullptr) {
    return;
  }

  auto task = [this, progress](std::stop_token) { progressCb(progress); };
  cbThreadPool.enqueue(task);
}

void AudioPlayer::setStateCallback(StateCallback cb) {
  auto task = [this, cb](std::stop_token) { stateCb = cb; };
  enqueue(task);
}

void AudioPlayer::setProgressUpdateCallback(ProgressUpdateCallback cb) {
  auto task = [this, cb](std::stop_token) { progressCb = cb; };
  enqueue(task);
}