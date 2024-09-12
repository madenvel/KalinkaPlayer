#include "FileInputNode.h"
#include "FlacStreamDecoder.h"

#include <gtest/gtest.h>
#include <memory>

#include "ErrorFakeNode.h"
#include "TestHelpers.h"

class FlacStreamDecoderTest : public ::testing::Test {
protected:
  static constexpr size_t bufferSize = 16384;

  void SetUp() override {}
};

TEST_F(FlacStreamDecoderTest, constructor_destructor) {}

TEST_F(FlacStreamDecoderTest, connectTo) {
  auto flacStreamDecoder = std::make_shared<FlacStreamDecoder>(bufferSize);
  auto inputNode = std::make_shared<FileInputNode>("files/tone440.flac");
  flacStreamDecoder->connectTo(inputNode);
}

TEST_F(FlacStreamDecoderTest, connectTo_nullptr) {
  auto flacStreamDecoder = std::make_shared<FlacStreamDecoder>(bufferSize);
  EXPECT_THROW(flacStreamDecoder->connectTo(nullptr), std::runtime_error);
}

TEST_F(FlacStreamDecoderTest, disconnect) {
  auto flacStreamDecoder = std::make_shared<FlacStreamDecoder>(bufferSize);
  auto inputNode = std::make_shared<FileInputNode>("files/tone440.flac");
  flacStreamDecoder->connectTo(inputNode);
  std::this_thread::sleep_for(std::chrono::milliseconds(500));
  flacStreamDecoder->disconnect(inputNode);
}

TEST_F(FlacStreamDecoderTest, read) {
  auto flacStreamDecoder = std::make_shared<FlacStreamDecoder>(bufferSize);
  auto inputNode = std::make_shared<FileInputNode>("files/tone440.flac");
  flacStreamDecoder->connectTo(inputNode);
  uint8_t data[bufferSize];
  waitForStatus(*flacStreamDecoder, AudioGraphNodeState::STREAMING);
  std::this_thread::sleep_for(std::chrono::milliseconds(1000));
  EXPECT_EQ(flacStreamDecoder->read(data, 100), 100);
  flacStreamDecoder->disconnect(inputNode);
}

TEST_F(FlacStreamDecoderTest, getStreamInfo) {
  auto flacStreamDecoder = std::make_shared<FlacStreamDecoder>(bufferSize);
  auto inputNode = std::make_shared<FileInputNode>("files/tone440.flac");
  flacStreamDecoder->connectTo(inputNode);
  auto state =
      waitForStatus(*flacStreamDecoder, AudioGraphNodeState::STREAMING);
  ASSERT_TRUE(state.streamInfo.has_value());
  auto audioInfo = state.streamInfo.value();
  EXPECT_EQ(audioInfo.format.sampleRate, 44100);
  EXPECT_EQ(audioInfo.format.channels, 2);
  EXPECT_EQ(audioInfo.format.bitsPerSample, 16);
  EXPECT_EQ(audioInfo.totalSamples, 44100);
}

TEST_F(FlacStreamDecoderTest, getState) {
  auto flacStreamDecoder = std::make_shared<FlacStreamDecoder>(bufferSize);
  auto inputNode = std::make_shared<FileInputNode>("files/tone440.flac");
  ASSERT_EQ(flacStreamDecoder->getState().state, AudioGraphNodeState::STOPPED);
  flacStreamDecoder->connectTo(inputNode);
  EXPECT_EQ(flacStreamDecoder->getState().state,
            AudioGraphNodeState::PREPARING);
  waitForStatus(*flacStreamDecoder, AudioGraphNodeState::STREAMING);
  EXPECT_EQ(flacStreamDecoder->getState().state,
            AudioGraphNodeState::STREAMING);
  uint8_t data[1000];
  int totalSize = 0;
  int num = 0;
  do {
    flacStreamDecoder->waitForData();
    num = flacStreamDecoder->read(data, 1000);
    totalSize += num;
  } while (num > 0 && flacStreamDecoder->getState().state !=
                          AudioGraphNodeState::FINISHED);
  EXPECT_EQ(flacStreamDecoder->getState().state, AudioGraphNodeState::FINISHED);
  EXPECT_EQ(totalSize, 44100 * 4);
  flacStreamDecoder->disconnect(inputNode);
  EXPECT_EQ(flacStreamDecoder->getState().state, AudioGraphNodeState::STOPPED);
}

TEST_F(FlacStreamDecoderTest, stream_error) {
  auto flacStreamDecoder = std::make_shared<FlacStreamDecoder>(bufferSize);
  auto inputNode = std::make_shared<ErrorFakeNode>();
  flacStreamDecoder->connectTo(inputNode);
  std::this_thread::sleep_for(std::chrono::milliseconds(500));
  auto streamState = flacStreamDecoder->getState();
  EXPECT_EQ(streamState.state, AudioGraphNodeState::ERROR);
  EXPECT_EQ(streamState.message, "Fake error message");
}
