#include "SineWaveNode.h"

#include <gtest/gtest.h>
#include <vector>

#include "TestHelpers.h"

class SineWaveNodeTest : public ::testing::Test {
protected:
  int duration = 1000;
  int frequency = 440;
};

TEST_F(SineWaveNodeTest, constructor_destructor) {
  SineWaveNode sineWaveNode(frequency, duration);
}

TEST_F(SineWaveNodeTest, read) {
  SineWaveNode sineWaveNode(frequency, duration);
  std::vector<int16_t> data(20);
  EXPECT_EQ(sineWaveNode.read(data.data(), 40), 40);
  for (int i = 0; i < 20; i++) {
    const auto value =
        floor(8192 * sin(2 * M_PI * static_cast<double>(frequency) *
                         floor(i / 2) / 48000.0));
    EXPECT_EQ(data[i], value);
  }
}

TEST_F(SineWaveNodeTest, waitForData) {
  SineWaveNode sineWaveNode(frequency, duration);
  EXPECT_EQ(sineWaveNode.waitForData(std::stop_token(), 40), 40);
}

TEST_F(SineWaveNodeTest, getStreamInfo) {
  SineWaveNode sineWaveNode(frequency, duration);
  auto state = sineWaveNode.getState();
  ASSERT_TRUE(state.streamInfo.has_value());
  StreamInfo audioInfo = state.streamInfo.value();
  EXPECT_EQ(audioInfo.format.sampleRate, 48000);
  EXPECT_EQ(audioInfo.format.channels, 2);
  EXPECT_EQ(audioInfo.format.bitsPerSample, 16);
  EXPECT_EQ(audioInfo.totalSamples,
            static_cast<unsigned int>(duration) * 48000 / 1000);
}

TEST_F(SineWaveNodeTest, read_all) {
  SineWaveNode sineWaveNode(frequency, duration);
  std::vector<int16_t> data(96000);
  EXPECT_EQ(sineWaveNode.read(data.data(), 96000 * 2), 96000 * 2);
  EXPECT_EQ(sineWaveNode.getState().state, AudioGraphNodeState::FINISHED);
  for (int i = 0; i < 96000; i++) {
    const auto value =
        floor(8192 * sin(2 * M_PI * static_cast<double>(frequency) *
                         floor(i / 2) / 48000.0));
    EXPECT_EQ(data[i], value);
  }
}

TEST_F(SineWaveNodeTest, seekTo) {
  SineWaveNode sineWaveNode(frequency, duration);
  auto state = waitForStatus(sineWaveNode, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(sineWaveNode.seekTo(state.streamInfo.value().totalSamples / 2),
            state.streamInfo.value().totalSamples / 2);
  state = waitForStatus(sineWaveNode, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.position, state.streamInfo.value().totalSamples / 2);
  std::vector<int16_t> data(20);
  EXPECT_EQ(sineWaveNode.read(data.data(), 40), 40);
  for (int i = 0; i < 20; i++) {
    const auto value =
        floor(8192 * sin(2 * M_PI * static_cast<double>(frequency) *
                         floor((i + 48000) / 2) / 48000.0));
    EXPECT_EQ(data[i], value);
  }
}

TEST_F(SineWaveNodeTest, seekToEnd) {
  SineWaveNode sineWaveNode(frequency, duration);
  auto state = waitForStatus(sineWaveNode, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(sineWaveNode.seekTo(state.streamInfo.value().totalSamples),
            state.streamInfo.value().totalSamples);
  EXPECT_EQ(sineWaveNode.getState().state, AudioGraphNodeState::FINISHED);
}

TEST_F(SineWaveNodeTest, seekToEnd_and_back) {
  SineWaveNode sineWaveNode(frequency, duration);
  auto state = waitForStatus(sineWaveNode, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(sineWaveNode.seekTo(state.streamInfo.value().totalSamples),
            state.streamInfo.value().totalSamples);
  EXPECT_EQ(sineWaveNode.getState().state, AudioGraphNodeState::FINISHED);
  EXPECT_EQ(sineWaveNode.seekTo(0), 0);
  state = waitForStatus(sineWaveNode, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(state.position, 0);
}