#include "AudioPlayer.h"
#include "AlsaAudioEmitter.h"
#include "AudioGraphHttpStream.h"
#include "AudioStreamSwitcher.h"
#include "FlacStreamDecoder.h"
#include "StateMonitor.h"

namespace {
// These numbers can be reduced depending on audio bitness,
// sample rate and network throughput.
//
// 1.5MB = 1s for 192KHz / 24bit audio / stereo
const size_t FLAC_BUFFER_SIZE = 1536000;
// 750KB, 50% of flac buffer size
// approx. flac compression ratio is 50%
const size_t HTTP_BUFFER_SIZE = 768000;
} // namespace

struct StreamNodes {
  using NodeChain = std::list<std::shared_ptr<AudioGraphOutputNode>>;
  NodeChain nodeChain;
  const std::string url;

  StreamNodes(const std::string &url) : url(url) {
    nodeChain.emplace_back(
        std::make_shared<AudioGraphHttpStream>(url, HTTP_BUFFER_SIZE));

    auto decoder = std::make_shared<FlacStreamDecoder>(FLAC_BUFFER_SIZE);
    decoder->connectTo(nodeChain.back());
    nodeChain.emplace_back(std::move(decoder));
  }

  StreamNodes(StreamNodes &&other) : nodeChain(std::move(other.nodeChain)) {}

  ~StreamNodes() {
    for (NodeChain::reverse_iterator rit = nodeChain.rbegin();
         rit != nodeChain.rend(); ++rit) {
      auto inputNode = std::dynamic_pointer_cast<AudioGraphInputNode>(*rit);
      if (inputNode) {
        ++rit;
        if (rit != nodeChain.rend()) {
          inputNode->disconnect(*rit);
        }
      }
    }
  }
};

AudioPlayer::AudioPlayer(const std::string &audioDevice)
    : audioEmitter(std::make_shared<AlsaAudioEmitter>(audioDevice)),
      streamSwitcher(std::make_shared<AudioStreamSwitcher>()) {}

AudioPlayer::~AudioPlayer() { stop(); }

void AudioPlayer::play(const std::string &url) {
  for (auto it = streamNodesList.begin(); it != streamNodesList.end(); ++it) {
    if (it->url == url) {
      std::list<StreamNodes> newStreamNodesList;
      newStreamNodesList.emplace_back(std::move(*it));
      streamNodesList.erase(it);
      disconnectAllStreams();
      streamNodesList.swap(newStreamNodesList);
      return;
    }
  }

  StreamNodes newStream(url);
  streamSwitcher->connectTo(newStream.nodeChain.back());
  audioEmitter->connectTo(streamSwitcher);
  disconnectAllStreams();
  streamNodesList.emplace_back(std::move(newStream));
}

void AudioPlayer::playNext(const std::string &url) {
  std::cerr << "Adding new track to play next" << std::endl;
  StreamNodes newStream(url);
  streamSwitcher->connectTo(newStream.nodeChain.back());
  audioEmitter->connectTo(streamSwitcher);
  cleanUpFinishedStreams();
  streamNodesList.emplace_back(std::move(newStream));
}

void AudioPlayer::stop() {
  audioEmitter->disconnect(streamSwitcher);
  disconnectAllStreams();
}

void AudioPlayer::pause(bool paused) { audioEmitter->pause(paused); }

StreamState AudioPlayer::getState() { return audioEmitter->getState(); }

std::unique_ptr<StateMonitor> AudioPlayer::monitor() {
  return std::make_unique<StateMonitor>(audioEmitter.get());
}

void AudioPlayer::disconnectAllStreams() {
  for (auto &streamNodes : streamNodesList) {
    streamSwitcher->disconnect(streamNodes.nodeChain.back());
  }
  streamNodesList.clear();
}

void AudioPlayer::cleanUpFinishedStreams() {
  for (auto it = streamNodesList.begin(); it != streamNodesList.end();) {
    if (it->nodeChain.back()->getState().state ==
        AudioGraphNodeState::FINISHED) {
      it = streamNodesList.erase(it);
    } else {
      ++it;
    }
  }
}
