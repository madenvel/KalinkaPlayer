#include "AudioSampleFormat.h"

#include <gtest/gtest.h>
#include <vector>

TEST(AudioSampleFormatTest, ConvertPCM16ToPCM16) {
  std::vector<int16_t> sourceSamples = {1000, -1000, 32767, -32768};
  std::vector<int16_t> destSamples(sourceSamples.size());

  size_t convertedSamples = convertSampleFormat(
      sourceSamples.data(), AudioSampleFormat::PCM16_LE, sourceSamples.size(),
      destSamples.data(), AudioSampleFormat::PCM16_LE,
      destSamples.size() * sizeof(int16_t));

  ASSERT_EQ(convertedSamples, sourceSamples.size());
  for (size_t i = 0; i < sourceSamples.size(); ++i) {
    EXPECT_EQ(sourceSamples[i], destSamples[i]) << "i = " << i;
  }
}

TEST(AudioSampleFormatTest, ConvertPCM24ToPCM24) {
  std::vector<int32_t> sourceSamples = {1000000, -1000000, 8388607, -8388608};
  std::vector<int32_t> destSamples(sourceSamples.size());

  size_t convertedSamples = convertSampleFormat(
      sourceSamples.data(), AudioSampleFormat::PCM24_LE, sourceSamples.size(),
      destSamples.data(), AudioSampleFormat::PCM24_LE,
      destSamples.size() * sizeof(int32_t));

  ASSERT_EQ(convertedSamples, sourceSamples.size());
  for (size_t i = 0; i < sourceSamples.size(); ++i) {
    EXPECT_EQ(sourceSamples[i] & 0xFFFFFF, destSamples[i] & 0xFFFFFF)
        << "i = " << i;
  }
}

TEST(AudioSampleFormatTest, ConvertPCM32ToPCM32) {
  std::vector<int32_t> sourceSamples = {100000000, -100000000, 2147483392,
                                        -2147483648};
  std::vector<int32_t> destSamples(sourceSamples.size());

  size_t convertedSamples = convertSampleFormat(
      sourceSamples.data(), AudioSampleFormat::PCM32_LE, sourceSamples.size(),
      destSamples.data(), AudioSampleFormat::PCM32_LE,
      destSamples.size() * sizeof(int32_t));

  ASSERT_EQ(convertedSamples, sourceSamples.size());
  for (size_t i = 0; i < sourceSamples.size(); ++i) {
    EXPECT_EQ(sourceSamples[i], destSamples[i]) << "i = " << i;
  }
}

TEST(AudioSampleFormatTest, ConvertPCM16ToPCM24) {
  std::vector<int16_t> sourceSamples = {1000, -1000, 32767, -32768};
  std::vector<int32_t> destSamples(sourceSamples.size());
  const std::vector<int32_t> expectedSamples = {256000, 16521216, 8388352,
                                                8388608};

  size_t convertedSamples = convertSampleFormat(
      sourceSamples.data(), AudioSampleFormat::PCM16_LE, sourceSamples.size(),
      destSamples.data(), AudioSampleFormat::PCM24_LE,
      destSamples.size() * sizeof(int32_t));

  ASSERT_EQ(convertedSamples, sourceSamples.size());
  for (size_t i = 0; i < sourceSamples.size(); ++i) {
    EXPECT_EQ(destSamples[i], expectedSamples[i]) << "i = " << i;
  }
}

TEST(AudioSampleFormatTest, ConvertPCM24ToPCM32) {
  std::vector<int32_t> sourceSamples = {1000000, 0x1000000 - 1000000, 8388607,
                                        0x1000000 - 8388608};
  std::vector<int32_t> destSamples(sourceSamples.size());
  const std::vector<int32_t> expectedSamples = {
      256000000, (0x1000000 - 1000000) << 8, 2147483392, (int32_t)0x80000000u};

  size_t convertedSamples = convertSampleFormat(
      sourceSamples.data(), AudioSampleFormat::PCM24_LE, sourceSamples.size(),
      destSamples.data(), AudioSampleFormat::PCM32_LE,
      destSamples.size() * sizeof(int32_t));

  ASSERT_EQ(convertedSamples, sourceSamples.size());
  for (size_t i = 0; i < sourceSamples.size(); ++i) {
    EXPECT_EQ(destSamples[i], expectedSamples[i]) << "i = " << i;
  }
}

TEST(AudioSampleFormatTest, ConvertPCM32ToPCM24) {
  std::vector<int32_t> sourceSamples = {256000000, (0x1000000 - 1000000) << 8,
                                        2147483392, (int32_t)0x80000000u};
  std::vector<int32_t> destSamples(sourceSamples.size());
  const std::vector<int32_t> expectedSamples = {1000000, 0x1000000 - 1000000,
                                                8388607, 0x1000000 - 8388608};

  size_t convertedSamples = convertSampleFormat(
      sourceSamples.data(), AudioSampleFormat::PCM32_LE, sourceSamples.size(),
      destSamples.data(), AudioSampleFormat::PCM24_LE,
      destSamples.size() * sizeof(int32_t));

  ASSERT_EQ(convertedSamples, sourceSamples.size());
  for (size_t i = 0; i < convertedSamples; ++i) {
    EXPECT_EQ(destSamples[i], expectedSamples[i]) << "i = " << i;
  }
}

TEST(AudioSampleFormatTest, ConvertPCM24ToPCM24_3) {
  std::vector<int32_t> sourceSamples = {1000000, 0x1000000 - 1000000, 8388607,
                                        0x1000000 - 8388608};
  std::vector<uint8_t> destBytes(sourceSamples.size() * 3);
  const std::vector<uint8_t> expectedBytes = {
      0x40, 0x42, 0x0f, 0xc0, 0xbd, 0xf0, 0xff, 0xff, 0x7f, 0x00, 0x00, 0x80};

  size_t convertedSamples = convertSampleFormat(
      sourceSamples.data(), AudioSampleFormat::PCM24_LE, sourceSamples.size(),
      destBytes.data(), AudioSampleFormat::PCM24_3LE, destBytes.size());

  ASSERT_EQ(convertedSamples, expectedBytes.size() / 3);
  for (size_t i = 0; i < convertedSamples * 3; ++i) {
    EXPECT_EQ(destBytes[i], expectedBytes[i]) << "i = " << i;
  }
}

TEST(AudioSampleFormatTest, ConvertPCM24_3ToPCM24) {
  std::vector<uint8_t> sourceBytes = {0x40, 0x42, 0x0f, 0xc0, 0xbd, 0xf0,
                                      0xff, 0xff, 0x7f, 0x00, 0x00, 0x80};
  const std::vector<int32_t> expectedSamples = {1000000, 0x1000000 - 1000000,
                                                8388607, 0x1000000 - 8388608};
  std::vector<int32_t> destSamples(expectedSamples.size());

  size_t convertedSamples = convertSampleFormat(
      sourceBytes.data(), AudioSampleFormat::PCM24_3LE, sourceBytes.size() / 3,
      destSamples.data(), AudioSampleFormat::PCM24_LE,
      destSamples.size() * sizeof(int32_t));

  ASSERT_EQ(convertedSamples, expectedSamples.size());
  for (size_t i = 0; i < convertedSamples; ++i) {
    EXPECT_EQ(destSamples[i], expectedSamples[i]) << "i = " << i;
  }
}

TEST(AudioSampleFormatTest, DestinationBufferCannoFitAllSamples) {
  std::vector<int16_t> sourceSamples = {1000, -1000, 32767, -32768};
  std::vector<int16_t> destSamples(sourceSamples.size() - 1);

  size_t convertedSamples = convertSampleFormat(
      sourceSamples.data(), AudioSampleFormat::PCM16_LE, sourceSamples.size(),
      destSamples.data(), AudioSampleFormat::PCM16_LE,
      destSamples.size() * sizeof(int16_t));

  ASSERT_EQ(convertedSamples, destSamples.size());
  for (size_t i = 0; i < destSamples.size(); ++i) {
    EXPECT_EQ(sourceSamples[i], destSamples[i]) << "i = " << i;
  }
}