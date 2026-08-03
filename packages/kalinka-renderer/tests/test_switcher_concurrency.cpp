#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <memory>
#include <thread>

#include "native_player/AudioStreamSwitcher.h"

namespace {

class FakeStream : public AudioGraphOutputNode {
public:
  explicit FakeStream(std::optional<StreamId> streamId)
      : AudioGraphNode(streamId) {}

  size_t read(void *, size_t) override { return 0; }
  size_t waitForData(std::stop_token, size_t) override { return 0; }
  size_t waitForDataFor(std::stop_token, std::chrono::milliseconds,
                        size_t) override {
    return 0;
  }

  void emit(AudioGraphNodeState state) { setState(StreamState(state)); }
};

}  // namespace

// Reporting and disconnecting take the node and switcher locks in opposite
// orders. A regression hangs here rather than failing; ctest bounds it.
TEST(SwitcherConcurrency, DisconnectingAStreamThatIsStillReporting) {
  for (int round = 0; round < 2000; ++round) {
    AudioStreamSwitcher switcher;
    auto stream = std::make_shared<FakeStream>(1);
    switcher.connectTo(stream);
    switcher.acceptSourceChange();

    std::atomic<bool> stop{false};
    std::thread reporting([&stream, &stop] {
      while (!stop) {
        stream->emit(AudioGraphNodeState::STREAMING);
        stream->emit(AudioGraphNodeState::PAUSED);
      }
    });

    switcher.disconnect(stream);
    stop = true;
    reporting.join();
  }
}
