#include "AudioGraphNode.h"
#include "AudioPlayer.h"

#include <gtest/gtest.h>

class AudioPlayerTest : public ::testing::Test {
protected:
  const std::string url1 =
      "https://getsamplefiles.com/download/flac/sample-3.flac";
  const std::string url2 =
      "https://getsamplefiles.com/download/flac/sample-4.flac";
  AudioPlayer audioPlayer;

  AudioPlayerTest() : audioPlayer("hw:0,0") {}
};

TEST_F(AudioPlayerTest, constructor_destructor) {}

TEST_F(AudioPlayerTest, play) {
  audioPlayer.play(url1);
  std::this_thread::sleep_for(std::chrono::seconds(4));
}

TEST_F(AudioPlayerTest, playNext) {
  audioPlayer.playNext(url1);
  audioPlayer.playNext(url2);
  while (audioPlayer.getState().state != AudioGraphNodeState::FINISHED) {
    std::this_thread::sleep_for(std::chrono::milliseconds(1000));
  }
}

TEST_F(AudioPlayerTest, monitor) {
  auto monitor = audioPlayer.monitor();
  auto state = monitor->waitState();
  EXPECT_EQ(state.state, AudioGraphNodeState::STOPPED);
}