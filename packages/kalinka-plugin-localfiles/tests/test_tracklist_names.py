#!/usr/bin/env python3
"""Telling a hand-named rip from a folder a machine numbered or gave up on.

The names in a real rip carry the pressing's own tracklist; the names a ripper
or a recorder writes carry the running order, or nothing. Each name is judged
on its own, with the folder as context only — a name every sibling shares
tells the tracks apart no better than the extension does.
"""

import pytest

from kalinka_plugin_localfiles.utils.tracklist_names import name_carries_title

_RIP = [
    "/m/Toto Cutugno/1. Итальянец (L’Italiano).flac",
    "/m/Toto Cutugno/2. Только мы (Solo noi).flac",
    "/m/Toto Cutugno/3. Одни (Soli).flac",
]


def _verdicts(paths):
    return [name_carries_title(p, paths) for p in paths]


class TestNamesThatAreATracklist:
    def test_a_hand_named_rip_carries_its_titles(self):
        """The Melodiya Cutugno pressing: Russian title, Italian in brackets."""
        assert all(_verdicts(_RIP))

    def test_a_composer_suffix_is_still_a_title(self):
        paths = [
            "/m/Abbey Road/THE BEATLES - 01.Come Together (Lennon-McCartney).flac",
            "/m/Abbey Road/THE BEATLES - 02.Something (Harrison).flac",
        ]
        assert all(_verdicts(paths))

    def test_numbered_movements_of_one_work_are_protected(self):
        """Goldberg variations differ only by number, but "Variatio a Clav"
        is three words of real name — the safe reading is that it is one."""
        paths = ["/m/Bach/Variatio 1 a 1 Clav.flac", "/m/Bach/Variatio 2 a 1 Clav.flac"]
        assert all(_verdicts(paths))

    def test_a_single_named_file_is_its_own_title(self):
        assert name_carries_title("/m/a/Итальянец.flac", ["/m/a/Итальянец.flac"])

    def test_a_one_word_title_is_still_a_title(self):
        """Russian song titles often are one word; only a shared one is not."""
        paths = ["/m/Кино/1. Кукушка.flac", "/m/Кино/2. Звезда.flac"]
        assert all(_verdicts(paths))


class TestNamesThatAreOnlyAnOrder:
    @pytest.mark.parametrize("stem", ["track{}", "{}", "audio_{:04d}", "Untitled {}"])
    def test_a_stem_plus_a_number_says_only_the_order(self, stem):
        paths = [f"/m/rip/{stem.format(n)}.flac" for n in range(1, 6)]
        assert not any(_verdicts(paths))

    def test_an_unrecognised_stem_is_caught_by_its_siblings(self):
        """No vocabulary lists "zzz"; the folder is what gives it away."""
        paths = [f"/m/rip/zzz{n:02d}.flac" for n in range(1, 6)]
        assert not any(_verdicts(paths))

    def test_zero_padding_does_not_make_a_title(self):
        paths = [f"/m/rip/{n:03d}.flac" for n in range(1, 12)]
        assert not any(_verdicts(paths))

    def test_case_and_separators_are_not_content(self):
        paths = ["/m/rip/Track-01.flac", "/m/rip/TRACK_02.flac", "/m/rip/track 03.flac"]
        assert not any(_verdicts(paths))


class TestNamesThatDifferAndStillSayNothing:
    """The trap: distinctness is not information. A dump of files a recorder
    or a browser named has a different word in each name and not one title
    among them — and such a folder clusters as an album, so nothing else
    stops it reaching the gate.
    """

    def test_a_dump_of_filler_words_is_not_a_tracklist(self):
        paths = [
            "/d/music.mp3",
            "/d/audio.mp3",
            "/d/song.mp3",
            "/d/recording.mp3",
            "/d/untitled.mp3",
        ]
        assert not any(_verdicts(paths))

    def test_filler_with_a_number_is_still_filler(self):
        paths = ["/d/New Recording 3.m4a", "/d/audio copy 2.m4a", "/d/track.m4a"]
        assert not any(_verdicts(paths))

    def test_one_real_title_among_filler_keeps_only_itself(self):
        """Judged per name: the real one is protected, the rest stay free."""
        paths = ["/d/music.mp3", "/d/audio.mp3", "/d/Кукушка.mp3"]
        assert _verdicts(paths) == [False, False, True]

    def test_a_filler_word_inside_a_real_title_is_not_filler(self):
        paths = ["/m/a/1. Music of the Night.flac", "/m/a/2. Sound and Vision.flac"]
        assert all(_verdicts(paths))

    def test_nothing_to_judge_is_not_a_title(self):
        assert not name_carries_title("", [])
        assert not name_carries_title("/d/01.flac", [])
