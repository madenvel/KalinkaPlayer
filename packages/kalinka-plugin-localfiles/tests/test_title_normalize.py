#!/usr/bin/env python3
"""
Tests for normalize_album_title (Phase 2, 2h) — deterministic cleanup of
folder/tag-derived album titles. Cases drawn from a real library's
path-artifact titles, plus protective cases for titles that must NOT change.
"""

import pytest

from kalinka_plugin_localfiles.clustering.classify import normalize_album_title


@pytest.mark.parametrize("raw,clean", [
    # Real path-artifact titles from the assessed library:
    ("1993. Jean Michel Jarre - Chronology (Sony 88875129492, Russia)",
     "Jean Michel Jarre - Chronology"),
    ("1982 - Roxy Music - Avalon (EG, EGHP 50, UK, 24-192)",
     "Roxy Music - Avalon"),
    ("1976. Jean Michel Jarre - Oxygene (Sony-BMG 88843089342, Russia)",
     "Jean Michel Jarre - Oxygene"),
    ("{uaoa}Ellington, Mingus, Roach - Money Jungle",
     "Ellington, Mingus, Roach - Money Jungle"),
    ("Playlist - Urban - 500604904 --- Jamendo - MP3",
     "Playlist - Urban"),
    # Bracketed catalogue number ("CD" + a long digit run) — a pressing id,
    # not the "(CD 1)" disc marker, so it is stripped.
    ("Talks [CD 61407]", "Talks"),
    ("Classic Queen [CD 61311]", "Classic Queen"),
    ("Queen Talks [1992, Canada, CD 61407]", "Queen Talks"),
])
def test_strips_junk(raw, clean):
    assert normalize_album_title(raw) == clean


@pytest.mark.parametrize("title", [
    "Abbey Road",
    "The Wall",
    "Blade Runner (Soundtrack)",          # descriptive paren kept
    "OK Computer (Remastered)",           # descriptive paren kept
    "Live at Wembley (Live)",
    "Greatest Hits Vol. 2",               # volume kept (not a catalog paren)
    "The Best Of Michael Jackson (Disk 2)",  # disc marker left to disc logic
    "Discovery (2001)",                   # bare year in paren kept
    "1984",                               # a title that *is* a year
    "Iceberg Forces [CC Edition]",        # descriptive bracket (no catalog id) kept
])
def test_leaves_real_titles_untouched(title):
    assert normalize_album_title(title) == title


def test_empty_and_none_safe():
    assert normalize_album_title("") == ""
    assert normalize_album_title("(Sony 12345678)") == "(Sony 12345678)"  # all junk -> keep original
    # Idempotent.
    once = normalize_album_title("1993. Foo - Bar (Sony 88875129492, Russia)")
    assert normalize_album_title(once) == once
