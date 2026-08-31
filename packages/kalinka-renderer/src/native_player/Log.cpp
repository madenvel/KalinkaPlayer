#include "Log.h"

// Renderer delta: current spdlog declares custom_flag_formatter here.
#include <spdlog/pattern_formatter.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <spdlog/spdlog.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <string>

/// Renders the level name uppercase; spdlog's own %l is lower case.
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

/// Emits the sd-daemon "<N>" syslog priority for the message's level.
class SyslogPriorityFormatter : public spdlog::custom_flag_formatter {
public:
  void format(const spdlog::details::log_msg &msg, const std::tm &,
              spdlog::memory_buf_t &dest) override {
    char priority;
    switch (msg.level) {
    case spdlog::level::critical:
      priority = '2';
      break;
    case spdlog::level::err:
      priority = '3';
      break;
    case spdlog::level::warn:
      priority = '4';
      break;
    case spdlog::level::info:
      priority = '6';
      break;
    default:
      priority = '7';
      break;
    }
    dest.push_back('<');
    dest.push_back(priority);
    dest.push_back('>');
  }

  std::unique_ptr<spdlog::custom_flag_formatter> clone() const override {
    return spdlog::details::make_unique<SyslogPriorityFormatter>();
  }
};

bool streamIsJournal(int fd) {
  const char *spec = std::getenv("JOURNAL_STREAM");
  if (spec == nullptr) {
    return false;
  }
  unsigned long long dev = 0;
  unsigned long long ino = 0;
  if (std::sscanf(spec, "%llu:%llu", &dev, &ino) != 2) {
    return false;
  }
  struct stat st {};
  if (fstat(fd, &st) != 0) {
    return false;
  }
  return static_cast<unsigned long long>(st.st_dev) == dev &&
         static_cast<unsigned long long>(st.st_ino) == ino;
}

namespace {

enum class LogFormat { Auto, Journal, Full };

LogFormat configuredLogFormat() {
  const char *mode = std::getenv("KALINKA_LOG_FORMAT");
  if (mode == nullptr) {
    return LogFormat::Auto;
  }
  std::string value(mode);
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return std::tolower(c); });
  if (value == "journal") {
    return LogFormat::Journal;
  }
  if (value == "full") {
    return LogFormat::Full;
  }
  return LogFormat::Auto;
}

}  // namespace

bool useJournalFormat(bool writingToFile, int fd) {
  switch (configuredLogFormat()) {
  case LogFormat::Journal:
    return true;
  case LogFormat::Full:
    return false;
  case LogFormat::Auto:
    break;
  }
  return !writingToFile && streamIsJournal(fd);
}

void applyLogPattern(spdlog::logger &logger, bool journal) {
  auto formatter = std::make_unique<spdlog::pattern_formatter>();
  if (journal) {
    formatter->add_flag<SyslogPriorityFormatter>('P').set_pattern("%P%v");
  } else {
    formatter->add_flag<CustomLogLevelFormatter>('L').set_pattern(
        "%Y-%m-%d %H:%M:%S.%e %L %t %n: %v");
  }
  logger.set_formatter(std::move(formatter));
}

void initLogger(const std::string &logLevel) {
  auto console_sink = std::make_shared<spdlog::sinks::stdout_color_sink_mt>();
  auto logger = std::make_shared<spdlog::logger>("native", console_sink);
  applyLogPattern(*logger, false);
  spdlog::set_default_logger(logger);
  spdlog::set_level(spdlog::level::from_str(logLevel));
}
