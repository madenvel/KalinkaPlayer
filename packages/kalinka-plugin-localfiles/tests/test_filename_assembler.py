"""Each rule the assembler applies on top of the model's raw spans.

Driven from literal spans, so a failure here names the rule that broke rather
than the model that moved. Every test is titled with the real case it
protects; the end-to-end behaviour lives in
``test_indexer_filename_metadata.py``.
"""

import pytest

from kalinka_plugin_localfiles.clustering.classify import is_various_artists_name
from kalinka_plugin_localfiles.filename_model.assembler import (
    MIN_SCORE,
    assemble,
    build_view,
)

MUSIC_ROOT = "/home/user/Music"


def spans(view, *labelled):
    """Spans for ``labelled`` as (label, text[, score]), located in ``view``."""
    out = []
    cursor = 0
    for entry in labelled:
        label, text = entry[0], entry[1]
        score = entry[2] if len(entry) > 2 else 1.0
        start = view.index(text, cursor)
        out.append(
            {
                "label": label,
                "text": text,
                "start": start,
                "end": start + len(text),
                "score": score,
            }
        )
        cursor = start + len(text)
    return {"input": view, "spans": out}


def run(view, *labelled, floors=MIN_SCORE):
    return assemble(
        view,
        spans(view, *labelled),
        is_placeholder_artist=is_various_artists_name,
        floors=floors,
    )


class TestPathWindow:
    """Rule 1. More than an artist folder above the album confuses the model
    into reading the topmost directory as the artist."""

    def test_keeps_artist_and_album_folders(self):
        view = build_view(
            f"{MUSIC_ROOT}/Nick Cave/Murder Ballads/Stagger Lee.mp3", MUSIC_ROOT
        )
        assert view == "Nick Cave/Murder Ballads/Stagger Lee.mp3"

    def test_drops_a_genre_folder_above_the_artist(self):
        view = build_view(
            f"{MUSIC_ROOT}/Rock/Nick Cave/Murder Ballads/Stagger Lee.mp3", MUSIC_ROOT
        )
        assert view == "Nick Cave/Murder Ballads/Stagger Lee.mp3"

    def test_steps_over_a_disc_subdirectory(self):
        view = build_view(
            f"{MUSIC_ROOT}/Vangelis - The Best Of/CD 1/08 Closing.flac", MUSIC_ROOT
        )
        assert view == "Vangelis - The Best Of/CD 1/08 Closing.flac"

    def test_never_climbs_above_the_music_root(self):
        assert (
            build_view(f"{MUSIC_ROOT}/Stagger Lee.mp3", MUSIC_ROOT) == "Stagger Lee.mp3"
        )
        assert build_view(
            f"{MUSIC_ROOT}/Murder Ballads/Stagger Lee.mp3", MUSIC_ROOT
        ) == ("Murder Ballads/Stagger Lee.mp3")

    def test_no_root_means_no_view(self):
        assert build_view(f"{MUSIC_ROOT}/Stagger Lee.mp3", None) is None


class TestDirectoryArtistWins:
    """Rule 2. "В.Цой - Черный альбом/2.Красно - желтые дни.flac" was filed
    under an artist "Красно" with the title "желтые дни"."""

    def test_folder_artist_beats_the_basename(self):
        view = "Кино/1988 - Группа крови/06 - Красно-желтые дни.flac"
        result = run(
            view,
            ("ARTIST", "Кино"),
            ("YEAR", "1988"),
            ("ALBUM", "Группа крови"),
            ("TRACK_NUMBER", "06"),
            ("ARTIST", "Красно"),
            ("TITLE", "желтые дни"),
        )
        assert result.artist == "Кино"
        assert result.title == "Красно-желтые дни"
        assert result.album == "Группа крови"
        assert result.track_number == 6
        assert result.year == 1988

    def test_deepest_folder_artist_wins(self):
        """A "Jarre - Collection" container must not outrank the album folder."""
        view = "Jarre - Collection/1993 Jean Michel Jarre - Chronology/Chronologie.flac"
        result = run(
            view,
            ("ARTIST", "Jarre"),
            ("YEAR", "1993"),
            ("ARTIST", "Jean Michel Jarre"),
            ("ALBUM", "Chronology"),
            ("TITLE", "Chronologie"),
        )
        assert result.artist == "Jean Michel Jarre"

    def test_various_artists_folder_names_nobody(self):
        view = "Various Artists - Hits 1995/03 - Roxette - Joyride.flac"
        result = run(
            view,
            ("ARTIST", "Various Artists"),
            ("ALBUM", "Hits 1995"),
            ("TRACK_NUMBER", "03"),
            ("ARTIST", "Roxette"),
            ("TITLE", "Joyride"),
        )
        assert result.artist == "Roxette"
        assert result.title == "Joyride"

    @pytest.mark.parametrize("placeholder", ["VA", "V.A", "V.A.", "Various"])
    def test_every_various_artists_spelling(self, placeholder):
        view = f"{placeholder} - Hits/03 - Roxette - Joyride.flac"
        result = run(
            view,
            ("ARTIST", placeholder),
            ("ALBUM", "Hits"),
            ("ARTIST", "Roxette"),
            ("TITLE", "Joyride"),
        )
        assert result.artist == "Roxette"


class TestBasenameArtistAbsorption:
    """Rule 3. A basename that repeats the folder's artist is a real split;
    one that contradicts it is the head of the title."""

    def test_matching_artist_keeps_the_split(self):
        view = "The Orb - Adventures/05 - The Orb - Spanish Castles.flac"
        result = run(
            view,
            ("ARTIST", "The Orb"),
            ("ALBUM", "Adventures"),
            ("TRACK_NUMBER", "05"),
            ("ARTIST", "The Orb"),
            ("TITLE", "Spanish Castles"),
        )
        assert result.artist == "The Orb"
        assert result.title == "Spanish Castles"

    def test_punctuation_only_difference_keeps_the_split(self):
        view = "Jean Michel Jarre - Chronology/Jean-Michel Jarre - Chronologie.flac"
        result = run(
            view,
            ("ARTIST", "Jean Michel Jarre"),
            ("ALBUM", "Chronology"),
            ("ARTIST", "Jean-Michel Jarre"),
            ("TITLE", "Chronologie"),
        )
        assert result.title == "Chronologie"

    def test_a_trailing_artist_is_not_absorbed(self):
        """ "03 Ain't No Sunshine - Michael Jackson.flac" — the artist follows
        the title, so there is nothing in front of it to absorb."""
        view = "Best Of/03 Ain't No Sunshine - Michael Jackson.flac"
        result = run(
            view,
            ("ALBUM", "Best Of"),
            ("TRACK_NUMBER", "03"),
            ("TITLE", "Ain't No Sunshine"),
            ("ARTIST", "Michael Jackson"),
        )
        assert result.title == "Ain't No Sunshine"


class TestUnspacedDash:
    """Rule 4. The model splits "Красно-желтые дни" and "Кино-Пачка сигарет"
    the same way; the case of the following word is the only difference."""

    def test_lowercase_continuation_is_a_hyphen(self):
        result = run(
            "Красно-желтые дни.flac", ("ARTIST", "Красно"), ("TITLE", "желтые дни")
        )
        assert result.artist is None
        assert result.title == "Красно-желтые дни"

    def test_uppercase_continuation_is_an_artist(self):
        result = run(
            "Кино-Пачка сигарет.flac", ("ARTIST", "Кино"), ("TITLE", "Пачка сигарет")
        )
        assert result.artist == "Кино"
        assert result.title == "Пачка сигарет"

    def test_a_spaced_dash_always_separates(self):
        result = run(
            "Pink Floyd - one of these days.flac",
            ("ARTIST", "Pink Floyd"),
            ("TITLE", "one of these days"),
        )
        assert result.artist == "Pink Floyd"
        assert result.title == "one of these days"


class TestEdition:
    """Rule 5. Tags in a real library read "Us And Them (2011 Remastered
    Version)", so the qualifier belongs to the title it qualifies."""

    def test_edition_is_folded_back_into_the_title(self):
        view = "01. Moment of Peace (New 2025 Jubilee Version).flac"
        result = run(
            view,
            ("TRACK_NUMBER", "01"),
            ("TITLE", "Moment of Peace"),
            ("EDITION", "New 2025 Jubilee Version"),
        )
        assert result.title == "Moment of Peace (New 2025 Jubilee Version)"

    def test_a_directory_edition_qualifies_the_album_not_the_track(self):
        view = "Pink Floyd - Dark Side (2011 Remastered Version)/09. Brain Damage.flac"
        result = run(
            view,
            ("ARTIST", "Pink Floyd"),
            ("ALBUM", "Dark Side"),
            ("EDITION", "2011 Remastered Version"),
            ("TRACK_NUMBER", "09"),
            ("TITLE", "Brain Damage"),
        )
        assert result.title == "Brain Damage"
        assert result.album == "Dark Side"


class TestAlbum:
    """Rule 6. One folder is one album, and an album title never carries the
    year, edition or rip settings the folder name happens to include."""

    def test_a_lone_parent_folder_is_an_album_not_an_artist(self):
        result = run(
            "Murder Ballads/Stagger Lee.mp3",
            ("ARTIST", "Murder Ballads"),
            ("TITLE", "Stagger Lee"),
        )
        assert result.album == "Murder Ballads"
        assert result.artist is None

    def test_an_artist_folder_above_an_album_is_still_an_artist(self):
        view = "Nick Cave/Murder Ballads/Stagger Lee.mp3"
        result = run(
            view,
            ("ARTIST", "Nick Cave"),
            ("ALBUM", "Murder Ballads"),
            ("TITLE", "Stagger Lee"),
        )
        assert result.artist == "Nick Cave"
        assert result.album == "Murder Ballads"

    def test_technical_tags_never_reach_the_album(self):
        view = "Борис Гребенщиков - 2018 - Время N (WEB) [FLAC]/06 - Ножи.flac"
        result = run(
            view,
            ("ARTIST", "Борис Гребенщиков"),
            ("YEAR", "2018"),
            ("ALBUM", "Время N"),
            ("TECHNICAL", "FLAC"),
            ("TRACK_NUMBER", "06"),
            ("TITLE", "Ножи"),
        )
        assert result.album == "Время N"
        assert result.year == 2018


class TestNumbers:
    def test_track_number_comes_only_from_the_basename(self):
        """A "01 - …" folder does not number the files inside it."""
        view = "01 - Live Sessions/Stagger Lee.mp3"
        result = run(
            view,
            ("TRACK_NUMBER", "01"),
            ("ALBUM", "Live Sessions"),
            ("TITLE", "Stagger Lee"),
        )
        assert result.track_number is None

    def test_disc_number_may_come_from_a_directory(self):
        view = "The Best Of/CD 1/08 Closing themes.flac"
        result = run(
            view,
            ("ALBUM", "The Best Of"),
            ("DISC_NUMBER", "1"),
            ("TRACK_NUMBER", "08"),
            ("TITLE", "Closing themes"),
        )
        assert result.disc_number == 1
        assert result.track_number == 8

    def test_a_vinyl_side_numbers_the_disc_it_is(self):
        """ "B1" is side two, track one — the letter is not a failed int()."""
        view = "Pink Floyd/The Wall/B1 Goodbye Blue Sky.flac"
        result = run(
            view,
            ("ARTIST", "Pink Floyd"),
            ("ALBUM", "The Wall"),
            ("DISC_NUMBER", "B"),
            ("TRACK_NUMBER", "1"),
            ("TITLE", "Goodbye Blue Sky"),
        )
        assert result.disc_number == 2
        assert result.track_number == 1

    def test_a_disc_letter_outside_the_sides_numbers_nothing(self):
        """The runtime drops these before the assembler sees them; if one ever
        arrives, it must not become a disc by accident."""
        view = "U96 - Das Boot.mp3"
        result = run(view, ("DISC_NUMBER", "U"), ("TITLE", "Das Boot"))
        assert result.disc_number is None

    def test_the_earliest_year_is_the_release_year(self):
        """ "1973 (2021) Pink Floyd - …" states the original first."""
        view = "1973 (2021) Pink Floyd - Dark Side/09. Brain Damage.flac"
        result = run(
            view,
            ("YEAR", "1973"),
            ("YEAR", "2021"),
            ("ARTIST", "Pink Floyd"),
            ("ALBUM", "Dark Side"),
            ("TRACK_NUMBER", "09"),
            ("TITLE", "Brain Damage"),
        )
        assert result.year == 1973


class TestConfidenceFloors:
    """A low-scoring artist would mint a library row nothing later removes."""

    def test_an_artist_below_its_floor_is_dropped(self):
        result = run(
            "Some Folder/Stagger Lee.mp3",
            ("ARTIST", "Some Folder", 0.2),
            ("TITLE", "Stagger Lee"),
        )
        assert result.artist is None
        assert result.album is None

    def test_a_title_is_kept_at_any_score(self):
        result = run("Stagger Lee.mp3", ("TITLE", "Stagger Lee", 0.01))
        assert result.title == "Stagger Lee"


class TestTitleFallback:
    def test_an_unlabelled_basename_falls_back_to_its_stem(self):
        result = run(
            "Nick Cave/Murder Ballads/Stagger Lee.mp3", ("ARTIST", "Nick Cave")
        )
        assert result.title == "Stagger Lee"


class TestBulkDownloads:
    """A bulk playlist download's leading number is a playlist position and
    the long run after it a download id. The model was never trained on them,
    and the containing folder makes it read "Playlist" as the artist."""

    def test_the_prefix_and_the_directory_are_both_dropped(self):
        view = build_view(
            f"{MUSIC_ROOT}/Playlist - Urban - 500604904/"
            "061-1834934-Ogi Feel the Beat-Only On This Planet.mp3",
            MUSIC_ROOT,
        )
        assert view == "Ogi Feel the Beat-Only On This Planet.mp3"

    def test_an_ordinary_leading_number_is_untouched(self):
        view = build_view(
            f"{MUSIC_ROOT}/Murder Ballads/03 - Stagger Lee.mp3", MUSIC_ROOT
        )
        assert view == "Murder Ballads/03 - Stagger Lee.mp3"
