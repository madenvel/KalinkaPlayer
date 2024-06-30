#include "AlsaAudioEmitter.h"
#include "FileInputNode.h"
#include "FlacStreamDecoder.h"
#include "SineWaveNode.h"

#include <cmath>
#include <gtest/gtest.h>

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
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::FINISHED);
  alsaAudioEmitter->disconnect(outputNode);
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::STOPPED);
}

TEST_F(AlsaAudioEmitterTest, pause) {
  auto outputNode = std::make_shared<SineWaveNode>(440, 1000);
  alsaAudioEmitter->connectTo(outputNode);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);
  auto sleepAmount = 500;
  std::this_thread::sleep_for(std::chrono::milliseconds(sleepAmount));
  alsaAudioEmitter->pause(true);
  auto state = alsaAudioEmitter->getState();
  EXPECT_NEAR(state.position, sleepAmount + bufferSize / 48, 10);
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::PAUSED);
  std::this_thread::sleep_for(std::chrono::milliseconds(500));
  alsaAudioEmitter->pause(false);
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::STREAMING);
  auto start = std::chrono::high_resolution_clock::now();
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::FINISHED).state,
      AudioGraphNodeState::FINISHED);
  auto end = std::chrono::high_resolution_clock::now();
  auto duration =
      std::chrono::duration_cast<std::chrono::milliseconds>(end - start);
  EXPECT_NEAR(duration.count(), 500, 10);
  alsaAudioEmitter->disconnect(outputNode);
}