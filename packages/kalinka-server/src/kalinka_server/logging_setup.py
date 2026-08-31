"""Journald-aware log formatting shared by the server's entry points."""

import logging
import os


LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s %(thread)d %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _syslog_priority(levelno: int) -> int:
    if levelno >= logging.CRITICAL:
        return 2
    if levelno >= logging.ERROR:
        return 3
    if levelno >= logging.WARNING:
        return 4
    if levelno >= logging.INFO:
        return 6
    return 7


class JournalFormatter(logging.Formatter):
    """Formats for the systemd journal: an sd-daemon "<N>" priority prefix and
    no timestamp or level text of our own — journald records both itself."""

    def __init__(self) -> None:
        super().__init__("%(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        prefix = f"<{_syslog_priority(record.levelno)}>"
        # journald parses each stream line on its own; prefix continuation
        # lines (tracebacks) so they keep the record's priority.
        return prefix + super().format(record).replace("\n", "\n" + prefix)


def make_formatter() -> logging.Formatter:
    """The formatter for this process: journal-native under systemd, the full
    timestamped Kalinka format everywhere else."""
    if os.environ.get("JOURNAL_STREAM"):
        return JournalFormatter()
    return logging.Formatter(LOG_FORMAT, DATE_FORMAT)
