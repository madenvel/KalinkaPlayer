#include "AlsaAudioEmitter.h"
#include "AudioGraphNode.h"
#include "FileInputNode.h"
#include "FlacStreamDecoder.h"

#include <gtest/gtest.h>
#include <memory>

#include "TestHelpers.h"

class AudioGraphNodeTest : public ::testing::Test {
protected:
  const std::string filename = "files/tone440.flac";

  std::shared_ptr<FlacStreamDecoder> flacStreamDecoder;
  std::shared_ptr<AlsaAudioEmitter> alsaAudioEmitter;

  AudioGraphNodeTest()
      : flacStreamDecoder(std::make_shared<FlacStreamDecoder>(65536)),
        alsaAudioEmitter(std::make_shared<AlsaAudioEmitter>("default")) {}
};

TEST_F(AudioGraphNodeTest, stateMonitor) {
  auto monitor = std::make_shared<StateMonitor>(alsaAudioEmitter.get());
  EXPECT_TRUE(monitor->isRunning());
  auto fileInputNode = std::make_shared<FileInputNode>(filename);
  flacStreamDecoder->connectTo(fileInputNode);
  alsaAudioEmitter->connectTo(flacStreamDecoder);
  waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::FINISHED);
  int i = 0;
  AudioGraphNodeState states[] = {
      AudioGraphNodeState::STOPPED, AudioGraphNodeState::PREPARING,
      AudioGraphNodeState::STREAMING, AudioGraphNodeState::FINISHED};

  while (monitor->hasData()) {
    auto state = monitor->waitState();
    ASSERT_LT(i, sizeof(states) / sizeof(states[0]));
    EXPECT_EQ(state.state, states[i++]);
  }

  monitor->stop();
  EXPECT_FALSE(monitor->isRunning());

  alsaAudioEmitter->disconnect(flacStreamDecoder);
  flacStreamDecoder->disconnect(fileInputNode);
}