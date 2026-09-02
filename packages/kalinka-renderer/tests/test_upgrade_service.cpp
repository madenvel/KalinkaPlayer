#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>

#include "upgrade/TriggerFileUpgradeService.h"

namespace fs = std::filesystem;

namespace {

/// A temp tree standing in for the paths the package installs.
class TriggerFileUpgradeServiceTest : public ::testing::Test {
protected:
  void SetUp() override {
    root = fs::temp_directory_path() /
           ("kalinka-upgrade-test-" + std::to_string(::getpid()));
    fs::create_directories(root / "opt");
    trigger = root / "run" / "upgrade-request";
    script = root / "opt" / "upgrade-renderer.sh";
    unit = root / "opt" / "kalinka-renderer-upgrade.path";
  }

  void TearDown() override {
    std::error_code ec;
    fs::remove_all(root, ec);
  }

  void installRootSide() {
    std::ofstream(script) << "#!/bin/sh\n";
    std::ofstream(unit) << "[Path]\n";
  }

  TriggerFileUpgradeService service() {
    return TriggerFileUpgradeService(trigger, script, unit);
  }

  fs::path root, trigger, script, unit;
};

TEST_F(TriggerFileUpgradeServiceTest, WithoutTheRootSideUnitsItCannotUpgrade) {
  // A flatpak or from-source build: saying so is what stops a Core offering
  // an upgrade that would silently do nothing.
  EXPECT_FALSE(service().supported());
  EXPECT_FALSE(service().request("0.4.0").empty());
  EXPECT_FALSE(fs::exists(trigger));
}

TEST_F(TriggerFileUpgradeServiceTest, TheRequestCarriesTheVersionToInstall) {
  // The version is in the file so the root-side script installs what was
  // asked for, not whatever is newest by the time it runs.
  installRootSide();
  auto upgrade = service();
  ASSERT_TRUE(upgrade.supported());

  EXPECT_TRUE(upgrade.request("0.4.0").empty());

  ASSERT_TRUE(fs::exists(trigger));
  std::string line;
  std::getline(std::ifstream(trigger), line);
  EXPECT_EQ(line, "0.4.0");
}

TEST_F(TriggerFileUpgradeServiceTest, AnEmptyTargetAsksForTheLatestRelease) {
  installRootSide();
  auto upgrade = service();

  EXPECT_TRUE(upgrade.request("").empty());

  std::string line;
  std::getline(std::ifstream(trigger), line);
  EXPECT_TRUE(line.empty());
}

}  // namespace
