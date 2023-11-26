#include "DataSource.h"

#include <curlpp/Info.hpp>
#include <curlpp/Options.hpp>
#include <curlpp/Types.hpp>
#include <curlpp/cURLpp.hpp>

HttpRequestNode::HttpRequestNode(std::string url) {
  try {
    curlpp::options::Url myUrl(url);
    request.setOpt(myUrl);
  } catch (curlpp::LibcurlRuntimeError &ex) {
    std::cerr << "Libcurl exception: " << ex.what() << std::endl;
  }
}

void HttpRequestNode::connectOut(std::shared_ptr<ProcessNode> outputNode) {
  out = outputNode;
}

void HttpRequestNode::start() { startTransfer(); }

void HttpRequestNode::stop() {
  if (readerThread.joinable()) {
    readerThread.request_stop();
    readerThread.join();
    readerThread = std::jthread();
  }
}

size_t HttpRequestNode::WriteCallback(void *contents, size_t size,
                                      size_t nmemb) {
  long responseCode = 0;
  curlpp::Info<CURLINFO_RESPONSE_CODE, long>::get(request, responseCode);

  size_t sizeWritten = 0;
  if (responseCode == 200) {
    size_t totalSize = size * nmemb;
    if (!out.expired()) {
      sizeWritten = out.lock()->write(static_cast<uint8_t *>(contents),
                                      totalSize, readerThread.get_stop_token());
    }
  }

  return sizeWritten;
}

void HttpRequestNode::startTransfer() {
  if (!readerThread.joinable()) {
    readerThread =
        std::jthread(std::bind_front(&HttpRequestNode::reader, this));
  }
}

void HttpRequestNode::reader(std::stop_token token) {
  using namespace std::placeholders;
  try {
    curlpp::types::WriteFunctionFunctor functor =
        std::bind(&HttpRequestNode::WriteCallback, this, _1, _2, _3);

    request.setOpt(new curlpp::options::WriteFunction(functor));
    request.perform();
    if (!out.expired()) {
      if (!token.stop_requested()) {
        out.lock()->setStreamFinished();
      }
    }
  } catch (curlpp::LibcurlRuntimeError &ex) {
    if (!token.stop_requested()) {
      // TODO: Use StateMachine to set the error
      std::cerr << "Libcurl failure: " << ex.what() << std::endl;
    }
  } catch (std::runtime_error &ex) {
    std::cerr << "Error: " << ex.what() << std::endl;
  }
}