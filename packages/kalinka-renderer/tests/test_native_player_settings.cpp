#include <gtest/gtest.h>
#include <stdlib.h>

#include <boost/asio.hpp>
#include <filesystem>
#include <memory>
#include <string>

#include "config/SettingsPersistence.h"
#include "player/NativePlayer.h"

namespace fs = std::filesystem;
namespace pb = kalinka::renderer::v1;

namespace {

// The settings the graph is built with, as the config plane sees them. A temp
// prefix keeps the overrides file off the real machine, and the null device
// keeps the graph off its sound card.
class NativePlayerSettingsTest : public ::testing::Test {
protected:
  void SetUp() override {
    prefix_ = fs::temp_directory_path() /
              ("kalinka-player-settings-" + std::to_string(::getpid()));
    fs::create_directories(prefix_);
    setenv("KALINKA_PREFIX", prefix_.c_str(), 1);
    saveSettingsOverrides({{"output.device", "null"}});
    player_ = std::make_shared<NativePlayer>(ioc_);
  }

  void TearDown() override {
    player_.reset();
    unsetenv("KALINKA_PREFIX");
    std::error_code ec;
    fs::remove_all(prefix_, ec);
  }

  static const pb::ConfigField *field(const pb::ConfigSection &section,
                                      const std::string &path) {
    for (const pb::ConfigField &candidate : section.fields()) {
      if (candidate.path() == path) {
        return &candidate;
      }
    }
    return nullptr;
  }

  pb::ConfigSection output() const {
    pb::ConfigSection section;
    player_->fillConfig(section);
    return section;
  }

  pb::ConfigSection buffers() const {
    pb::ConfigSection section;
    player_->bufferSettings()->fillConfig(section);
    return section;
  }

  std::string error_;
  fs::path prefix_;
  boost::asio::io_context ioc_;
  std::shared_ptr<NativePlayer> player_;
};

TEST_F(NativePlayerSettingsTest, TheSinkIsBufferedAsTheServerUsedToBufferIt) {
  const pb::ConfigSection section = output();

  const pb::ConfigField *latency = field(section, "output.latency_ms");
  ASSERT_NE(latency, nullptr);
  EXPECT_EQ(latency->value(), "160");
  EXPECT_EQ(latency->default_value(), "160");
  EXPECT_EQ(latency->type(), pb::CONFIG_FIELD_TYPE_INT);
  EXPECT_EQ(latency->apply(), pb::APPLY_COST_INTERRUPTS_PLAYBACK);

  const pb::ConfigField *period = field(section, "output.period_ms");
  ASSERT_NE(period, nullptr);
  EXPECT_EQ(period->value(), "40");
  EXPECT_EQ(period->default_value(), "40");
}

TEST_F(NativePlayerSettingsTest, OnlyWhatAUserPicksIsOnThePageProper) {
  const pb::ConfigSection section = output();

  EXPECT_EQ(field(section, "output.device")->importance(),
            pb::CONFIG_IMPORTANCE_SIMPLE);
  EXPECT_EQ(field(section, "output.volume_mode")->importance(),
            pb::CONFIG_IMPORTANCE_SIMPLE);
  EXPECT_EQ(field(section, "output.driver")->importance(),
            pb::CONFIG_IMPORTANCE_EXPERT);
  EXPECT_EQ(field(section, "output.latency_ms")->importance(),
            pb::CONFIG_IMPORTANCE_EXPERT);
  for (const pb::ConfigField &knob : buffers().fields()) {
    EXPECT_EQ(knob.importance(), pb::CONFIG_IMPORTANCE_EXPERT) << knob.path();
  }
}

TEST_F(NativePlayerSettingsTest, BufferingIsItsOwnSection) {
  const pb::ConfigSection section = buffers();

  EXPECT_EQ(section.path(), "buffers");
  EXPECT_EQ(section.fields_size(), 4);
  for (const pb::ConfigField &knob : section.fields()) {
    EXPECT_TRUE(knob.path().starts_with("buffers."));
    EXPECT_FALSE(knob.value().empty()) << knob.path();
    EXPECT_EQ(knob.value(), knob.default_value()) << knob.path();
  }
}

TEST_F(NativePlayerSettingsTest, AWrittenKnobIsKeptAndPersisted) {
  ASSERT_TRUE(player_->applyConfig("output.latency_ms", "250", error_))
      << error_;

  EXPECT_EQ(field(output(), "output.latency_ms")->value(), "250");
  EXPECT_EQ(loadSettingsOverrides().at("output.latency_ms"), "250");
}

TEST_F(NativePlayerSettingsTest, WritingTheDefaultBackLeavesNothingStored) {
  ASSERT_TRUE(player_->applyConfig("output.period_ms", "80", error_)) << error_;
  ASSERT_TRUE(player_->applyConfig("output.period_ms", "40", error_)) << error_;

  EXPECT_FALSE(loadSettingsOverrides().contains("output.period_ms"));
  EXPECT_EQ(field(output(), "output.period_ms")->value(), "40");
}

TEST_F(NativePlayerSettingsTest, BufferWritesGoThroughTheBufferingSection) {
  auto section = player_->bufferSettings();

  ASSERT_TRUE(section->applyConfig("buffers.mpeg", "200000", error_)) << error_;

  EXPECT_EQ(field(buffers(), "buffers.mpeg")->value(), "200000");
  EXPECT_EQ(loadSettingsOverrides().at("buffers.mpeg"), "200000");
}

TEST_F(NativePlayerSettingsTest, ANegativeSizeIsRefusedRatherThanStored) {
  EXPECT_FALSE(player_->applyConfig("buffers.flac", "-1", error_));
  EXPECT_EQ(error_, "must not be negative");

  EXPECT_EQ(field(buffers(), "buffers.flac")->value(), "1536000");
  EXPECT_FALSE(loadSettingsOverrides().contains("buffers.flac"));
}

TEST_F(NativePlayerSettingsTest, AnUndeclaredPathIsRefused) {
  EXPECT_FALSE(player_->applyConfig("output.nonsense", "1", error_));
  EXPECT_EQ(error_, "unknown setting");
}

}  // namespace
