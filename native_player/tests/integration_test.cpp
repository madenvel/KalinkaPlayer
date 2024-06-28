#include "AlsaAudioEmitter.h"
#include "AudioGraphHttpStream.h"
#include "AudioStreamSwitcher.h"
#include "FileInputNode.h"
#include "FlacStreamDecoder.h"
#include "SineWaveNode.h"

#include <gtest/gtest.h>
#include <memory>

#include "TestHelpers.h"

class IntegrationTest : public ::testing::Test {
protected:
  const std::string filename = "files/tone440.flac";

  std::shared_ptr<FlacStreamDecoder> flacStreamDecoder;
  std::shared_ptr<AlsaAudioEmitter> alsaAudioEmitter;

  IntegrationTest()
      : flacStreamDecoder(std::make_shared<FlacStreamDecoder>(65536)),
        alsaAudioEmitter(std::make_shared<AlsaAudioEmitter>("hw:0,0")) {}
};

TEST_F(IntegrationTest, fileInputIntegration) {
  auto fileInputNode = std::make_shared<FileInputNode>(filename);
  flacStreamDecoder->connectTo(fileInputNode);
  EXPECT_EQ(flacStreamDecoder->getState().state,
            AudioGraphNodeState::PREPARING);
  flacStreamDecoder->waitForData(std::stop_token(), 8192);
  EXPECT_EQ(flacStreamDecoder->getState().state,
            AudioGraphNodeState::STREAMING);
  alsaAudioEmitter->connectTo(flacStreamDecoder);
  std::this_thread::sleep_for(std::chrono::milliseconds(1200));
  alsaAudioEmitter->disconnect(flacStreamDecoder);
  flacStreamDecoder->disconnect(fileInputNode);
}

TEST_F(IntegrationTest, httpInputIntegration) {
  auto audioGraphHttpStream = std::make_shared<AudioGraphHttpStream>(
      "https://getsamplefiles.com/download/flac/sample-3.flac", 16384);
  flacStreamDecoder->connectTo(audioGraphHttpStream);
  EXPECT_EQ(flacStreamDecoder->getState().state,
            AudioGraphNodeState::PREPARING);
  alsaAudioEmitter->connectTo(flacStreamDecoder);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);
  waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::FINISHED);

  alsaAudioEmitter->disconnect(flacStreamDecoder);
  flacStreamDecoder->disconnect(audioGraphHttpStream);
}

TEST_F(IntegrationTest, streamSwitch) {
  const int duration1 = 500;
  const int duration2 = 200;
  auto sineWaveNode440 = std::make_shared<SineWaveNode>(440, duration1);
  auto sineWaveNode880 = std::make_shared<SineWaveNode>(880, duration2);
  auto streamSwitchNode = std::make_shared<AudioStreamSwitcher>();
  EXPECT_EQ(streamSwitchNode->getState().state, AudioGraphNodeState::STOPPED);
  alsaAudioEmitter->connectTo(streamSwitchNode);
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::STOPPED);
  streamSwitchNode->connectTo(sineWaveNode440);
  streamSwitchNode->connectTo(sineWaveNode880);

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

  EXPECT_NEAR(duration.count(), (duration1 + duration2), 10);
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::FINISHED);

  alsaAudioEmitter->disconnect(streamSwitchNode);
}
