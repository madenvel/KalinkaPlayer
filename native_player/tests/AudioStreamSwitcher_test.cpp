#include "AudioStreamSwitcher.h"
#include "SineWaveNode.h"

#include <gtest/gtest.h>
#include <memory>

#include "TestHelpers.h"

class WaitingNode : public AudioGraphOutputNode {
  bool done = false;

public:
  WaitingNode() = default;
  ~WaitingNode() { done = true; }

  virtual size_t read(void *data, size_t size) override { return 0; }

  virtual StreamState getState() override {
    return StreamState(AudioGraphNodeState::STREAMING);
  }

  virtual size_t waitForData(std::stop_token stopToken = std::stop_token(),
                             size_t size = 1) override {
    while (!done && !stopToken.stop_requested()) {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    return 0;
  }
  virtual size_t waitForDataFor(std::stop_token stopToken,
                                std::chrono::milliseconds timeout,
                                size_t size) override {
    while (!done && !stopToken.stop_requested() && timeout.count() > 0) {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      timeout -= std::chrono::milliseconds(100);
    }
    return 0;
  }
};

class AudioStreamSwitcherTest : public ::testing::Test {
protected:
  std::shared_ptr<SineWaveNode> sineWaveNode440 =
      std::make_shared<SineWaveNode>(440, 100);
  std::shared_ptr<SineWaveNode> sineWaveNode880 =
      std::make_shared<SineWaveNode>(880, 50);
  std::shared_ptr<AudioStreamSwitcher> audioStreamSwitcher =
      std::make_shared<AudioStreamSwitcher>();

  bool isSineWaveValid(int frequency, int timeMs, const void *data) {
    bool result = true;
    for (int i = 0; i < 96 * timeMs; i++) {
      const auto value =
          floor(8192 * sin(2 * M_PI * static_cast<double>(frequency) *
                           floor(i / 2) / 48000.0));
      auto found = static_cast<const int16_t *>(data)[i];
      if (found != value) {
        result = false;
      }
    }
    return result;
  }
};

TEST_F(AudioStreamSwitcherTest, constructor_destructor) {}

TEST_F(AudioStreamSwitcherTest, connectTo) {
  audioStreamSwitcher->connectTo(sineWaveNode440);
  audioStreamSwitcher->connectTo(sineWaveNode880);

  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioStreamSwitcher->acceptSourceChange();
  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::STREAMING);
}

TEST_F(AudioStreamSwitcherTest, connectTo_nullptr) {
  EXPECT_THROW(audioStreamSwitcher->connectTo(nullptr), std::runtime_error);
}

TEST_F(AudioStreamSwitcherTest, disconnect) {
  audioStreamSwitcher->connectTo(sineWaveNode440);
  audioStreamSwitcher->connectTo(sineWaveNode880);

  audioStreamSwitcher->disconnect(sineWaveNode440);
  audioStreamSwitcher->disconnect(sineWaveNode880);

  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::FINISHED);
}

TEST_F(AudioStreamSwitcherTest, readStreamData) {
  audioStreamSwitcher->connectTo(sineWaveNode440);
  audioStreamSwitcher->connectTo(sineWaveNode880);

  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);

  audioStreamSwitcher->acceptSourceChange();

  auto state =
      waitForStatus(*audioStreamSwitcher, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);

  StreamInfo sine440 = sineWaveNode440->getState().streamInfo.value();
  size_t stream440Size = sine440.totalSamples * sine440.format.channels *
                         sine440.format.bitsPerSample / 8;

  StreamInfo sine880 = sineWaveNode880->getState().streamInfo.value();
  size_t stream880Size = sine880.totalSamples * sine880.format.channels *
                         sine880.format.bitsPerSample / 8;

  std::vector<uint8_t> stream440(stream440Size + 1);
  std::vector<uint8_t> stream880(stream880Size + 1);

  EXPECT_EQ(audioStreamSwitcher->read(stream440.data(), stream440Size),
            stream440Size);

  EXPECT_TRUE(isSineWaveValid(440, 100, stream440.data()));

  ASSERT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);

  audioStreamSwitcher->acceptSourceChange();

  EXPECT_EQ(audioStreamSwitcher->read(stream880.data(), stream880Size),
            stream880Size);

  EXPECT_TRUE(isSineWaveValid(880, 50, stream880.data()));

  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::FINISHED);
}

TEST_F(AudioStreamSwitcherTest, waitForData) {
  audioStreamSwitcher->connectTo(sineWaveNode440);
  audioStreamSwitcher->connectTo(sineWaveNode880);

  ASSERT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioStreamSwitcher->acceptSourceChange();

  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::STREAMING);

  StreamInfo sine440 = sineWaveNode440->getState().streamInfo.value();
  size_t stream440Size = sine440.totalSamples * sine440.format.channels *
                         sine440.format.bitsPerSample / 8;

  StreamInfo sine880 = sineWaveNode880->getState().streamInfo.value();
  size_t stream880Size = sine880.totalSamples * sine880.format.channels *
                         sine880.format.bitsPerSample / 8;

  std::vector<uint8_t> stream440(stream440Size + 1);

  EXPECT_EQ(
      audioStreamSwitcher->waitForData(std::stop_token(), stream440Size + 1),
      stream440Size);

  EXPECT_EQ(audioStreamSwitcher->read(stream440.data(), stream440Size),
            stream440Size);
  ASSERT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioStreamSwitcher->acceptSourceChange();

  EXPECT_EQ(
      audioStreamSwitcher->waitForData(std::stop_token(), stream880Size + 1),
      stream880Size);
  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::STREAMING);

  audioStreamSwitcher->disconnect(sineWaveNode440);
  audioStreamSwitcher->disconnect(sineWaveNode880);

  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::FINISHED);
}

TEST_F(AudioStreamSwitcherTest, manualSwitchStream) {
  audioStreamSwitcher->connectTo(sineWaveNode440);
  ASSERT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioStreamSwitcher->acceptSourceChange();
  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::STREAMING);
  std::vector<uint8_t> buffer(100);
  EXPECT_EQ(audioStreamSwitcher->read(buffer.data(), buffer.size()),
            buffer.size());

  audioStreamSwitcher->connectTo(sineWaveNode880);
  audioStreamSwitcher->disconnect(sineWaveNode440);

  ASSERT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioStreamSwitcher->acceptSourceChange();

  EXPECT_EQ(audioStreamSwitcher->read(buffer.data(), buffer.size()),
            buffer.size());
}

TEST_F(AudioStreamSwitcherTest, disconnectDuringWait) {
  std::shared_ptr<WaitingNode> waitingNode = std::make_shared<WaitingNode>();
  audioStreamSwitcher->connectTo(waitingNode);

  std::thread disconnectThread([&]() {
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    audioStreamSwitcher->disconnect(waitingNode);
  });

  EXPECT_EQ(audioStreamSwitcher->waitForData(std::stop_token(), 1), 0);
  disconnectThread.join();
}

TEST_F(AudioStreamSwitcherTest, connect_after_finished) {
  audioStreamSwitcher->connectTo(sineWaveNode440);
  ASSERT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioStreamSwitcher->acceptSourceChange();
  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::STREAMING);
  std::vector<uint8_t> buffer(100);

  while (audioStreamSwitcher->getState().state ==
         AudioGraphNodeState::STREAMING) {
    audioStreamSwitcher->read(buffer.data(), buffer.size());
  }
  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::FINISHED);

  audioStreamSwitcher->connectTo(sineWaveNode880);

  ASSERT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioStreamSwitcher->acceptSourceChange();

  while (audioStreamSwitcher->getState().state ==
         AudioGraphNodeState::STREAMING) {
    audioStreamSwitcher->read(buffer.data(), buffer.size());
  }
  EXPECT_EQ(audioStreamSwitcher->getState().state,
            AudioGraphNodeState::FINISHED);
}