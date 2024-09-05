#include "FileInputNode.h"

#include "TestHelpers.h"
#include <gtest/gtest.h>

class FileInputNodeTest : public ::testing::Test {
protected:
  const std::string filename = "files/tone440.flac";
};

TEST_F(FileInputNodeTest, constructor_destructor) {
  FileInputNode fileInputNode(filename);
  EXPECT_EQ(fileInputNode.getState().state, AudioGraphNodeState::STREAMING);
}

TEST_F(FileInputNodeTest, read) {
  FileInputNode fileInputNode(filename);
  auto state = waitForStatus(fileInputNode, AudioGraphNodeState::STREAMING);
  size_t fileSize = state.streamInfo.value().totalSamples;
  uint8_t data[100];
  int bytesRead = 0;
  int num = 0;
  do {
    num = fileInputNode.read(data, 100);
    bytesRead += num;
  } while (num != 0);
  EXPECT_EQ(fileInputNode.getState().state, AudioGraphNodeState::FINISHED);
  EXPECT_EQ(bytesRead, fileSize);
}

TEST_F(FileInputNodeTest, seek) {
  FileInputNode fileInputNode(filename);
  auto state = waitForStatus(fileInputNode, AudioGraphNodeState::STREAMING);
  size_t fileSize = state.streamInfo.value().totalSamples;
  uint8_t data[100];
  int bytesRead = 0;
  int num = 0;
  do {
    num = fileInputNode.read(data, 100);
    bytesRead += num;
  } while (num != 0);
  EXPECT_EQ(fileInputNode.getState().state, AudioGraphNodeState::FINISHED);
  EXPECT_EQ(bytesRead, fileSize);

  EXPECT_EQ(fileInputNode.seekTo(0), 0);
  bytesRead = 0;
  do {
    num = fileInputNode.read(data, 100);
    bytesRead += num;
  } while (num != 0);
  EXPECT_EQ(fileInputNode.getState().state, AudioGraphNodeState::FINISHED);
  EXPECT_EQ(bytesRead, fileSize);
}

TEST_F(FileInputNodeTest, seekToEnd) {
  FileInputNode fileInputNode(filename);
  auto state = waitForStatus(fileInputNode, AudioGraphNodeState::STREAMING);
  size_t fileSize = state.streamInfo.value().totalSamples;
  EXPECT_EQ(fileInputNode.seekTo(fileSize), fileSize);
  EXPECT_EQ(fileInputNode.getState().state, AudioGraphNodeState::FINISHED);
}

TEST_F(FileInputNodeTest, seekPastEnd) {
  FileInputNode fileInputNode(filename);
  auto state = waitForStatus(fileInputNode, AudioGraphNodeState::STREAMING);
  size_t fileSize = state.streamInfo.value().totalSamples;
  EXPECT_EQ(fileInputNode.seekTo(fileSize + 1), fileSize);
  EXPECT_EQ(fileInputNode.getState().state, AudioGraphNodeState::FINISHED);
}