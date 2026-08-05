#pragma once

#include <string>

#include "ConfigContributor.h"

/**
 * @brief The name this renderer announces to Cores, and the setting behind it.
 *
 * Three sources, strongest first: --name on the command line, the stored
 * `renderer.name` setting, and "Kalinka Renderer on <hostname>". A name given
 * on the command line wins for the whole run and the setting is shown
 * read-only, because the next start would override anything written to it.
 *
 * Cores learn the name from the Hello frame, so a change reaches them when the
 * renderer restarts — which is what the field declares.
 */
class RendererName : public ConfigContributor {
public:
  /// @param commandLineName --name, or empty when it was not passed.
  explicit RendererName(std::string commandLineName);

  /// The name to announce right now.
  const std::string &value() const { return effective_; }

  void fillConfig(kalinka::renderer::v1::ConfigSection &out) const override;
  bool applyConfig(const std::string &path, const std::string &value,
                   std::string &error) override;

private:
  std::string commandLineName_;
  std::string configured_;  ///< Stored setting; empty means "use the default".
  std::string effective_;
};

/// "Kalinka Renderer on <hostname>".
std::string defaultRendererName();
