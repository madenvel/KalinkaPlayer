#include "AudioGraphHttpStream.h"

#include <gtest/gtest.h>
#include <memory>

#include "TestHelpers.h"

#include "Log.h"

class AudioGraphHttpStreamTest : public ::testing::Test {
protected:
  const std::string url =
      "https://getsamplefiles.com/download/flac/sample-2.flac";
  const size_t bufferSize = 32768;

  void SetUp() override {}
};

TEST_F(AudioGraphHttpStreamTest, constructor_destructor) {
  auto audioGraphHttpStream =
      std::make_shared<AudioGraphHttpStream>(url, bufferSize);
}

TEST_F(AudioGraphHttpStreamTest, read) {
  auto audioGraphHttpStream =
      std::make_shared<AudioGraphHttpStream>(url, bufferSize);
  std::shared_ptr<uint8_t[]> data = std::make_shared<uint8_t[]>(bufferSize);
  size_t bytesToRead = 0;
  size_t totalBytesRead = 0;
  while ((bytesToRead =
              audioGraphHttpStream->waitForData(std::stop_token(), 1)) != 0) {
    audioGraphHttpStream->read(data.get(), bytesToRead);
    totalBytesRead += bytesToRead;
  }
  EXPECT_EQ(audioGraphHttpStream->getState().state,
            AudioGraphNodeState::FINISHED);
  EXPECT_EQ(totalBytesRead, 11314580);
}

TEST_F(AudioGraphHttpStreamTest, test_broken_url_set_error_status) {
  auto audioGraphHttpStream = std::make_shared<AudioGraphHttpStream>(
      "http://httpstat.us/404", bufferSize);
  std::shared_ptr<uint8_t[]> data = std::make_shared<uint8_t[]>(bufferSize);
  size_t bytesToRead = 0;
  size_t totalBytesRead = 0;
  while ((bytesToRead =
              audioGraphHttpStream->waitForData(std::stop_token(), 1)) != 0) {
    audioGraphHttpStream->read(data.get(), bytesToRead);
    totalBytesRead += bytesToRead;
  }
  EXPECT_EQ(audioGraphHttpStream->getState().state, AudioGraphNodeState::ERROR);
  EXPECT_EQ(totalBytesRead, 0);
}

TEST_F(AudioGraphHttpStreamTest, seekTo_forward) {
  auto audioGraphHttpStream =
      std::make_shared<AudioGraphHttpStream>(url, bufferSize);
  std::shared_ptr<uint8_t[]> data = std::make_shared<uint8_t[]>(bufferSize);
  auto state =
      waitForStatus(*audioGraphHttpStream, AudioGraphNodeState::STREAMING);
  auto contentLength = state.streamInfo.value().totalSamples;
  spdlog::info("Content length: {}", contentLength);
  size_t bytesToRead = 0;
  size_t totalBytesRead = 0;
  size_t halfContent = contentLength / 2;
  EXPECT_GT(contentLength, 0);
  while ((bytesToRead =
              audioGraphHttpStream->waitForData(std::stop_token(), 1)) != 0) {
    size_t sizeToRead = std::min(halfContent - totalBytesRead, bytesToRead);
    audioGraphHttpStream->read(data.get(), sizeToRead);
    totalBytesRead += sizeToRead;

    if (totalBytesRead == halfContent) {
      break;
    };
  }

  audioGraphHttpStream->seekTo(halfContent + halfContent / 2);

  while ((bytesToRead =
              audioGraphHttpStream->waitForData(std::stop_token(), 1)) != 0) {
    audioGraphHttpStream->read(data.get(), bytesToRead);
    totalBytesRead += bytesToRead;
  }

  EXPECT_EQ(audioGraphHttpStream->getState().state,
            AudioGraphNodeState::FINISHED);
  EXPECT_EQ(totalBytesRead, contentLength - halfContent / 2);
}

TEST_F(AudioGraphHttpStreamTest, seekTo_backward) {
  auto audioGraphHttpStream =
      std::make_shared<AudioGraphHttpStream>(url, bufferSize);
  std::shared_ptr<uint8_t[]> data = std::make_shared<uint8_t[]>(bufferSize);
  auto state =
      waitForStatus(*audioGraphHttpStream, AudioGraphNodeState::STREAMING);
  auto contentLength = state.streamInfo.value().totalSamples;
  size_t bytesToRead = 0;
  size_t totalBytesRead = 0;
  size_t halfContent = contentLength / 2;
  EXPECT_GT(contentLength, 0);
  while ((bytesToRead =
              audioGraphHttpStream->waitForData(std::stop_token(), 1)) != 0) {
    size_t sizeToRead = std::min(halfContent - totalBytesRead, bytesToRead);
    audioGraphHttpStream->read(data.get(), sizeToRead);
    totalBytesRead += sizeToRead;

    if (totalBytesRead == halfContent) {
      break;
    };
  }

  audioGraphHttpStream->seekTo(halfContent / 2);

  while ((bytesToRead =
              audioGraphHttpStream->waitForData(std::stop_token(), 1)) != 0) {
    audioGraphHttpStream->read(data.get(), bytesToRead);
    totalBytesRead += bytesToRead;
  }

  EXPECT_EQ(audioGraphHttpStream->getState().state,
            AudioGraphNodeState::FINISHED);
  EXPECT_EQ(totalBytesRead, contentLength + halfContent / 2);
}