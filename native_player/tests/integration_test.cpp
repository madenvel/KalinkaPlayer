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

  EXPECT_NEAR(duration.count(), (duration1 + duration2), 15);
  EXPECT_EQ(alsaAudioEmitter->getState().state, AudioGraphNodeState::FINISHED);

  alsaAudioEmitter->disconnect(streamSwitchNode);
}

TEST_F(IntegrationTest, fileInputSearchForward) {
  auto fileInputNode = std::make_shared<FileInputNode>(filename);
  flacStreamDecoder->connectTo(fileInputNode);
  auto state =
      waitForStatus(*flacStreamDecoder, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
  std::vector<uint8_t> buffer(4096);
  flacStreamDecoder->waitForData(std::stop_token(), 4096);
  flacStreamDecoder->read(buffer.data(), 4096);
  EXPECT_EQ(flacStreamDecoder->seekTo(16384), 16384);
  EXPECT_EQ(flacStreamDecoder->getState().state,
            AudioGraphNodeState::PREPARING);
  state = waitForStatus(*flacStreamDecoder, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
  EXPECT_TRUE(state.streamInfo.has_value());
  EXPECT_EQ(state.position, 16384);
  flacStreamDecoder->waitForData(std::stop_token(), 4096);
  flacStreamDecoder->read(buffer.data(), 4096);
  flacStreamDecoder->disconnect(fileInputNode);
}

TEST_F(IntegrationTest, fileInputSearchBackward) {
  auto fileInputNode = std::make_shared<FileInputNode>(filename);
  flacStreamDecoder->connectTo(fileInputNode);
  auto state =
      waitForStatus(*flacStreamDecoder, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
  std::vector<uint8_t> buffer(4096);
  flacStreamDecoder->waitForData(std::stop_token(), 4096);
  flacStreamDecoder->read(buffer.data(), 4096);
  EXPECT_EQ(flacStreamDecoder->seekTo(100), 100);
  EXPECT_GE(flacStreamDecoder->waitForData(std::stop_token(), 4096), 4096);
  EXPECT_EQ(flacStreamDecoder->read(buffer.data(), 4096), 4096);
  flacStreamDecoder->disconnect(fileInputNode);
}

TEST_F(IntegrationTest, httpInputSearchForward) {
  auto audioGraphHttpStream = std::make_shared<AudioGraphHttpStream>(
      "https://getsamplefiles.com/download/flac/sample-3.flac", 16384);
  flacStreamDecoder->connectTo(audioGraphHttpStream);
  auto state =
      waitForStatus(*flacStreamDecoder, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
  std::vector<uint8_t> buffer(4096);
  flacStreamDecoder->waitForData(std::stop_token(), 4096);
  flacStreamDecoder->read(buffer.data(), 4096);
  EXPECT_EQ(flacStreamDecoder->seekTo(16384), 16384);
  EXPECT_EQ(flacStreamDecoder->getState().state,
            AudioGraphNodeState::PREPARING);
  state = waitForStatus(*flacStreamDecoder, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
  EXPECT_TRUE(state.streamInfo.has_value());
  EXPECT_EQ(state.position, 16384);
  flacStreamDecoder->waitForData(std::stop_token(), 4096);
  flacStreamDecoder->read(buffer.data(), 4096);
  flacStreamDecoder->disconnect(audioGraphHttpStream);
}

TEST_F(IntegrationTest, fileInputReadAllAndSearchToBeg) {
  auto fileInputNode = std::make_shared<FileInputNode>(filename);
  flacStreamDecoder->connectTo(fileInputNode);
  auto state =
      waitForStatus(*flacStreamDecoder, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
  auto contentLength = state.streamInfo.value().totalSamples;
  std::vector<uint8_t> buffer(4096);
  size_t contentRead = 0;
  while (flacStreamDecoder->waitForData(std::stop_token(), 4096)) {
    contentRead += flacStreamDecoder->read(buffer.data(), 4096);
  }
  EXPECT_EQ(contentRead, contentLength * 4);

  EXPECT_EQ(flacStreamDecoder->getState().state, AudioGraphNodeState::FINISHED);

  flacStreamDecoder->seekTo(0);

  state = waitForStatus(*flacStreamDecoder, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
  EXPECT_TRUE(state.streamInfo.has_value());
  EXPECT_EQ(state.position, 0);
  contentRead = 0;
  while (flacStreamDecoder->waitForData(std::stop_token(), 4096)) {
    contentRead += flacStreamDecoder->read(buffer.data(), 4096);
  }
  EXPECT_EQ(contentRead, contentLength * 4);
  flacStreamDecoder->disconnect(fileInputNode);
}

TEST_F(IntegrationTest, test_play_after_finished) {
  const auto totalDuration = 1000;
  auto outputNode1 = std::make_shared<SineWaveNode>(440, totalDuration);
  auto outputNode2 = std::make_shared<SineWaveNode>(880, totalDuration);
  auto switcher = std::make_shared<AudioStreamSwitcher>();
  switcher->connectTo(outputNode1);
  alsaAudioEmitter->connectTo(switcher);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::FINISHED).state,
      AudioGraphNodeState::FINISHED);
  switcher->connectTo(outputNode2);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::STREAMING).state,
      AudioGraphNodeState::STREAMING);
  EXPECT_EQ(
      waitForStatus(*alsaAudioEmitter, AudioGraphNodeState::FINISHED).state,
      AudioGraphNodeState::FINISHED);
}