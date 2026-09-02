#pragma once

#include <filesystem>
#include <string>

#include "UpgradeService.h"

/**
 * @brief UpgradeService over the trigger file kalinka-renderer-upgrade.path
 *        watches, as shipped in the renderer package.
 *
 * The requested version is the file's contents, so the root-side script
 * installs what the Core asked for rather than whatever is newest by the time
 * it runs.
 */
class TriggerFileUpgradeService : public UpgradeService {
public:
  /// Paths are injectable for tests; the defaults are what the package ships.
  explicit TriggerFileUpgradeService(
      std::filesystem::path trigger = "/run/kalinka-renderer/upgrade-request",
      std::filesystem::path script = "/opt/kalinka/upgrade-renderer.sh",
      std::filesystem::path unit =
          "/usr/lib/systemd/system/kalinka-renderer-upgrade.path");

  bool supported() const override;
  std::string request(const std::string &targetVersion) override;

private:
  std::filesystem::path trigger_;
  std::filesystem::path script_;
  std::filesystem::path unit_;
};
