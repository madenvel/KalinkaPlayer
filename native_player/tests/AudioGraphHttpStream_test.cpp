#include "AudioGraphHttpStream.h"

#include <gtest/gtest.h>
#include <memory>

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
