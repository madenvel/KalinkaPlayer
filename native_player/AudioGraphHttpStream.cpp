#include "AudioGraphHttpStream.h"

#include <curlpp/Info.hpp>
#include <curlpp/Options.hpp>
#include <curlpp/Types.hpp>
#include <curlpp/cURLpp.hpp>

#include <boost/algorithm/string.hpp>
#include <functional>

#include "Log.h"

AudioGraphHttpStream::AudioGraphHttpStream(const std::string &url,
                                           size_t bufferSize, size_t chunkSize)
    : url(url),
      buffer(std::max(bufferSize, static_cast<size_t>(CURL_MAX_WRITE_SIZE)),
             std::bind(&AudioGraphHttpStream::emptyBufferCallback, this,
                       std::placeholders::_1)),
      chunkSize(chunkSize) {
  readerThread =
      std::jthread(std::bind_front(&AudioGraphHttpStream::reader, this));
}

AudioGraphHttpStream::~AudioGraphHttpStream() {
  if (readerThread.joinable()) {
    readerThread.request_stop();
    readerThread.join();
    readerThread = std::jthread();
  }
}

size_t AudioGraphHttpStream::WriteCallback(void *contents, size_t size,
                                           size_t nmemb) {
  size_t sizeWritten = 0;
  size_t totalSize = size * nmemb;
  if (setStreamingState) {
    setState(StreamState(AudioGraphNodeState::STREAMING, offset,
                         StreamInfo{.totalSamples = contentLength}));
    setStreamingState = false;
  }
  auto combinedStopToken = combineStopTokens(seekRequestSignal.getStopToken(),
                                             readerThread.get_stop_token());
  while (sizeWritten < totalSize) {
    auto spaceAvailable = buffer.waitForSpace(combinedStopToken.get_token());
    if (combinedStopToken.get_token().stop_requested()) {
      break;
    }
    auto writtenChunkSize =
        buffer.write(static_cast<uint8_t *>(contents) + sizeWritten,
                     std::min(totalSize - sizeWritten, spaceAvailable));
    sizeWritten += writtenChunkSize;
    offset += writtenChunkSize;
  }

  return sizeWritten;
}

void AudioGraphHttpStream::emptyBufferCallback(DequeBuffer<uint8_t> &buffer) {
  if (buffer.isEof() && getState().state != AudioGraphNodeState::ERROR) {
    setState(StreamState(AudioGraphNodeState::FINISHED));
  }
}

size_t AudioGraphHttpStream::headerCallback(char *buffer, size_t size,
                                            size_t nitems) {
  size_t totalSize = size * nitems;
  std::string header(buffer, totalSize);

  size_t separator = header.find(": ");
  if (separator != std::string::npos) {
    std::string key = header.substr(0, separator);
    std::string value = header.substr(separator + 2);
    boost::algorithm::to_lower(key);
    boost::algorithm::to_lower(value);
    boost::algorithm::trim(key);
    boost::algorithm::trim(value);
    if (!value.empty() && value.back() == '\r') {
      value.pop_back();
    }
    if (key == "content-length") {
      contentLength = std::stoul(value);
    } else if (key == "content-range") {
      size_t separator = value.find('/');
      if (separator != std::string::npos) {
        value = value.substr(separator + 1);
        contentLength = std::stoul(value);
      }
    }
    if (key == "accept-ranges") {
      acceptRange = (value == "bytes");
    }
  }
  return totalSize;
}

int AudioGraphHttpStream::progressCallback(double dltotal, double dlnow,
                                           double ultotal, double ulnow) {
  if (seekRequestSignal.getStopToken().stop_requested() ||
      readerThread.get_stop_token().stop_requested()) {
    return 1;
  }
  return 0;
}

void AudioGraphHttpStream::readHeader() {
  curlpp::Easy request;
  curlpp::options::Url myUrl(url);
  request.setOpt(myUrl);
  request.setOpt(new curlpp::options::NoBody(true));
  request.setOpt(new curlpp::options::ConnectTimeout(10));
  request.setOpt(new curlpp::options::Timeout(10));
  request.setOpt(new curlpp::options::HeaderFunction(std::bind(
      &AudioGraphHttpStream::headerCallback, this, std::placeholders::_1,
      std::placeholders::_2, std::placeholders::_3)));
  request.setOpt(new curlpp::options::WriteFunction(
      [](char *contents, size_t size, size_t nmemb) -> size_t {
        return size * nmemb;
      }));
  request.perform();
  long responseCode = 0;
  curlpp::Info<CURLINFO_RESPONSE_CODE, long>::get(request, responseCode);
  if (responseCode != 200) {
    std::string message =
        "HTTP HEADER request failed with code " + std::to_string(responseCode);
    throw std::runtime_error(message);
  }

  spdlog::debug("Http Stream: content-length={}", contentLength);
}

void AudioGraphHttpStream::handleSeekSignal(size_t position) {
  buffer.clear();
  buffer.resetEof();
  offset = position;
  setStreamingState = true;
  setState(StreamState(AudioGraphNodeState::PREPARING));
  seekRequestSignal.respond(offset);
}

void AudioGraphHttpStream::reader(std::stop_token stopToken) {
  try {
    setState(StreamState(AudioGraphNodeState::PREPARING));
    readHeader();
    while (!stopToken.stop_requested()) {
      readContentChunks(stopToken);
      buffer.setEof();
      spdlog::debug("Finished reading content");
      auto seekValue = seekRequestSignal.waitValue(stopToken);
      if (!seekValue) {
        break;
      }
      handleSeekSignal(*seekValue);
    }
  } catch (curlpp::LibcurlRuntimeError &ex) {
    if (!stopToken.stop_requested()) {
      std::string message = std::string("Libcurl exception: ") + ex.what();
      spdlog::error(message);
      setState({AudioGraphNodeState::ERROR, message});
    }
  } catch (std::runtime_error &ex) {
    spdlog::error(ex.what());
    setState({AudioGraphNodeState::ERROR, ex.what()});
  }
  buffer.setEof();
  spdlog::debug("Reader thread is finished");
}

void AudioGraphHttpStream::readContentChunks(std::stop_token stopToken) {
  using namespace std::placeholders;
  int numRetries = 3;
  for (; offset < contentLength;) {
    auto seekToPos = seekRequestSignal.getValue();
    if (seekToPos) {
      handleSeekSignal(seekToPos.value());
    }

    auto combinedStopToken =
        combineStopTokens(stopToken, seekRequestSignal.getStopToken());

    buffer.waitForSpace(combinedStopToken.get_token(), buffer.max_size() / 2);
    long responseCode = 0;
    if (stopToken.stop_requested()) {
      break;
    }

    if (seekRequestSignal.getStopToken().stop_requested()) {
      continue;
    }

    try {
      responseCode = readSingleChunk(stopToken);
    } catch (const curlpp::LibcurlRuntimeError &ex) {
      if (stopToken.stop_requested()) {
        break;
      }
      if (seekRequestSignal.getStopToken().stop_requested()) {
        continue;
      }
      responseCode = -1;
      spdlog::warn("Libcurl exception: {}", ex.what());
      if (numRetries == 0 || !acceptRange) {
        spdlog::error("Request failed at offset {}/{} - aborting", offset,
                      contentLength);
        throw;
      } else {
        spdlog::warn("Request failed at offset {}/{} - retrying {} more times",
                     offset, contentLength, numRetries);
        --numRetries;
        continue;
      }
    }

    switch (responseCode) {
    case 200:
      break;
    case 206:
      continue;
    case 416:
      throw std::runtime_error("Request range not satisfiable, " +
                               std::to_string(offset) + "/" +
                               std::to_string(contentLength) +
                               ", chunk=" + std::to_string(chunkSize));
    default:
      if (responseCode != 200) {
        if (numRetries == 0 || !acceptRange) {
          std::string message = "HTTP GET request failed with code " +
                                std::to_string(responseCode);
          throw std::runtime_error(message);
        } else {
          --numRetries;
          spdlog::warn(
              "HTTP GET request failed with code {}, retrying {} more times",
              responseCode, numRetries);
        }
      } else {
        numRetries = 3;
      }
    }

    if (responseCode == 206) {
      continue;
    }
  }
}

int AudioGraphHttpStream::readSingleChunk(std::stop_token stopToken) {
  using namespace std::placeholders;

  curlpp::Easy request;
  curlpp::options::Url myUrl(url);
  request.setOpt(myUrl);

  if (acceptRange) {
    std::ostringstream range;
    range << offset << "-";
    if (chunkSize) {
      range << chunkSize + offset - 1;
    }
    spdlog::info("Request range {}-{}/{}", offset,
                 (chunkSize ? std::to_string(chunkSize + offset - 1) : ""),
                 contentLength);
    request.setOpt(new curlpp::options::Range(range.str()));
  }

  request.setOpt(new curlpp::options::ConnectTimeout(10));
  request.setOpt(new curlpp::options::WriteFunction(
      std::bind(&AudioGraphHttpStream::WriteCallback, this, _1, _2, _3)));
  request.setOpt(new curlpp::options::NoProgress(false));
  request.setOpt(new curlpp::options::ProgressFunction(std::bind(
      &AudioGraphHttpStream::progressCallback, this, _1, _2, _3, _4)));
  request.perform();

  long responseCode = 0;
  curlpp::Info<CURLINFO_RESPONSE_CODE, long>::get(request, responseCode);
  return responseCode;
}

size_t AudioGraphHttpStream::read(void *data, size_t size) {
  return buffer.read(static_cast<uint8_t *>(data), size);
}

size_t AudioGraphHttpStream::waitForData(std::stop_token stopToken,
                                         size_t size) {
  auto combinedToken =
      combineStopTokens(stopToken, readerThread.get_stop_token());
  return buffer.waitForData(combinedToken.get_token(), size);
}

size_t AudioGraphHttpStream::waitForDataFor(std::stop_token stopToken,
                                            std::chrono::milliseconds timeout,
                                            size_t size) {
  auto combinedToken =
      combineStopTokens(stopToken, readerThread.get_stop_token());
  return buffer.waitForDataFor(combinedToken.get_token(), timeout, size);
}

size_t AudioGraphHttpStream::seekTo(size_t absolutePosition) {
  if (!acceptRange || absolutePosition >= contentLength ||
      getState().state == AudioGraphNodeState::ERROR) {
    return -1;
  }

  spdlog::debug("AudioGraphHttpStream::seekTo({})", absolutePosition);
  seekRequestSignal.sendValue(absolutePosition);
  auto retVal = seekRequestSignal.getResponse(std::stop_token());
  spdlog::debug("AudioGraphHttpStream::seekTo({}) -> {}", absolutePosition,
                retVal);
  return retVal;
}
