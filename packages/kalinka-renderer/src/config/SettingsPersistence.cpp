#include "SettingsPersistence.h"

#include <spdlog/spdlog.h>

#include <cstdlib>
#include <filesystem>
#include <fstream>

namespace fs = std::filesystem;

namespace {

fs::path overridesFile() {
  const char *prefix = std::getenv("KALINKA_PREFIX");
  return fs::path(prefix && *prefix ? prefix : "/") /
         "var/lib/kalinka-renderer/config_overrides";
}

}  // namespace

std::map<std::string, std::string> loadSettingsOverrides() {
  std::map<std::string, std::string> overrides;
  std::ifstream in(overridesFile());
  std::string line;
  while (in && std::getline(in, line)) {
    // Split on the first '=': keys carry none, ALSA device strings may.
    const auto eq = line.find('=');
    if (eq == std::string::npos || eq == 0) {
      continue;
    }
    overrides[line.substr(0, eq)] = line.substr(eq + 1);
  }
  return overrides;
}

void saveSettingsOverrides(
    const std::map<std::string, std::string> &overrides) {
  const fs::path file = overridesFile();
  std::error_code ec;
  if (overrides.empty()) {
    fs::remove(file, ec);
    return;
  }
  fs::create_directories(file.parent_path(), ec);
  // Atomic write so a crash can't leave a truncated file behind.
  const fs::path tmp = file.string() + ".tmp";
  std::ofstream out(tmp, std::ios::trunc);
  for (const auto &[key, value] : overrides) {
    out << key << '=' << value << '\n';
  }
  out.close();
  if (out) {
    fs::rename(tmp, file, ec);
  }
  if (!out || ec) {
    spdlog::warn("Could not persist config overrides at {}", file.string());
  }
}

void updateSettingsOverrides(
    const std::map<std::string, std::string> &settings,
    const std::map<std::string, std::string> &defaults) {
  std::map<std::string, std::string> overrides = loadSettingsOverrides();
  for (const auto &[key, value] : settings) {
    const auto fallback = defaults.find(key);
    if (fallback != defaults.end() && fallback->second == value) {
      overrides.erase(key);
    } else {
      overrides[key] = value;
    }
  }
  saveSettingsOverrides(overrides);
}
