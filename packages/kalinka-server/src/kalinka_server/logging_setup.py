"""Journald-aware log formatting shared by the server's entry points."""

import logging
import os
import sys
from typing import IO, Optional


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


def stream_is_journal(stream: IO) -> bool:
    """Whether this stream is the one systemd connected to the journal.

    JOURNAL_STREAM is inherited by every descendant of a systemd unit — a shell
    in a systemd-managed desktop session has it set too — so its presence alone
    proves nothing. systemd's contract is that it carries the device:inode of
    the journal stream, to be compared against the stream we actually write to.
    """
    spec = os.environ.get("JOURNAL_STREAM")
    if not spec:
        return False
    try:
        dev, _, ino = spec.partition(":")
        st = os.fstat(stream.fileno())
        return st.st_dev == int(dev) and st.st_ino == int(ino)
    except (AttributeError, OSError, ValueError):
        return False


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


def make_formatter(stream: Optional[IO] = None) -> logging.Formatter:
    """The formatter for `stream` (default stderr, as logging.StreamHandler):
    journal-native when systemd owns it, the full timestamped Kalinka format
    otherwise."""
    if stream_is_journal(stream if stream is not None else sys.stderr):
        return JournalFormatter()
    return logging.Formatter(LOG_FORMAT, DATE_FORMAT)
