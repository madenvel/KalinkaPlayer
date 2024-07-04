#include "Log.h"

#include <spdlog/sinks/stdout_color_sinks.h>
#include <spdlog/spdlog.h>

// Custom log level formatter to convert log level to uppercase
class CustomLogLevelFormatter : public spdlog::custom_flag_formatter {
public:
  void format(const spdlog::details::log_msg &msg, const std::tm &tm_time,
              spdlog::memory_buf_t &dest) override {
    std::string level_name;
    switch (msg.level) {
    case spdlog::level::trace:
      level_name = "TRACE";
      break;
    case spdlog::level::debug:
      level_name = "DEBUG";
      break;
    case spdlog::level::info:
      level_name = "INFO";
      break;
    case spdlog::level::warn:
      level_name = "WARN";
      break;
    case spdlog::level::err:
      level_name = "ERROR";
      break;
    case spdlog::level::critical:
      level_name = "CRITICAL";
      break;
    case spdlog::level::off:
      level_name = "OFF";
      break;
    default:
      level_name = "UNKNOWN";
      break;
    }
    dest.append(level_name.data(), level_name.data() + level_name.size());
  }

  std::unique_ptr<spdlog::custom_flag_formatter> clone() const override {
    return spdlog::details::make_unique<CustomLogLevelFormatter>();
  }
};

// Function to initialize the logger with a custom format
void initLogger() {
  auto console_sink = std::make_shared<spdlog::sinks::stdout_color_sink_mt>();
  auto formatter = std::make_unique<spdlog::pattern_formatter>();
  formatter->add_flag<CustomLogLevelFormatter>('L').set_pattern(
      "%Y-%m-%d %H:%M:%S.%e %L %t %n: %v");

  console_sink->set_formatter(std::move(formatter));

  auto logger = std::make_shared<spdlog::logger>("native", console_sink);
  spdlog::set_default_logger(logger);
  spdlog::set_level(spdlog::level::debug);
}
