#include "Log.h"

// Renderer delta: current spdlog declares custom_flag_formatter here.
#include <spdlog/pattern_formatter.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <spdlog/spdlog.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cctype>
#include <cerrno>
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
      level_name = "WARNING";
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

char syslogPriority(spdlog::level::level_enum level) {
  switch (level) {
  case spdlog::level::critical:
    return '2';
  case spdlog::level::err:
    return '3';
  case spdlog::level::warn:
    return '4';
  case spdlog::level::info:
    return '6';
  default:
    return '7';
  }
}

/// Emits the message behind an sd-daemon "<N>" priority on every line of it:
/// journald reads its stream line by line and prices each one on its own.
class JournalMessageFormatter : public spdlog::custom_flag_formatter {
public:
  void format(const spdlog::details::log_msg &msg, const std::tm &,
              spdlog::memory_buf_t &dest) override {
    const char prefix[] = {'<', syslogPriority(msg.level), '>'};
    const char *const begin = msg.payload.data();
    size_t size = msg.payload.size();
    // A trailing newline would leave a prefix alone on an empty line.
    while (size > 0 && begin[size - 1] == '\n') {
      --size;
    }

    dest.append(prefix, prefix + sizeof(prefix));
    for (size_t i = 0; i < size; ++i) {
      dest.push_back(begin[i]);
      if (begin[i] == '\n') {
        dest.append(prefix, prefix + sizeof(prefix));
      }
    }
  }

  std::unique_ptr<spdlog::custom_flag_formatter> clone() const override {
    return spdlog::details::make_unique<JournalMessageFormatter>();
  }
};

bool streamIsJournal(int fd) {
  const char *spec = std::getenv("JOURNAL_STREAM");
  if (spec == nullptr) {
    return false;
  }
  char *end = nullptr;
  errno = 0;
  const unsigned long long dev = std::strtoull(spec, &end, 10);
  if (errno != 0 || end == spec || *end != ':') {
    return false;
  }
  const char *const inode = end + 1;
  errno = 0;
  const unsigned long long ino = std::strtoull(inode, &end, 10);
  if (errno != 0 || end == inode || *end != '\0') {
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
    formatter->add_flag<JournalMessageFormatter>('P').set_pattern("%P");
  } else {
    formatter->add_flag<CustomLogLevelFormatter>('L').set_pattern(
        "%Y-%m-%d %H:%M:%S.%e %L %t %n: %v");
  }
  logger.set_formatter(std::move(formatter));
}

void initLogger(const std::string &logLevel) {
  auto console_sink = std::make_shared<spdlog::sinks::stdout_color_sink_mt>();
  auto logger = std::make_shared<spdlog::logger>("native", console_sink);
  applyLogPattern(*logger, useJournalFormat(false, STDOUT_FILENO));
  spdlog::set_default_logger(logger);
  spdlog::set_level(spdlog::level::from_str(logLevel));
}
