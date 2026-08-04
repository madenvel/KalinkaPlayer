#pragma once

#include <map>
#include <string>

/**
 * @brief On-disk persistence for config overrides.
 *
 * Only settings that differ from the compiled-in defaults are stored, as
 * key=value lines at <KALINKA_PREFIX>/var/lib/kalinka-renderer/config_overrides
 * (KALINKA_PREFIX defaults to "/", same convention as Identity). Saving an
 * empty set removes the file.
 */
std::map<std::string, std::string> loadSettingsOverrides();
void saveSettingsOverrides(const std::map<std::string, std::string> &overrides);
