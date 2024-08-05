#include "AlsaAudioEmitter.h"
#include "FileInputNode.h"
#include "FlacStreamDecoder.h"
#include "SineWaveNode.h"

#include <cmath>
#include <gtest/gtest.h>

#include "ErrorFakeNode.h"
#include "SlowOutputNode.h"
#include "TestHelpers.h"

class AlsaAudioEmitterTest : public ::testing::Test {
protected:
  const size_t bufferSize = 16384;
  const std::string device = "hw:0,0";

  std::shared_ptr<AlsaAudioEmitter> alsaAudioEmitter;

  void SetUp() override {
    alsaAudioEmitter = std::make_shared<AlsaAudioEmitter>(device);
  }
};

TEST_F(AlsaAudioEmitterTest, constructor_destructor) {}

TEST_F(AlsaAudioEmitterTest, connectTo) {
  auto outputNode = std::make_shared<SineWaveNode>(440, 1000);
  alsaAudioEmitter->connectTo(outputNode);
  alsaAudioEmitter->disconnect(outputNode);
}

TEST_F(AlsaAudioEmitterTest, connectTo_nullptr) {
  EXPECT_THROW(alsaAudioEmitter->connectTo(nullptr), std::runtime_error);
}

TEST_F(AlsaAudioEmitterTest, disconnect) {
  auto outputNode = std::make_shared<SineWaveNode>(440, 1000);
  EXPECT_EQ(outputNode->getState().state, AudioGraphNodeState::STREAMING);
  alsaAudioEmitter->connectTo(outputNode);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);
  alsaAudioEmitter->disconnect(outputNode);
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::STOPPED);
}

TEST_F(AlsaAudioEmitterTest, getState) {
  auto outputNode = std::make_shared<SineWaveNode>(440, 1000);
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::STOPPED);
  alsaAudioEmitter->connectTo(outputNode);
  EXPECT_EQ(

      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);
  std::this_thread::sleep_for(std::chrono::milliseconds(1010));
  EXPECT_EQ(outputNode->getState().state, AudioGraphNodeState::FINISHED);
  std::this_thread::sleep_for(std::chrono::milliseconds(341));
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::FINISHED);
  alsaAudioEmitter->disconnect(outputNode);
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::STOPPED);
}

TEST_F(AlsaAudioEmitterTest, pause) {
  const auto totalDuration = 1000;
  auto outputNode = std::make_shared<SineWaveNode>(440, totalDuration);
  alsaAudioEmitter->connectTo(outputNode);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);
  auto sleepAmount = totalDuration / 2;
  std::this_thread::sleep_for(std::chrono::milliseconds(sleepAmount));
  alsaAudioEmitter->pause(true);
  auto state = waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::PAUSED);
  EXPECT_NEAR(state.position, sleepAmount + bufferSize / 48, 30);
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::PAUSED);
  std::this_thread::sleep_for(std::chrono::milliseconds(500));
  alsaAudioEmitter->pause(false);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);
  auto start = std::chrono::high_resolution_clock::now();
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::FINISHED).state,
      AudioGraphNodeState::FINISHED);
  auto end = std::chrono::high_resolution_clock::now();
  auto duration =
      std::chrono::duration_cast<std::chrono::milliseconds>(end - start);
  EXPECT_NEAR(duration.count(), totalDuration - state.position, 1024 / 48);
  alsaAudioEmitter->disconnect(outputNode);
}

TEST_F(AlsaAudioEmitterTest, stream_error) {
  auto outputNode = std::make_shared<ErrorFakeNode>();
  alsaAudioEmitter->connectTo(outputNode);
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  auto streamState = alsaAudioEmitter->getState();
  EXPECT_EQ(streamState.state, AudioGraphNodeState::ERROR);
  EXPECT_EQ(streamState.message, "Fake error message");
  alsaAudioEmitter->disconnect(outputNode);
}

TEST_F(AlsaAudioEmitterTest, slow_output_node_goes_into_preparing) {
  const size_t duration = 2000;
  auto outputNode = std::make_shared<SlowOutputNode>(duration, 1000);
  alsaAudioEmitter->connectTo(outputNode);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);

  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::PREPARING).state,
      AudioGraphNodeState::PREPARING);

  auto state = waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING);

  EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.position, duration / 2);

  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::FINISHED).state,
      AudioGraphNodeState::FINISHED);

  alsaAudioEmitter->disconnect(outputNode);
}

TEST_F(AlsaAudioEmitterTest, test_seekToForward) {
  const auto totalDuration = 2000;
  auto outputNode = std::make_shared<SineWaveNode>(440, totalDuration);
  alsaAudioEmitter->connectTo(outputNode);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);
  auto sleepAmount = totalDuration / 4;
  std::this_thread::sleep_for(std::chrono::milliseconds(sleepAmount));
  alsaAudioEmitter->seek(totalDuration / 2);
  auto state = waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING);
  EXPECT_NEAR(state.position, 1000, 10);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::FINISHED).state,
      AudioGraphNodeState::FINISHED);
  alsaAudioEmitter->disconnect(outputNode);
}

TEST_F(AlsaAudioEmitterTest, test_seekBackwards) {
  const auto totalDuration = 2000;
  auto outputNode = std::make_shared<SineWaveNode>(440, totalDuration);
  alsaAudioEmitter->connectTo(outputNode);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);
  auto sleepAmount = totalDuration / 4;
  std::this_thread::sleep_for(std::chrono::milliseconds(sleepAmount));
  alsaAudioEmitter->seek(0);
  auto state = waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING);
  EXPECT_NEAR(state.position, 0, 10);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::FINISHED).state,
      AudioGraphNodeState::FINISHED);
  alsaAudioEmitter->disconnect(outputNode);
}

TEST_F(AlsaAudioEmitterTest, test_seekWhenStopped) {
  EXPECT_EQ(alsaAudioEmitter->seek(0), -1);
}