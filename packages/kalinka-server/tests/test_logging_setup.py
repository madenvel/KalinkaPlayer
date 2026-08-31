import io
import logging
import os
import re
import sys

import pytest

from kalinka_server.logging_setup import (
    JournalFormatter,
    make_formatter,
    stream_is_journal,
)


def _record(level, msg, exc_info=None):
    return logging.LogRecord("mod", level, __file__, 1, msg, None, exc_info)


@pytest.fixture
def stream(tmp_path):
    with open(tmp_path / "stream", "w") as handle:
        yield handle


def _identity(stream):
    st = os.fstat(stream.fileno())
    return f"{st.st_dev}:{st.st_ino}"


def test_journal_formatter_prefixes_priority():
    fmt = JournalFormatter()
    assert fmt.format(_record(logging.DEBUG, "d")) == "<7>mod: d"
    assert fmt.format(_record(logging.INFO, "i")) == "<6>mod: i"
    assert fmt.format(_record(logging.WARNING, "w")) == "<4>mod: w"
    assert fmt.format(_record(logging.ERROR, "e")) == "<3>mod: e"
    assert fmt.format(_record(logging.CRITICAL, "c")) == "<2>mod: c"


def test_journal_formatter_prefixes_every_traceback_line():
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record(logging.ERROR, "failed", sys.exc_info())
    lines = JournalFormatter().format(record).splitlines()
    assert len(lines) > 1
    assert all(line.startswith("<3>") for line in lines)


def test_stream_is_journal_needs_the_env_var(monkeypatch, stream):
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    assert not stream_is_journal(stream)


def test_stream_is_journal_rejects_a_value_naming_another_stream(monkeypatch, stream):
    # What a shell inside a systemd user session hands us: the variable is set,
    # inherited from the unit, but it names a stream that is not ours.
    monkeypatch.setenv("JOURNAL_STREAM", "999:999999")
    assert not stream_is_journal(stream)


def test_stream_is_journal_matches_device_and_inode(monkeypatch, stream):
    monkeypatch.setenv("JOURNAL_STREAM", _identity(stream))
    assert stream_is_journal(stream)


def test_stream_is_journal_survives_a_malformed_value(monkeypatch, stream):
    monkeypatch.setenv("JOURNAL_STREAM", "not-a-stream")
    assert not stream_is_journal(stream)


def test_stream_is_journal_survives_a_stream_without_fileno(monkeypatch):
    monkeypatch.setenv("JOURNAL_STREAM", "9:12345")
    assert not stream_is_journal(io.StringIO())


def test_make_formatter_selects_journal_for_the_journal_stream(monkeypatch, stream):
    monkeypatch.setenv("JOURNAL_STREAM", _identity(stream))
    assert isinstance(make_formatter(stream), JournalFormatter)


def test_make_formatter_uses_full_format_elsewhere(monkeypatch, stream):
    monkeypatch.setenv("JOURNAL_STREAM", "999:999999")
    formatter = make_formatter(stream)
    assert not isinstance(formatter, JournalFormatter)
    line = formatter.format(_record(logging.INFO, "hello"))
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} INFO \d+ mod: hello", line
    )


def test_make_formatter_defaults_to_stderr(monkeypatch):
    monkeypatch.setenv("JOURNAL_STREAM", _identity(sys.stderr))
    assert isinstance(make_formatter(), JournalFormatter)


def test_log_format_journal_overrides_detection(monkeypatch, stream):
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    monkeypatch.setenv("KALINKA_LOG_FORMAT", "journal")
    assert isinstance(make_formatter(stream), JournalFormatter)


def test_log_format_full_overrides_detection(monkeypatch, stream):
    monkeypatch.setenv("JOURNAL_STREAM", _identity(stream))
    monkeypatch.setenv("KALINKA_LOG_FORMAT", "FULL")
    assert not isinstance(make_formatter(stream), JournalFormatter)


def test_unknown_log_format_falls_back_to_detection(monkeypatch, stream):
    monkeypatch.setenv("JOURNAL_STREAM", _identity(stream))
    monkeypatch.setenv("KALINKA_LOG_FORMAT", "syslog")
    assert isinstance(make_formatter(stream), JournalFormatter)
