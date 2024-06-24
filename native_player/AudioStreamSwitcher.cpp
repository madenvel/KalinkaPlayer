#include "AudioStreamSwitcher.h"

namespace {
struct CombinedStopToken {
  std::stop_source stopSource;
  std::stop_callback<std::function<void()>> callback1;
  std::stop_callback<std::function<void()>> callback2;

  CombinedStopToken(const std::stop_token &token1,
                    const std::stop_token &token2)
      : stopSource(),
        callback1(token1, [this]() { stopSource.request_stop(); }),
        callback2(token2, [this]() { stopSource.request_stop(); }) {}

  std::stop_token get_token() const { return stopSource.get_token(); }
};

CombinedStopToken combineStopTokens(const std::stop_token &token1,
                                    const std::stop_token &token2) {

  return CombinedStopToken(token1, token2);
}
} // namespace

AudioStreamSwitcher::AudioStreamSwitcher() {}

void AudioStreamSwitcher::connectTo(
    std::shared_ptr<AudioGraphOutputNode> inputNode) {
  std::unique_lock lock(mutex);
  if (inputNode == nullptr) {
    throw std::runtime_error("Input node cannot be nullptr");
  }

  if (currentInputNode == nullptr) {
    currentInputNode = inputNode;
    setState(StreamState(AudioGraphNodeState::STREAMING));
  } else {
    inputNodes.push_back(inputNode);
  }
}

void AudioStreamSwitcher::disconnect(
    std::shared_ptr<AudioGraphOutputNode> inputNode) {
  std::unique_lock lock(mutex);

  if (inputNode == nullptr) {
    throw std::runtime_error("Input node cannot be nullptr");
  }

  if (inputNode == currentInputNode) {
    currentInputNode = nullptr;
    stopSource.request_stop();
    stopSource = std::stop_source();
    setState(inputNodes.empty()
                 ? StreamState(AudioGraphNodeState::FINISHED)
                 : StreamState(AudioGraphNodeState::SOURCE_CHANGED));
  } else {
    inputNodes.remove(inputNode);

    if (currentInputNode == nullptr) {
      setState(StreamState(AudioGraphNodeState::FINISHED));
    }
  }
}

void AudioStreamSwitcher::switchToNextSource() {
  if (inputNodes.empty()) {
    setState(StreamState(AudioGraphNodeState::FINISHED));
    return;
  }

  currentInputNode = inputNodes.front();
  inputNodes.pop_front();
  setState(StreamState(AudioGraphNodeState::STREAMING));
}

StreamState AudioStreamSwitcher::getState() {
  std::unique_lock lock(mutex);
  if (currentInputNode == nullptr) {
    auto state = AudioGraphNode::getState();
    switchToNextSource();
    return state;
  }

  return currentInputNode->getState();
}

StreamState AudioStreamSwitcher::waitForStatus(
    std::stop_token stopToken, std::optional<AudioGraphNodeState> nextStatus) {

  std::unique_lock lock(mutex);
  auto state = AudioGraphNode::getState();
  if (state.state == AudioGraphNodeState::SOURCE_CHANGED) {
    switchToNextSource();
    return StreamState(AudioGraphNodeState::SOURCE_CHANGED);
  }

  auto combinedStopToken = combineStopTokens(stopToken, stopSource.get_token());
  auto currentNode = currentInputNode;

  lock.unlock();

  if (currentNode == nullptr) {
    if (nextStatus.has_value() && state.state == nextStatus.value()) {
      return state;
    }

    auto newState =
        AudioGraphNode::waitForStatus(combinedStopToken.get_token(),
                                      AudioGraphNodeState::STREAMING)
            .state;

    if (combinedStopToken.get_token().stop_requested()) {
      return StreamState(AudioGraphNodeState::STOPPED);
    }

    lock.lock();
    currentNode = currentInputNode;
    lock.unlock();

    if (newState == AudioGraphNodeState::STREAMING && currentNode != nullptr) {
      if (nextStatus.has_value()) {
        return currentNode->waitForStatus(combinedStopToken.get_token(),
                                          nextStatus);
      }
      return currentNode->getState();
    }
  }

  return currentNode->waitForStatus(combinedStopToken.get_token(), nextStatus);
}

size_t AudioStreamSwitcher::read(void *data, size_t size) {
  std::unique_lock lock(mutex);
  if (currentInputNode == nullptr) {
    switchToNextSource();
  }

  if (currentInputNode == nullptr) {
    return 0;
  }

  size_t bytesRead = currentInputNode->read(data, size);

  if (currentInputNode->getState().state == AudioGraphNodeState::FINISHED &&
      !inputNodes.empty()) {
    setState(StreamState(AudioGraphNodeState::SOURCE_CHANGED));
    currentInputNode = nullptr;
  }

  return bytesRead;
}

size_t AudioStreamSwitcher::waitForData(std::stop_token stopToken,
                                        size_t size) {
  std::unique_lock lock(mutex);
  if (currentInputNode == nullptr) {
    switchToNextSource();
  }

  if (currentInputNode == nullptr) {
    return 0;
  }

  auto combinedStopToken = combineStopTokens(stopToken, stopSource.get_token());
  // Increase ref counter
  auto currentNode = currentInputNode;
  lock.unlock();
  return currentNode->waitForData(combinedStopToken.get_token(), size);
}
