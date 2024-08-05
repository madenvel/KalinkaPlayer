#include "FileInputNode.h"

#include <gtest/gtest.h>

class FileInputNodeTest : public ::testing::Test {
protected:
  const std::string filename = "files/tone440.flac";
  size_t fileSize = 0;

  void SetUp() override {
    std::ifstream file(filename, std::ios::binary | std::ios::ate);
    fileSize = file.tellg();
    file.close();
  }
};

TEST_F(FileInputNodeTest, constructor_destructor) {
  FileInputNode fileInputNode(filename);
  EXPECT_EQ(fileInputNode.getState().state, AudioGraphNodeState::STREAMING);
}

TEST_F(FileInputNodeTest, read) {
  FileInputNode fileInputNode(filename);
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