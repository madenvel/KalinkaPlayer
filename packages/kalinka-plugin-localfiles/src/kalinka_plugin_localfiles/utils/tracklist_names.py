"""Whether a file name says anything about the track inside it.

A rip named by hand carries its album's tracklist in the file names, and that
tracklist is the album's own — a Melodiya pressing really does call its second
track "Только мы (Solo noi)". A folder of ``track01.flac``, or of ``music.mp3``
beside ``audio.mp3``, carries nothing at all.

The distinction matters because an external source can identify only the
recordings it happens to know, and re-titling those alone splits one album
across two namings.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Tuple

#: Everything a running order is made of: digits, separators, padding.
_ORDERING_RE = re.compile(r"[\W\d_]+")

#: What a ripper, a recorder or a browser writes when it has no name to give.
_FILLER_WORDS = frozenset(
    {
        "audio", "copy", "file", "music", "new", "output", "rec", "recording",
        "song", "sound", "temp", "title", "tmp", "track", "unknown", "unnamed",
        "untitled",
    }
)

#: A stem every file in the folder repeats ("zzz01", "zzz02") is one word;
#: more than one is a work whose parts really are numbered ("Variatio 1 a 1
#: Clav"), and those numbers are its content.
_MAX_SHARED_WORDS = 1


def _words(file_path: str) -> Tuple[str, ...]:
    stem = os.path.splitext(os.path.basename(file_path))[0]
    return tuple(_ORDERING_RE.sub(" ", stem).casefold().split())


def name_carries_title(file_path: str, album_paths: Iterable[str]) -> bool:
    """Whether this file's name says more about its track than the order.

    @param file_path The track's own file.
    @param album_paths Every audio file of the same album, the file itself
        included; order is irrelevant. It is only context — a name shared
        with all of them distinguishes nothing.
    @return True when the name is this track's title and may not be replaced.
    """
    words = _words(file_path)
    if not words or all(word in _FILLER_WORDS for word in words):
        return False
    siblings = [_words(path) for path in album_paths if path]
    # A shared name is evidence of numbering only against something to share.
    if len(siblings) < 2 or len(set(siblings)) > 1:
        return True
    return len(words) > _MAX_SHARED_WORDS
