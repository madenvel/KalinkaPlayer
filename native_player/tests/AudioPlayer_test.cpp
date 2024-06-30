#include "AudioGraphNode.h"
#include "AudioPlayer.h"
#include "StateMonitor.h"
#include "StreamState.h"

#include <gtest/gtest.h>

class AudioPlayerTest : public ::testing::Test {
protected:
  const std::string url1 =
      "https://getsamplefiles.com/download/flac/sample-3.flac";
  const std::string url2 =
      "https://getsamplefiles.com/download/flac/sample-4.flac";
  const std::string url3 =
      "https://getsamplefiles.com/download/flac/sample-2.flac";

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

TEST_F(AudioPlayerTest, play_one_after_another) {
  auto monitor = audioPlayer.monitor();
  audioPlayer.play(url3);
  std::this_thread::sleep_for(std::chrono::seconds(1));
  audioPlayer.play(url2);
  std::this_thread::sleep_for(std::chrono::seconds(1));
  audioPlayer.play(url1);
  while (audioPlayer.getState().state != AudioGraphNodeState::FINISHED) {
    std::this_thread::sleep_for(std::chrono::seconds(1));
  }

  AudioGraphNodeState states[] = {
      AudioGraphNodeState::STOPPED,
      AudioGraphNodeState::SOURCE_CHANGED,
      AudioGraphNodeState::PREPARING,
      AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::SOURCE_CHANGED, /*AudioGraphNodeState::PREPARING,*/
      AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::SOURCE_CHANGED,
      /*AudioGraphNodeState::PREPARING, */ AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::FINISHED};

  int i = 0;
  while (monitor->hasData()) {
    auto state = monitor->waitState();
    ASSERT_LT(i, sizeof(states) / sizeof(states[0]));
    EXPECT_EQ(state.state, states[i++]);
  }
  EXPECT_EQ(i, sizeof(states) / sizeof(states[0]));
}

TEST_F(AudioPlayerTest, test_play_next_then_play) {
  auto monitor = audioPlayer.monitor();
  audioPlayer.playNext(url1);
  std::this_thread::sleep_for(std::chrono::seconds(1));
  audioPlayer.playNext(url3);
  std::this_thread::sleep_for(std::chrono::seconds(1));
  audioPlayer.play(url2);

  while (audioPlayer.getState().state != AudioGraphNodeState::FINISHED) {
    std::this_thread::sleep_for(std::chrono::milliseconds(1000));
  }

  AudioGraphNodeState states[] = {
      AudioGraphNodeState::STOPPED,        AudioGraphNodeState::SOURCE_CHANGED,
      AudioGraphNodeState::PREPARING,      AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::SOURCE_CHANGED, AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::FINISHED};

  int i = 0;
  while (monitor->hasData()) {
    auto state = monitor->waitState();
    ASSERT_LT(i, sizeof(states) / sizeof(states[0]));
    EXPECT_EQ(state.state, states[i++]);
  }
  EXPECT_EQ(i, sizeof(states) / sizeof(states[0]));
}