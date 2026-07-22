"""Minimal CUE-sheet parser.

Full-CD rips are often a single audio file plus a sibling ``.cue`` that holds
the real disc metadata (album, performer) and per-track titles + offsets. That
metadata is authoritative — far better than parsing it back out of a folder
name — so the indexer reads it when a file's own tags are empty.

This parses the subset we use: disc-level PERFORMER/TITLE/REM GENRE/REM DATE and
per-track TITLE/PERFORMER/INDEX. Playback splitting (seeking to INDEX offsets)
is out of scope here; only metadata is captured.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

# CUE keywords are ASCII, so they survive every candidate encoding; only the
# quoted values differ. utf-16 (BOM) and utf-8 are the common real-world
# encodings; cp1251 before latin-1 keeps Cyrillic titles readable.
_ENCODINGS = ("utf-8-sig", "utf-16", "cp1251", "latin-1")

_INDEX_RE = re.compile(r"^\s*INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)")
_QUOTED_RE = re.compile(r'"([^"]*)"')


@dataclass
class CueTrack:
    number: Optional[int]
    title: Optional[str]
    performer: Optional[str]
    start_seconds: Optional[float]


@dataclass
class CueFile:
    name: str
    tracks: List[CueTrack] = field(default_factory=list)


@dataclass
class CueSheet:
    performer: Optional[str] = None
    title: Optional[str] = None
    genre: Optional[str] = None
    date: Optional[str] = None
    files: List[CueFile] = field(default_factory=list)

    def tracks_for(self, audio_basename: str) -> List[CueTrack]:
        """Tracks whose FILE is the given audio file (basename compare)."""
        for f in self.files:
            if os.path.basename(f.name).lower() == audio_basename.lower():
                return f.tracks
        # Single-FILE cue: the lone file is implicitly this one.
        if len(self.files) == 1:
            return self.files[0].tracks
        return []


def _decode(raw: bytes) -> str:
    # A wrong encoding can still decode without error (e.g. utf-16 on
    # single-byte text yields CJK garbage). CUE keywords are ASCII and survive
    # only the correct decoding, so prefer whichever produces them.
    fallback: Optional[str] = None
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if fallback is None:
            fallback = text
        up = text.upper()
        if "TRACK" in up and "FILE" in up:
            return text
    return fallback if fallback is not None else raw.decode("latin-1", errors="replace")


def _quoted(line: str, keyword: str) -> Optional[str]:
    """Value after KEYWORD, preferring a quoted form, else the bare remainder."""
    rest = line.strip()[len(keyword):].strip()
    if not rest:
        return None
    m = _QUOTED_RE.search(rest)
    return m.group(1).strip() if m else rest.strip('"').strip()


def parse_cue(path: str) -> Optional[CueSheet]:
    """Parse a .cue file. Returns None if it can't be read or has no tracks."""
    try:
        with open(path, "rb") as fh:
            text = _decode(fh.read())
    except OSError:
        return None

    sheet = CueSheet()
    current: Optional[CueFile] = None
    track: Optional[CueTrack] = None

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        upper = s.upper()

        if upper.startswith("REM GENRE"):
            sheet.genre = _quoted(s[3:].strip(), "GENRE")
        elif upper.startswith("REM DATE"):
            sheet.date = _quoted(s[3:].strip(), "DATE")
        elif upper.startswith("REM "):
            continue
        elif upper.startswith("FILE "):
            name = _quoted(s, "FILE")
            # Strip the trailing type token (WAVE / MP3 / ...) if it leaked in.
            if name and not _QUOTED_RE.search(s):
                name = name.rsplit(" ", 1)[0]
            current = CueFile(name=name or "")
            sheet.files.append(current)
            track = None
        elif upper.startswith("TRACK "):
            parts = s.split()
            num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            track = CueTrack(number=num, title=None, performer=None,
                             start_seconds=None)
            if current is None:
                current = CueFile(name="")
                sheet.files.append(current)
            current.tracks.append(track)
        elif upper.startswith("TITLE"):
            value = _quoted(s, "TITLE")
            if track is not None:
                track.title = value
            else:
                sheet.title = value
        elif upper.startswith("PERFORMER"):
            value = _quoted(s, "PERFORMER")
            if track is not None:
                track.performer = value
            else:
                sheet.performer = value
        elif upper.startswith("INDEX"):
            m = _INDEX_RE.match(s)
            # INDEX 01 is the track start; INDEX 00 is pre-gap, ignore it.
            if m and track is not None and int(m.group(1)) == 1:
                mm, ss, ff = int(m.group(2)), int(m.group(3)), int(m.group(4))
                track.start_seconds = mm * 60 + ss + ff / 75.0

    if not any(f.tracks for f in sheet.files):
        return None
    return sheet


def find_cue_for(audio_path: str) -> Optional[str]:
    """Locate a sibling .cue describing this audio file.

    Prefers a same-stem .cue (the common single-file-rip layout); otherwise
    scans the directory for a .cue whose FILE line references this file.
    """
    directory = os.path.dirname(audio_path)
    base = os.path.basename(audio_path)
    stem = os.path.splitext(base)[0]

    same_stem = os.path.join(directory, stem + ".cue")
    if os.path.isfile(same_stem):
        return same_stem

    try:
        entries = os.listdir(directory)
    except OSError:
        return None
    for entry in entries:
        if not entry.lower().endswith(".cue"):
            continue
        cue_path = os.path.join(directory, entry)
        sheet = parse_cue(cue_path)
        if sheet and any(
            os.path.basename(f.name).lower() == base.lower() for f in sheet.files
        ):
            return cue_path
    return None
