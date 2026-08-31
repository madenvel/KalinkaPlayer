#ifndef LOG_H
#define LOG_H

#include <spdlog/spdlog.h>

extern void initLogger(const std::string &logLevel);

/**
 * @brief Apply Kalinka's shared log pattern to a logger.
 *
 * With journal=true the line is an sd-daemon "<N>" priority prefix plus the
 * message — journald stamps time and level itself, so emitting our own would
 * only duplicate bytes on disk. Otherwise the full
 * "date time LEVEL tid name: message" pattern, matching the server's format.
 */
void applyLogPattern(spdlog::logger &logger, bool journal);

/**
 * @brief Whether @p fd is the stream systemd connected to the journal.
 *
 * JOURNAL_STREAM is inherited by every descendant of a unit — a shell in a
 * systemd-managed desktop session carries it too — so its presence alone
 * proves nothing. It holds the device:inode of the journal stream, which this
 * compares against the fd we actually write to.
 */
bool streamIsJournal(int fd);

#endif // LOG_H
