#include "RendererName.h"

#include <spdlog/spdlog.h>
#include <unistd.h>

#include <map>

#include "SettingsPersistence.h"

namespace pb = kalinka::renderer::v1;

namespace {

constexpr const char *kPath = "renderer.name";

std::string trimmed(const std::string &text) {
  const size_t first = text.find_first_not_of(" \t");
  if (first == std::string::npos) return {};
  return text.substr(first, text.find_last_not_of(" \t") - first + 1);
}

}  // namespace

std::string defaultRendererName() {
  char host[256] = "unknown";
  gethostname(host, sizeof(host) - 1);
  return std::string("Kalinka Renderer on ") + host;
}

RendererName::RendererName(std::string commandLineName)
    : commandLineName_(trimmed(commandLineName)) {
  const std::map<std::string, std::string> overrides = loadSettingsOverrides();
  const auto stored = overrides.find(kPath);
  if (stored != overrides.end()) {
    configured_ = trimmed(stored->second);
  }
  effective_ = !commandLineName_.empty() ? commandLineName_
               : !configured_.empty()    ? configured_
                                         : defaultRendererName();
  if (!commandLineName_.empty() && !configured_.empty() &&
      configured_ != commandLineName_) {
    spdlog::info("--name '{}' overrides the configured name '{}'",
                 commandLineName_, configured_);
  }
}

void RendererName::fillConfig(pb::ConfigSection &out) const {
  out.set_path("renderer");
  out.set_title("Renderer");

  pb::ConfigField *name = out.add_fields();
  name->set_path(kPath);
  name->set_title("Name");
  name->set_description(
      commandLineName_.empty()
          ? "The name this renderer shows up under. Applies when it "
            "reconnects. Leave it empty to use the host name."
          : "Set with --name on the command line, which overrides this "
            "setting.");
  name->set_type(pb::CONFIG_FIELD_TYPE_STRING);
  name->set_value(effective_);
  name->set_default_value(defaultRendererName());
  name->set_apply(pb::APPLY_COST_RESTART_REQUIRED);
  name->set_read_only(!commandLineName_.empty());
}

bool RendererName::applyConfig(const std::string &path,
                               const std::string &value, std::string &error) {
  if (path != kPath) {
    error = "unknown setting";
    return false;
  }
  configured_ = trimmed(value);
  effective_ = configured_.empty() ? defaultRendererName() : configured_;
  updateSettingsOverrides({{kPath, effective_}},
                          {{kPath, defaultRendererName()}});
  spdlog::info("Renderer name set to '{}'; Cores see it on the next connect",
               effective_);
  return true;
}
