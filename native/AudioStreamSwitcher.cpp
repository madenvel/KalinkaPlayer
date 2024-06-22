#include "AudioStreamSwitcher.h"

AudioStreamSwitcher::AudioStreamSwitcher() {}

void AudioStreamSwitcher::connectTo(AudioGraphOutputNode *inputNode) {
  if (inputNode == nullptr) {
    throw std::runtime_error("Input node cannot be nullptr");
  }

  if (currentInputNode == nullptr) {
    currentInputNode = inputNode;
    setState(AudioGraphNodeState::STREAMING);
  } else {
    inputNodes.push_back(inputNode);
  }
}

void AudioStreamSwitcher::disconnect(AudioGraphOutputNode *inputNode) {
  if (inputNode == nullptr) {
    throw std::runtime_error("Input node cannot be nullptr");
  }

  if (inputNode == currentInputNode) {
    currentInputNode = nullptr;
    setState(inputNodes.empty() ? AudioGraphNodeState::FINISHED
                                : AudioGraphNodeState::SOURCE_CHANGED);
  } else {
    inputNodes.remove(inputNode);

    if (currentInputNode == nullptr) {
      setState(AudioGraphNodeState::FINISHED);
    }
  }
}

void AudioStreamSwitcher::switchToNextSource() {
  if (inputNodes.empty()) {
    setState(AudioGraphNodeState::FINISHED);
    return;
  }

  currentInputNode = inputNodes.front();
  inputNodes.pop_front();
  setState(AudioGraphNodeState::STREAMING);
}

AudioGraphNodeState AudioStreamSwitcher::getState() {
  if (currentInputNode == nullptr) {
    auto state = AudioGraphNode::getState();
    switchToNextSource();
    return state;
  }

  return currentInputNode->getState();
}

AudioGraphNodeState AudioStreamSwitcher::waitForStatus(
    std::stop_token stopToken, std::optional<AudioGraphNodeState> nextStatus) {
  auto state = AudioGraphNode::getState();
  if (state == AudioGraphNodeState::SOURCE_CHANGED) {
    switchToNextSource();
    return AudioGraphNodeState::SOURCE_CHANGED;
  }

  if (currentInputNode == nullptr) {
    if (nextStatus.has_value() && state == nextStatus.value()) {
      return state;
    }
    auto newState = AudioGraphNode::waitForStatus(
        stopToken, AudioGraphNodeState::STREAMING);

    if (newState == AudioGraphNodeState::STREAMING &&
        currentInputNode != nullptr) {
      if (nextStatus.has_value()) {
        return currentInputNode->waitForStatus(stopToken, nextStatus);
      }
      return currentInputNode->getState();
    }
  }

  return currentInputNode->waitForStatus(stopToken, nextStatus);
}

size_t AudioStreamSwitcher::read(void *data, size_t size) {
  if (currentInputNode == nullptr) {
    switchToNextSource();
  }

  if (currentInputNode == nullptr) {
    return 0;
  }

  size_t bytesRead = currentInputNode->read(data, size);

  if (currentInputNode->getState() == AudioGraphNodeState::FINISHED &&
      !inputNodes.empty()) {
    setState(AudioGraphNodeState::SOURCE_CHANGED);
    currentInputNode = nullptr;
  }

  return bytesRead;
}

size_t AudioStreamSwitcher::waitForData(std::stop_token stopToken,
                                        size_t size) {

  if (currentInputNode == nullptr) {
    switchToNextSource();
  }

  if (currentInputNode == nullptr) {
    return 0;
  }

  return currentInputNode->waitForData(stopToken, size);
}

StreamInfo AudioStreamSwitcher::getStreamInfo() {
  if (currentInputNode == nullptr) {
    return StreamInfo();
  }
  return currentInputNode->getStreamInfo();
}
