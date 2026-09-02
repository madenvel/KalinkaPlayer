#include "TriggerFileUpgradeService.h"

#include <fstream>
#include <system_error>

namespace fs = std::filesystem;

TriggerFileUpgradeService::TriggerFileUpgradeService(fs::path trigger,
                                                     fs::path script,
                                                     fs::path unit)
    : trigger_(std::move(trigger)), script_(std::move(script)),
      unit_(std::move(unit)) {}

bool TriggerFileUpgradeService::supported() const {
  std::error_code ec;
  return fs::is_regular_file(script_, ec) && fs::exists(unit_, ec);
}

std::string TriggerFileUpgradeService::request(const std::string &targetVersion) {
  if (!supported()) {
    return "this renderer was not installed in a way that can upgrade itself";
  }
  std::error_code ec;
  fs::create_directories(trigger_.parent_path(), ec);
  // Written whole and closed before the watcher can act on the change: the
  // path unit fires on close, so a half-written version is not observable.
  std::ofstream out(trigger_, std::ios::trunc);
  if (!out) {
    return "cannot write the upgrade request at " + trigger_.string();
  }
  out << targetVersion << "\n";
  out.close();
  if (!out) {
    return "failed to write the upgrade request at " + trigger_.string();
  }
  return {};
}
