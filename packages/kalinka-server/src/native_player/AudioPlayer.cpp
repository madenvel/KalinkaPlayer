#include "AudioPlayer.h"
#include "AlsaAudioEmitter.h"
#include "AudioGraphHttpStream.h"
#include "AudioStreamSwitcher.h"
#include "Config.h"
#include "FileInputNode.h"
#include "FlacStreamDecoder.h"
#include "Log.h"
#include "Mp3StreamDecoder.h"
#include "PerfMon.h"
#include "StateMonitor.h"

namespace {
// These numbers can be reduced depending on audio bitness,
// sample rate and network throughput.
//
// 1.5MB = 1s for 192KHz / 24bit audio / stereo
const size_t FLAC_BUFFER_SIZE = 1536000;
const size_t MPEG_BUFFER_SIZE = 768000;
// 750KB, 50% of flac buffer size
// approx. flac compression ratio is 50%
const size_t HTTP_BUFFER_SIZE = 768000;

const size_t CHUNK_SIZE = HTTP_BUFFER_SIZE / 2;

bool isInvalidState(AudioGraphNodeState state) {
  return state == AudioGraphNodeState::FINISHED ||
         state == AudioGraphNodeState::STOPPED ||
         state == AudioGraphNodeState::ERROR;
}
} // namespace

struct StreamNodes {
  using NodeChain = std::list<std::shared_ptr<AudioGraphOutputNode>>;
  NodeChain nodeChain;
  const std::string url;
  const StreamId id;

  StreamNodes(StreamId id, const std::string &url, const Config &config,
              const AudioFormat format)
      : url(url), id(id) {
    if (url.substr(0, 7) == "file://") {
      // Use FileInputNode for local files
      std::string filePath = url.substr(7);
      spdlog::debug("Creating FileInputNode for local file: {}", filePath);
      nodeChain.emplace_back(std::make_shared<FileInputNode>(filePath));
    } else {
      // Use AudioGraphHttpStream for network streams
      spdlog::debug("Creating AudioGraphHttpStream for URL: {}", url);
      nodeChain.emplace_back(std::make_shared<AudioGraphHttpStream>(
          url, value_or(config, "input.http.buffer_size", HTTP_BUFFER_SIZE),
          value_or(config, "input.http.chunk_size", CHUNK_SIZE)));
    }

    auto decoder = connectDecoder(nodeChain.back(), config, format);
    if (nodeChain.back() != decoder) {
      nodeChain.emplace_back(std::move(decoder));
    }
  }

  std::shared_ptr<AudioGraphOutputNode>
  connectDecoder(std::shared_ptr<AudioGraphOutputNode> outputNode,
                 const Config &config, const AudioFormat format) {
    switch (format) {
    case AudioFormat::FormatFlac: {
      auto decoder = std::make_shared<FlacStreamDecoder>(
          value_or(config, "decoder.flac.buffer_size", FLAC_BUFFER_SIZE));
      decoder->connectTo(outputNode);
      return decoder;
    }
    case AudioFormat::FormatMpeg: {
      auto decoder = std::make_shared<Mp3StreamDecoder>(
          value_or(config, "decoder.mpeg.buffer_size", MPEG_BUFFER_SIZE));
      decoder->connectTo(outputNode);
      return decoder;
    }
    default:
      spdlog::warn(
          "Undefined format {}, assuming raw and not attaching any decoder",
          static_cast<int>(format));
      return outputNode;
    }
  }

  StreamNodes(StreamNodes &&other)
      : nodeChain(std::move(other.nodeChain)), url(std::move(other.url)),
        id(other.id) {}

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

AudioPlayer::AudioPlayer(const Config &config)
    : config(config), audioEmitter(std::make_shared<AlsaAudioEmitter>(config)),
      streamSwitcher(std::make_shared<AudioStreamSwitcher>()) {
  initLogger(value_or(config, "server.log_level", std::string("debug")));
  perfmon_print_periodically(5);
}

AudioPlayer::~AudioPlayer() { stop(); }

StreamId AudioPlayer::append(const std::string &url, const AudioFormat format) {
  cleanUpFinishedStreams();
  StreamId id = nextStreamId++;
  spdlog::debug("Appending stream id={} url={}", id, url);
  StreamNodes newStream(id, url, config, format);
  streamSwitcher->connectTo(newStream.nodeChain.back());
  audioEmitter->connectTo(streamSwitcher);
  streamNodesList.emplace_back(std::move(newStream));
  return id;
}

void AudioPlayer::remove(StreamId id) {
  auto stream = std::find_if(streamNodesList.begin(), streamNodesList.end(),
                             [id](const StreamNodes &s) { return s.id == id; });
  if (stream != streamNodesList.end()) {
    streamSwitcher->disconnect(stream->nodeChain.back());
    streamNodesList.erase(stream);
  } else {
    spdlog::warn("Stream id={} not found", id);
  }
}

void AudioPlayer::clearAll() {
  disconnectAllStreams();
  // audioEmitter stays connected to streamSwitcher — auto-start on next append()
}

void AudioPlayer::stop() {
  audioEmitter->disconnect(streamSwitcher);
  disconnectAllStreams();
}

void AudioPlayer::pause()  { audioEmitter->pause(true);  }
void AudioPlayer::resume() { audioEmitter->pause(false); }

size_t AudioPlayer::seek(size_t positionMs) {
  return audioEmitter->seek(positionMs);
}

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
    if (isInvalidState(it->nodeChain.back()->getState().state)) {
      it = streamNodesList.erase(it);
    } else {
      ++it;
    }
  }
}
