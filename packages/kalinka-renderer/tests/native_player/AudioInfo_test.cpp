#include "AudioInfo.h"

#include <gtest/gtest.h>

namespace {

StreamInfo pcm16Stereo(StreamType type, unsigned long size,
                       unsigned int sampleRate = 44100) {
  StreamInfo info;
  info.format =
      StreamAudioFormat{sampleRate, 2, 16, AudioSampleFormat::PCM16_LE};
  info.streamType = type;
  info.streamSize = size;
  return info;
}

} // namespace

TEST(AudioInfoTest, FramesDivideByTheSampleRate) {
  const auto duration = pcm16Stereo(StreamType::FRAMES, 9876543).durationMs();

  ASSERT_TRUE(duration.has_value());
  EXPECT_EQ(*duration, 223957u);
}

TEST(AudioInfoTest, BytesDivideOutTheFrameSize) {
  const auto duration =
      pcm16Stereo(StreamType::BYTES, 9876543 * 4).durationMs();

  ASSERT_TRUE(duration.has_value());
  EXPECT_EQ(*duration, 223957u);
}

TEST(AudioInfoTest, APartialMillisecondIsNotCountedTwice) {
  const auto duration = pcm16Stereo(StreamType::FRAMES, 44122).durationMs();

  ASSERT_TRUE(duration.has_value());
  EXPECT_EQ(*duration, 1000u);
}

TEST(AudioInfoTest, AnUnstatedLengthIsUnknownRatherThanEmpty) {
  EXPECT_FALSE(pcm16Stereo(StreamType::FRAMES, 0).durationMs().has_value());
}

TEST(AudioInfoTest, ALengthWithNoSampleRateToDivideByIsUnknown) {
  EXPECT_FALSE(
      pcm16Stereo(StreamType::FRAMES, 9876543, 0).durationMs().has_value());
}

TEST(AudioInfoTest, AByteCountWithNoFrameSizeToDivideByIsUnknown) {
  StreamInfo info = pcm16Stereo(StreamType::BYTES, 9876543);
  info.format.channels = 0;

  EXPECT_FALSE(info.durationMs().has_value());
}

TEST(AudioInfoTest, ALongStreamOutgrowsThe32BitFrameCountItCameFrom) {
  // 4e9 frames * 1000 overflows 32 bits, where streamSize itself does not.
  const auto duration =
      pcm16Stereo(StreamType::FRAMES, 4000000000UL, 192000).durationMs();

  ASSERT_TRUE(duration.has_value());
  EXPECT_EQ(*duration, 20833333u);
}
