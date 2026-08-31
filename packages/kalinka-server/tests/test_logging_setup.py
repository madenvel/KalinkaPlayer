import logging
import re
import sys

from kalinka_server.logging_setup import JournalFormatter, make_formatter


def _record(level, msg, exc_info=None):
    return logging.LogRecord("mod", level, __file__, 1, msg, None, exc_info)


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


def test_make_formatter_selects_journal_under_systemd(monkeypatch):
    monkeypatch.setenv("JOURNAL_STREAM", "9:12345")
    assert isinstance(make_formatter(), JournalFormatter)


def test_make_formatter_uses_full_format_elsewhere(monkeypatch):
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    formatter = make_formatter()
    assert not isinstance(formatter, JournalFormatter)
    line = formatter.format(_record(logging.INFO, "hello"))
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} INFO \d+ mod: hello", line
    )
