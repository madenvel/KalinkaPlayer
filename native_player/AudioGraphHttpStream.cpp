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
  while (sizeWritten < totalSize) {
    auto spaceAvailable = buffer.waitForSpace(combinedStopToken->get_token());
    if (combinedStopToken->get_token().stop_requested()) {
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
  if (seekStopTokenSource.get_token().stop_requested()) {
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

void AudioGraphHttpStream::reader(std::stop_token stopToken) {
  try {
    setState(StreamState(AudioGraphNodeState::PREPARING));
    readHeader();
    setState(StreamState(AudioGraphNodeState::STREAMING, 0,
                         StreamInfo{.totalSamples = contentLength}));
    readContentChunks(stopToken);
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
}

void AudioGraphHttpStream::readContentChunks(std::stop_token stopToken) {
  using namespace std::placeholders;
  int numRetries = 3;
  for (; offset < contentLength;) {
    auto seekToPos = seekRequest.load();
    if (seekToPos != -1) {
      spdlog::info("Clearing buffer");

      buffer.clear();
      spdlog::info("Buffer is clear");
      offset = seekToPos;
      seekRequest = -1;
      cv.notify_one();
    }

    buffer.waitForSpace(stopToken, buffer.max_size() / 2);
    long responseCode = 0;
    if (stopToken.stop_requested()) {
      break;
    }

    try {
      responseCode = readSingleChunk(stopToken);
    } catch (const curlpp::LibcurlRuntimeError &ex) {
      if (stopToken.stop_requested()) {
        break;
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
  combinedStopToken = std::make_unique<CombinedStopToken>(
      stopToken, seekStopTokenSource.get_token());
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
  return buffer.waitForData(stopToken, size);
}

size_t AudioGraphHttpStream::waitForDataFor(std::stop_token stopToken,
                                            std::chrono::milliseconds timeout,
                                            size_t size) {
  return buffer.waitForDataFor(stopToken, timeout, size);
}

size_t AudioGraphHttpStream::seekTo(size_t absolutePosition) {

  if (!acceptRange || absolutePosition >= contentLength) {
    return offset;
  }

  std::unique_lock lock(mutex);
  seekRequest = absolutePosition;
  seekStopTokenSource.request_stop();
  seekStopTokenSource = std::stop_source();

  cv.wait(lock, readerThread.get_stop_token(),
          [this, absolutePosition] { return offset == absolutePosition; });

  return absolutePosition;
}
