#pragma once

#include <string>

/**
 * @brief Asking this machine to install a new renderer release.
 *
 * The renderer runs unprivileged and cannot install anything itself; all it
 * does is ask, by touching a file a root-owned systemd path unit watches.
 * Whether that machinery exists is what supported() answers — a build
 * installed some other way (flatpak, from source) can only report that it
 * cannot, so a Core never offers an upgrade that would silently do nothing.
 */
class UpgradeService {
public:
  virtual ~UpgradeService() = default;

  /// Whether the root-side units that perform an upgrade are installed here.
  virtual bool supported() const = 0;

  /// Ask for @p targetVersion (empty asks for the latest published release).
  /// @return empty on success, else why the request could not be made.
  virtual std::string request(const std::string &targetVersion) = 0;
};
