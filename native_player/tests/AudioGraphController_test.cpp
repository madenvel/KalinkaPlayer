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
};

class AudioGraphControllerTest : public ::testing::Test {
protected:
  std::shared_ptr<SineWaveNode> sineWaveNode440 =
      std::make_shared<SineWaveNode>(440, 100);
  std::shared_ptr<SineWaveNode> sineWaveNode880 =
      std::make_shared<SineWaveNode>(880, 50);

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

TEST_F(AudioGraphControllerTest, constructor_destructor) {
  std::shared_ptr<AudioStreamSwitcher> audioGraphController =
      std::make_shared<AudioStreamSwitcher>();
}

TEST_F(AudioGraphControllerTest, connectTo) {
  std::shared_ptr<AudioStreamSwitcher> audioGraphController =
      std::make_shared<AudioStreamSwitcher>();
  audioGraphController->connectTo(sineWaveNode440);
  audioGraphController->connectTo(sineWaveNode880);

  EXPECT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioGraphController->acceptSourceChange();
  EXPECT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::STREAMING);
}

TEST_F(AudioGraphControllerTest, connectTo_nullptr) {
  std::shared_ptr<AudioStreamSwitcher> audioGraphController =
      std::make_shared<AudioStreamSwitcher>();
  EXPECT_THROW(audioGraphController->connectTo(nullptr), std::runtime_error);
}

TEST_F(AudioGraphControllerTest, disconnect) {
  std::shared_ptr<AudioStreamSwitcher> audioGraphController =
      std::make_shared<AudioStreamSwitcher>();
  audioGraphController->connectTo(sineWaveNode440);
  audioGraphController->connectTo(sineWaveNode880);

  audioGraphController->disconnect(sineWaveNode440);
  audioGraphController->disconnect(sineWaveNode880);

  EXPECT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::FINISHED);
}

TEST_F(AudioGraphControllerTest, readStreamData) {
  std::shared_ptr<AudioStreamSwitcher> audioGraphController =
      std::make_shared<AudioStreamSwitcher>();
  audioGraphController->connectTo(sineWaveNode440);
  audioGraphController->connectTo(sineWaveNode880);

  EXPECT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);

  audioGraphController->acceptSourceChange();

  auto state =
      waitForStatus(*audioGraphController, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);

  StreamInfo sine440 = sineWaveNode440->getState().streamInfo.value();
  size_t stream440Size = sine440.totalSamples * sine440.format.channels *
                         sine440.format.bitsPerSample / 8;

  StreamInfo sine880 = sineWaveNode880->getState().streamInfo.value();
  size_t stream880Size = sine880.totalSamples * sine880.format.channels *
                         sine880.format.bitsPerSample / 8;

  std::vector<uint8_t> stream440(stream440Size + 1);
  std::vector<uint8_t> stream880(stream880Size + 1);

  EXPECT_EQ(audioGraphController->read(stream440.data(), stream440Size),
            stream440Size);

  EXPECT_TRUE(isSineWaveValid(440, 100, stream440.data()));

  ASSERT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);

  std::cerr << "Calling acceptSourceChange" << std::endl;
  audioGraphController->acceptSourceChange();

  EXPECT_EQ(audioGraphController->read(stream880.data(), stream880Size),
            stream880Size);

  EXPECT_TRUE(isSineWaveValid(880, 50, stream880.data()));

  EXPECT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::FINISHED);
}

TEST_F(AudioGraphControllerTest, waitForData) {
  std::shared_ptr<AudioStreamSwitcher> audioGraphController =
      std::make_shared<AudioStreamSwitcher>();
  audioGraphController->connectTo(sineWaveNode440);
  audioGraphController->connectTo(sineWaveNode880);

  ASSERT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioGraphController->acceptSourceChange();

  EXPECT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::STREAMING);

  StreamInfo sine440 = sineWaveNode440->getState().streamInfo.value();
  size_t stream440Size = sine440.totalSamples * sine440.format.channels *
                         sine440.format.bitsPerSample / 8;

  StreamInfo sine880 = sineWaveNode880->getState().streamInfo.value();
  size_t stream880Size = sine880.totalSamples * sine880.format.channels *
                         sine880.format.bitsPerSample / 8;

  std::vector<uint8_t> stream440(stream440Size + 1);

  EXPECT_EQ(
      audioGraphController->waitForData(std::stop_token(), stream440Size + 1),
      stream440Size);

  EXPECT_EQ(audioGraphController->read(stream440.data(), stream440Size),
            stream440Size);
  ASSERT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioGraphController->acceptSourceChange();

  EXPECT_EQ(
      audioGraphController->waitForData(std::stop_token(), stream880Size + 1),
      stream880Size);
  EXPECT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::STREAMING);

  audioGraphController->disconnect(sineWaveNode440);
  audioGraphController->disconnect(sineWaveNode880);

  EXPECT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::FINISHED);
}

TEST_F(AudioGraphControllerTest, manualSwitchStream) {
  std::shared_ptr<AudioStreamSwitcher> audioGraphController =
      std::make_shared<AudioStreamSwitcher>();
  audioGraphController->connectTo(sineWaveNode440);
  ASSERT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioGraphController->acceptSourceChange();
  EXPECT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::STREAMING);
  std::vector<uint8_t> buffer(100);
  EXPECT_EQ(audioGraphController->read(buffer.data(), buffer.size()),
            buffer.size());

  audioGraphController->connectTo(sineWaveNode880);
  audioGraphController->disconnect(sineWaveNode440);

  ASSERT_EQ(audioGraphController->getState().state,
            AudioGraphNodeState::SOURCE_CHANGED);
  audioGraphController->acceptSourceChange();

  EXPECT_EQ(audioGraphController->read(buffer.data(), buffer.size()),
            buffer.size());
}

TEST_F(AudioGraphControllerTest, disconnectDuringWait) {
  std::shared_ptr<AudioStreamSwitcher> audioGraphController =
      std::make_shared<AudioStreamSwitcher>();
  std::shared_ptr<WaitingNode> waitingNode = std::make_shared<WaitingNode>();
  audioGraphController->connectTo(waitingNode);

  std::thread disconnectThread([&]() {
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    audioGraphController->disconnect(waitingNode);
  });

  EXPECT_EQ(audioGraphController->waitForData(std::stop_token(), 1), 0);
  disconnectThread.join();
}