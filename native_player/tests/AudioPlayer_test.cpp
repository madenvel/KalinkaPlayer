#include "AudioGraphNode.h"
#include "AudioPlayer.h"
#include "Config.h"
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

  Config config = {{"input.http.buffer_size", "768000"},
                   {"input.http.chunk_size", "384000"},
                   {"decoder.flac.buffer_size", "1536000"},
                   {"output.alsa.device", "hw:0,0"},
                   {"output.alsa.buffer_size", "16384"},
                   {"output.alsa.period_size", "1024"}};

  AudioPlayer audioPlayer;

  AudioPlayerTest() : audioPlayer(config) {}
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
      AudioGraphNodeState::STOPPED,        AudioGraphNodeState::SOURCE_CHANGED,
      AudioGraphNodeState::PREPARING,      AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::SOURCE_CHANGED, AudioGraphNodeState::PREPARING,
      AudioGraphNodeState::STREAMING,      AudioGraphNodeState::SOURCE_CHANGED,
      AudioGraphNodeState::PREPARING,      AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::FINISHED};

  int i = 0;
  while (monitor->hasData()) {
    auto state = monitor->waitState();
    ASSERT_LT(i, sizeof(states) / sizeof(states[0]));
    EXPECT_EQ(state.state, states[i++]) << "i=" << (i - 1);
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
      AudioGraphNodeState::SOURCE_CHANGED, AudioGraphNodeState::PREPARING,
      AudioGraphNodeState::STREAMING,      AudioGraphNodeState::FINISHED};

  int i = 0;
  while (monitor->hasData()) {
    auto state = monitor->waitState();
    ASSERT_LT(i, sizeof(states) / sizeof(states[0]));
    EXPECT_EQ(state.state, states[i++]) << "i=" << (i - 1);
  }
  EXPECT_EQ(i, sizeof(states) / sizeof(states[0]));
}

TEST_F(AudioPlayerTest, test_play_pause_stop_play) {
  auto monitor = audioPlayer.monitor();
  audioPlayer.play(url1);
  std::this_thread::sleep_for(std::chrono::seconds(2));
  audioPlayer.stop();
  std::this_thread::sleep_for(std::chrono::seconds(2));
  audioPlayer.play(url2);

  while (audioPlayer.getState().state != AudioGraphNodeState::FINISHED) {
    std::this_thread::sleep_for(std::chrono::milliseconds(1000));
  }

  AudioGraphNodeState states[] = {
      AudioGraphNodeState::STOPPED,   AudioGraphNodeState::SOURCE_CHANGED,
      AudioGraphNodeState::PREPARING, AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::STOPPED,   AudioGraphNodeState::SOURCE_CHANGED,
      AudioGraphNodeState::PREPARING, AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::FINISHED};

  int i = 0;
  while (monitor->hasData()) {
    auto state = monitor->waitState();
    ASSERT_LT(i, sizeof(states) / sizeof(states[0]));
    EXPECT_EQ(state.state, states[i++]);
  }
  EXPECT_EQ(i, sizeof(states) / sizeof(states[0]));
}

TEST_F(AudioPlayerTest, seek_forward) {
  auto monitor = audioPlayer.monitor();
  audioPlayer.play(url3);
  std::this_thread::sleep_for(std::chrono::seconds(4));
  audioPlayer.seek(6000);

  std::this_thread::sleep_for(std::chrono::milliseconds(2000));

  AudioGraphNodeState states[] = {
      AudioGraphNodeState::STOPPED,   AudioGraphNodeState::SOURCE_CHANGED,
      AudioGraphNodeState::PREPARING, AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::PREPARING, AudioGraphNodeState::STREAMING};

  int i = 0;
  while (monitor->hasData()) {
    auto state = monitor->waitState();
    ASSERT_LT(i, sizeof(states) / sizeof(states[0]));
    EXPECT_EQ(state.state, states[i++]);

    if (i == 6) {
      EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
      EXPECT_NEAR(state.position, 6000, 10);
    }
  }

  EXPECT_EQ(i, sizeof(states) / sizeof(states[0]));
}

TEST_F(AudioPlayerTest, seek_backward) {
  auto monitor = audioPlayer.monitor();
  audioPlayer.play(url3);
  std::this_thread::sleep_for(std::chrono::seconds(4));
  audioPlayer.seek(0);

  std::this_thread::sleep_for(std::chrono::milliseconds(2000));

  AudioGraphNodeState states[] = {
      AudioGraphNodeState::STOPPED,   AudioGraphNodeState::SOURCE_CHANGED,
      AudioGraphNodeState::PREPARING, AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::PREPARING, AudioGraphNodeState::STREAMING};

  int i = 0;
  while (monitor->hasData()) {
    auto state = monitor->waitState();
    ASSERT_LT(i, sizeof(states) / sizeof(states[0]));
    EXPECT_EQ(state.state, states[i++]);

    if (i == 6) {
      EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
      EXPECT_NEAR(state.position, 0, 10);
    }
  }

  EXPECT_EQ(i, sizeof(states) / sizeof(states[0]));
}

TEST_F(AudioPlayerTest, seek_one_after_another) {
  auto monitor = audioPlayer.monitor();
  audioPlayer.play(url3);

  std::this_thread::sleep_for(std::chrono::seconds(4));
  EXPECT_EQ(audioPlayer.seek(5000), 5000);
  EXPECT_EQ(audioPlayer.seek(500), 500);

  std::this_thread::sleep_for(std::chrono::seconds(4));

  AudioGraphNodeState states[] = {
      AudioGraphNodeState::STOPPED,   AudioGraphNodeState::SOURCE_CHANGED,
      AudioGraphNodeState::PREPARING, AudioGraphNodeState::STREAMING,
      AudioGraphNodeState::PREPARING, AudioGraphNodeState::STREAMING};

  int i = 0;
  while (monitor->hasData()) {
    auto state = monitor->waitState();
    ASSERT_LT(i, sizeof(states) / sizeof(states[0]));
    EXPECT_EQ(state.state, states[i++]);

    if (i == 6) {
      EXPECT_EQ(state.state, AudioGraphNodeState::STREAMING);
      EXPECT_NEAR(state.position, 0, 500);
    }
  }

  EXPECT_EQ(i, sizeof(states) / sizeof(states[0]));
}