#include <gtest/gtest.h>

#include <memory>

#include "AlsaVolumeControl.h"
#include "AudioPlayer.h"
#include "TestHelpers.h"

// What a monitor listens to, and for how long. configureVolume() drops and
// remakes the mixer on every mode change — a session open and a session close
// are each one — and a monitor bound to the mixer was left holding a dead one.
namespace {

Config playbackConfig() {
  return Config{{"output.alsa.device", testDevice()}};
}

TEST(VolumeEventsTest, AChangeReachesTheMonitorThatIsListening) {
  auto events = std::make_shared<VolumeEvents>();
  VolumeMonitor monitor(events);

  events->publish(42);

  const VolumeState heard = monitor.wait();
  EXPECT_TRUE(heard.supported);
  EXPECT_EQ(heard.current, 42);
  EXPECT_EQ(heard.backend, static_cast<int>(VolumeBackend::Hardware));
}

TEST(VolumeEventsTest, AStoppedMonitorHearsNothingFurther) {
  auto events = std::make_shared<VolumeEvents>();
  VolumeMonitor monitor(events);

  monitor.stop();
  events->publish(42);

  EXPECT_FALSE(monitor.isRunning());
  EXPECT_FALSE(monitor.hasData());
}

TEST(VolumeMonitorLifetimeTest, TheMixerMayBeRemadeUnderAMonitorThatIsHeld) {
  AudioPlayer player(playbackConfig());
  player.configureVolume("hardware", "");
  auto monitor = player.volumeMonitor();

  // A session takes the volume, and gives it back: two mixers gone.
  player.configureVolume("software", "");
  player.configureVolume("hardware", "");

  monitor->stop();
  EXPECT_FALSE(monitor->isRunning());
}

TEST(VolumeMonitorLifetimeTest, AMonitorMayOutliveThePlayerThatMadeIt) {
  std::unique_ptr<VolumeMonitor> monitor;
  {
    AudioPlayer player(playbackConfig());
    player.configureVolume("hardware", "");
    monitor = player.volumeMonitor();
  }

  monitor->stop();
  EXPECT_FALSE(monitor->isRunning());
}

TEST(VolumeMonitorLifetimeTest, ItGoesOnListeningAcrossAModeChange) {
  AudioPlayer player(playbackConfig());
  player.configureVolume("hardware", "");
  auto monitor = player.volumeMonitor();

  player.configureVolume("software", "");
  ASSERT_TRUE(monitor->isRunning()) << "a mode change is not the end of it";
}

} // namespace
