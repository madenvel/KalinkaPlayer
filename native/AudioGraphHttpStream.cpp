#include "AudioGraphHttpStream.h"

#include <curlpp/Info.hpp>
#include <curlpp/Options.hpp>
#include <curlpp/Types.hpp>
#include <curlpp/cURLpp.hpp>

AudioGraphHttpStream::AudioGraphHttpStream(const std::string &url,
                                           size_t bufferSize)
    : url(url),
      buffer(std::max(bufferSize, static_cast<size_t>(CURL_MAX_WRITE_SIZE)),
             std::bind(&AudioGraphHttpStream::emptyBufferCallback, this,
                       std::placeholders::_1)) {
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
  long responseCode = 0;
  curlpp::Info<CURLINFO_RESPONSE_CODE, long>::get(request, responseCode);

  size_t sizeWritten = 0;
  if (responseCode == 200) {
    size_t totalSize = size * nmemb;
    while (sizeWritten < totalSize) {
      auto spaceAvailable = buffer.waitForSpace(readerThread.get_stop_token());
      if (readerThread.get_stop_token().stop_requested()) {
        break;
      }
      sizeWritten +=
          buffer.write(static_cast<uint8_t *>(contents) + sizeWritten,
                       std::min(totalSize - sizeWritten, spaceAvailable));
    }
  } else {
    std::cerr << "HTTP request failed with code " + std::to_string(responseCode)
              << std::endl;
  }

  return sizeWritten;
}

void AudioGraphHttpStream::emptyBufferCallback(DequeBuffer<uint8_t> &buffer) {
  if (buffer.isEof() && getState() != AudioGraphNodeState::ERROR) {
    setState(AudioGraphNodeState::FINISHED);
  }
}

void AudioGraphHttpStream::reader(std::stop_token token) {
  using namespace std::placeholders;
  try {
    setState(AudioGraphNodeState::STREAMING);
    curlpp::options::Url myUrl(url);
    request.setOpt(myUrl);
    curlpp::types::WriteFunctionFunctor functor =
        std::bind(&AudioGraphHttpStream::WriteCallback, this, _1, _2, _3);

    request.setOpt(new curlpp::options::WriteFunction(functor));
    request.perform();
    buffer.setEof();
    long responseCode = 0;
    curlpp::Info<CURLINFO_RESPONSE_CODE, long>::get(request, responseCode);
    if (responseCode != 200) {
      std::cerr << "HTTP request failed with code " +
                       std::to_string(responseCode);
      setState(AudioGraphNodeState::ERROR);
    }
  } catch (curlpp::LibcurlRuntimeError &ex) {
    if (!token.stop_requested()) {
      std::cerr << "Libcurl exception: " << ex.what() << std::endl;
      setState(AudioGraphNodeState::ERROR);
    }
  }
}

size_t AudioGraphHttpStream::read(void *data, size_t size) {
  return buffer.read(static_cast<uint8_t *>(data), size);
}

size_t AudioGraphHttpStream::waitForData(std::stop_token stopToken,
                                         size_t size) {
  return buffer.waitForData(stopToken, size);
}

StreamInfo AudioGraphHttpStream::getStreamInfo() { return StreamInfo(); }