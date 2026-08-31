#include "Log.h"

#include <gtest/gtest.h>
#include <spdlog/sinks/ostream_sink.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <regex>
#include <sstream>
#include <string>

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
                 R"(WARNING \d+ renderer: hello\n)")));
}

TEST(LogPattern, JournalPrefixesEveryLineOfTheMessage) {
  std::ostringstream out;
  auto sink = std::make_shared<spdlog::sinks::ostream_sink_st>(out);
  spdlog::logger logger("renderer", sink);
  applyLogPattern(logger, true);
  logger.error("first\nsecond");
  EXPECT_EQ(out.str(), "<3>first\n<3>second\n");
}

TEST(LogPattern, JournalLeavesNoPrefixOnATrailingBlankLine) {
  std::ostringstream out;
  auto sink = std::make_shared<spdlog::sinks::ostream_sink_st>(out);
  spdlog::logger logger("renderer", sink);
  applyLogPattern(logger, true);
  logger.error("body\n");
  EXPECT_EQ(out.str(), "<3>body\n");
}

namespace {

class JournalStreamEnv : public ::testing::Test {
protected:
  void TearDown() override { unsetenv("JOURNAL_STREAM"); }

  static std::string identityOf(int fd) {
    struct stat st {};
    EXPECT_EQ(fstat(fd, &st), 0);
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%llu:%llu",
                  static_cast<unsigned long long>(st.st_dev),
                  static_cast<unsigned long long>(st.st_ino));
    return buf;
  }
};

}  // namespace

TEST_F(JournalStreamEnv, UnsetIsNotJournal) {
  unsetenv("JOURNAL_STREAM");
  EXPECT_FALSE(streamIsJournal(STDOUT_FILENO));
}

TEST_F(JournalStreamEnv, InheritedValueForAnotherStreamIsNotJournal) {
  // What a shell inside a systemd user session hands us: the variable is set,
  // but it names a stream that is not ours.
  setenv("JOURNAL_STREAM", "999:999999", 1);
  EXPECT_FALSE(streamIsJournal(STDOUT_FILENO));
}

TEST_F(JournalStreamEnv, MatchingDeviceAndInodeIsJournal) {
  setenv("JOURNAL_STREAM", identityOf(STDOUT_FILENO).c_str(), 1);
  EXPECT_TRUE(streamIsJournal(STDOUT_FILENO));
}

TEST_F(JournalStreamEnv, MalformedValueIsNotJournal) {
  setenv("JOURNAL_STREAM", "not-a-stream", 1);
  EXPECT_FALSE(streamIsJournal(STDOUT_FILENO));
}

TEST_F(JournalStreamEnv, TrailingGarbageIsNotJournal) {
  // Matches what Python's int() would reject, so both sides agree on a value
  // that only starts out looking like ours.
  setenv("JOURNAL_STREAM", (identityOf(STDOUT_FILENO) + ":7").c_str(), 1);
  EXPECT_FALSE(streamIsJournal(STDOUT_FILENO));
}

namespace {

class UseJournalFormat : public JournalStreamEnv {
protected:
  void TearDown() override {
    unsetenv("KALINKA_LOG_FORMAT");
    JournalStreamEnv::TearDown();
  }

  static constexpr bool kToFile = true;
  static constexpr bool kToStdout = false;
};

}  // namespace

TEST_F(UseJournalFormat, FollowsTheStreamWhenNothingIsForced) {
  setenv("JOURNAL_STREAM", identityOf(STDOUT_FILENO).c_str(), 1);
  EXPECT_TRUE(useJournalFormat(kToStdout, STDOUT_FILENO));

  setenv("JOURNAL_STREAM", "999:999999", 1);
  EXPECT_FALSE(useJournalFormat(kToStdout, STDOUT_FILENO));
}

TEST_F(UseJournalFormat, ALogFileNeverTakesTheJournalFormatByDetection) {
  setenv("JOURNAL_STREAM", identityOf(STDOUT_FILENO).c_str(), 1);
  EXPECT_FALSE(useJournalFormat(kToFile, STDOUT_FILENO));
}

TEST_F(UseJournalFormat, TheOverrideWinsOverDetectionBothWays) {
  setenv("KALINKA_LOG_FORMAT", "journal", 1);
  unsetenv("JOURNAL_STREAM");
  EXPECT_TRUE(useJournalFormat(kToFile, STDOUT_FILENO));

  setenv("KALINKA_LOG_FORMAT", "Full", 1);
  setenv("JOURNAL_STREAM", identityOf(STDOUT_FILENO).c_str(), 1);
  EXPECT_FALSE(useJournalFormat(kToStdout, STDOUT_FILENO));
}

TEST_F(UseJournalFormat, AnUnknownOverrideLeavesDetectionInCharge) {
  setenv("KALINKA_LOG_FORMAT", "syslog", 1);
  setenv("JOURNAL_STREAM", identityOf(STDOUT_FILENO).c_str(), 1);
  EXPECT_TRUE(useJournalFormat(kToStdout, STDOUT_FILENO));
}
