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

/**
 * @brief Whether this process should log in the journal's format.
 *
 * KALINKA_LOG_FORMAT settles it outright when set to "journal" or "full";
 * otherwise the format follows @p fd, and a run logging to a file never takes
 * the journal's — there is no journald behind it to consume the prefixes.
 */
bool useJournalFormat(bool writingToFile, int fd);

#endif // LOG_H
