#include "AudioStreamSwitcher.h"
#include "Log.h"
#include "Utils.h"

AudioStreamSwitcher::AudioStreamSwitcher() {}

void AudioStreamSwitcher::connectTo(
    std::shared_ptr<AudioGraphOutputNode> inputNode) {
  if (inputNode == nullptr) {
    throw std::runtime_error("Input node cannot be nullptr");
  }

  std::lock_guard lock(mutex);
  inputNodes.push_back(inputNode);

  if (currentInputNode == nullptr) {
    stopSource.request_stop();
    stopSource = std::stop_source();
    setState(StreamState(AudioGraphNodeState::SOURCE_CHANGED));
  }
}

void AudioStreamSwitcher::disconnect(
    std::shared_ptr<AudioGraphOutputNode> inputNode) {

  if (inputNode == nullptr) {
    throw std::runtime_error("Input node cannot be nullptr");
  }
  std::unique_lock lock(mutex);
  if (inputNode == currentInputNode) {
    currentInputNode->removeStateChangeCallback(stateCallbackId);
    currentInputNode = nullptr;
    stopSource.request_stop();
    stopSource = std::stop_source();
    if (inputNodes.empty()) {
      spdlog::debug("Removed current node, no more input nodes available, "
                    "setting state to FINISHED");
    }
    setState(inputNodes.empty()
                 ? StreamState(AudioGraphNodeState::FINISHED)
                 : StreamState(AudioGraphNodeState::SOURCE_CHANGED));
  } else {
    inputNodes.remove(inputNode);
    if (currentInputNode == nullptr && inputNodes.empty()) {
      spdlog::debug("Removed other node, no more input nodes available, "
                    "setting state to FINISHED");
      setState(StreamState(AudioGraphNodeState::FINISHED));
    }
  }
}

void AudioStreamSwitcher::switchToNextSource() {
  std::unique_lock lock(mutex);
  if (currentInputNode != nullptr ||
      getState().state != AudioGraphNodeState::SOURCE_CHANGED) {
    spdlog::warn("Not in SOURCE_CHANGED state or currentInputNode is not null");
    return;
  }
  auto inputNode = inputNodes.front();
  currentInputNode = inputNode;
  inputNodes.pop_front();
  lock.unlock();

  auto id = inputNode->onStateChange([this](AudioGraphNode *node,
                                            StreamState state) -> bool {
    std::lock_guard lock(mutex);
    if (node != currentInputNode.get()) {
      return false;
    }
    if (state.state == AudioGraphNodeState::FINISHED && !inputNodes.empty()) {
      currentInputNode = nullptr;
      setState(StreamState(AudioGraphNodeState::SOURCE_CHANGED));
      return false;
    }

    // Restamp the state to avoid time jumping backwards
    state.timestamp = getTimestampNs();
    setState(state);

    return true;
  });
  lock.lock();
  if (currentInputNode == inputNode) {
    stateCallbackId = id;
  }
}

size_t AudioStreamSwitcher::read(void *data, size_t size) {
  std::unique_lock lock(mutex);
  auto currentInput = currentInputNode;
  lock.unlock();
  if (currentInput == nullptr) {
    return 0;
  }

  return currentInput->read(data, size);
}

size_t AudioStreamSwitcher::waitForData(std::stop_token stopToken,
                                        size_t size) {
  std::unique_lock lock(mutex);
  auto currentNode = currentInputNode;
  lock.unlock();
  if (currentNode == nullptr) {
    return 0;
  }

  auto combinedToken = combineStopTokens(stopToken, stopSource.get_token());
  return currentNode->waitForData(combinedToken.get_token(), size);
}

size_t AudioStreamSwitcher::waitForDataFor(std::stop_token stopToken,
                                           std::chrono::milliseconds timeout,
                                           size_t size) {
  std::unique_lock lock(mutex);
  auto currentNode = currentInputNode;
  lock.unlock();
  if (currentNode == nullptr) {
    return 0;
  }

  auto combinedToken = combineStopTokens(stopToken, stopSource.get_token());
  return currentNode->waitForDataFor(combinedToken.get_token(), timeout, size);
}

void AudioStreamSwitcher::acceptSourceChange() { switchToNextSource(); }

size_t AudioStreamSwitcher::seekTo(size_t absolutePosition) {
  std::unique_lock lock(mutex);
  auto currentNode = currentInputNode;
  lock.unlock();

  if (currentNode == nullptr) {
    return 0;
  }

  return currentNode->seekTo(absolutePosition);
}