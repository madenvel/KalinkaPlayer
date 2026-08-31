#include "Log.h"

#include <gtest/gtest.h>
#include <spdlog/sinks/ostream_sink.h>

#include <regex>
#include <sstream>

namespace {

std::string logOne(bool journal, spdlog::level::level_enum level) {
  std::ostringstream out;
  auto sink = std::make_shared<spdlog::sinks::ostream_sink_st>(out);
  spdlog::logger logger("renderer", sink);
  logger.set_level(spdlog::level::trace);
  applyLogPattern(logger, journal);
  logger.log(level, "hello");
  return out.str();
}

}  // namespace

TEST(LogPattern, JournalEmitsSyslogPriorityPrefixOnly) {
  EXPECT_EQ(logOne(true, spdlog::level::trace), "<7>hello\n");
  EXPECT_EQ(logOne(true, spdlog::level::debug), "<7>hello\n");
  EXPECT_EQ(logOne(true, spdlog::level::info), "<6>hello\n");
  EXPECT_EQ(logOne(true, spdlog::level::warn), "<4>hello\n");
  EXPECT_EQ(logOne(true, spdlog::level::err), "<3>hello\n");
  EXPECT_EQ(logOne(true, spdlog::level::critical), "<2>hello\n");
}

TEST(LogPattern, FullPatternCarriesTimestampLevelThreadAndName) {
  EXPECT_TRUE(std::regex_match(
      logOne(false, spdlog::level::warn),
      std::regex(R"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} )"
                 R"(WARN \d+ renderer: hello\n)")));
}
