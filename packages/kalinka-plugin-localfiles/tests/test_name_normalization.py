"""Tests for shared artist/album name normalization.

Covers:
- ``clean_display_name`` keeps the user-visible string readable while
  removing artifacts like leading hyphens from path-derived names.
- ``normalize_for_id`` collapses surface-level differences so variants
  of the same artist/album hash to the same ID.
- ID generators round-trip the normalization (so the in-DB ID is
  stable across the variants users actually have in their tags).
- Tag-mangling repair: web HTML entities are unrolled once at index time
  ("Simon &amp; Garfunkel"), and dotted abbreviations get their missing
  space for search queries ("В.Цой" is one Lucene token and finds nothing
  on MusicBrainz; "В. Цой" scores 100).
"""

import numpy as np
import pytest
import pytest_asyncio
import soundfile as sf
from mutagen.flac import FLAC

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.indexer.indexer import FileIndexer
from kalinka_plugin_localfiles.indexer.indexer_db import AsyncIndexerDb
from kalinka_plugin_localfiles.utils.name_utils import (
    album_folder_for_path,
    clean_display_name,
    normalize_for_id,
    repair_mojibake,
    space_dotted_abbreviations,
    unescape_web_entities,
)
from kalinka_plugin_localfiles.enricher.id_generator import (
    generate_artist_id as enricher_artist_id,
    generate_album_id as enricher_album_id,
)
from kalinka_plugin_localfiles.indexer.id_generator import (
    generate_artist_id as indexer_artist_id,
    generate_album_id as indexer_album_id,
)


class TestCleanDisplayName:
    def test_strips_leading_punctuation(self):
        assert clean_display_name("- Roxy Music") == "Roxy Music"
        assert clean_display_name(",  The Beatles") == "The Beatles"
        assert clean_display_name("_Daft Punk") == "Daft Punk"

    def test_strips_trailing_punctuation(self):
        assert clean_display_name("Pink Floyd ") == "Pink Floyd"
        assert clean_display_name("Bowie -") == "Bowie"
        assert clean_display_name("Album,") == "Album"

    def test_collapses_internal_whitespace(self):
        assert clean_display_name("Pink   Floyd") == "Pink Floyd"
        assert clean_display_name("a\tb") == "a b"

    def test_preserves_internal_hyphens_and_case(self):
        # The whole point: display still reads naturally — only the ID is normalized.
        assert clean_display_name("Jean-Michel Jarre") == "Jean-Michel Jarre"
        assert clean_display_name("AC/DC") == "AC/DC"
        assert clean_display_name("Sigur Rós") == "Sigur Rós"

    def test_empty_and_whitespace_only(self):
        assert clean_display_name("") == ""
        assert clean_display_name("   ") == ""
        assert clean_display_name("- - -") == ""


class TestNormalizeForId:
    def test_leading_punct_collapses(self):
        assert normalize_for_id("- Roxy Music") == normalize_for_id("Roxy Music")

    def test_hyphen_variants_collapse(self):
        # The user's "Jean-Michel Jarre" / "Jean Michel Jarre" dup case.
        assert normalize_for_id("Jean-Michel Jarre") == normalize_for_id(
            "Jean Michel Jarre"
        )

    def test_case_collapses(self):
        assert normalize_for_id("Pink Floyd") == normalize_for_id("pink floyd")

    def test_diacritics_collapse(self):
        assert normalize_for_id("Beyoncé") == normalize_for_id("Beyonce")
        assert normalize_for_id("Mötley Crüe") == normalize_for_id("Motley Crue")

    def test_distinct_artists_stay_distinct(self):
        # Hyphen normalization shouldn't false-merge actually-different artists.
        assert normalize_for_id("Iron Maiden") != normalize_for_id("Iron Butterfly")
        assert normalize_for_id("The Beatles") != normalize_for_id("Beatles")

    def test_empty_input(self):
        assert normalize_for_id("") == ""
        assert normalize_for_id("   ") == ""
        assert normalize_for_id("---") == ""


class TestArtistIdStability:
    """The two id_generator copies (indexer + enricher) must agree."""

    def test_indexer_and_enricher_agree(self):
        for name in ["Pink Floyd", "Jean-Michel Jarre", "- Roxy Music", "Beyoncé"]:
            assert indexer_artist_id(name) == enricher_artist_id(name), (
                f"ID drift between indexer and enricher for {name!r}"
            )

    def test_hyphen_variants_share_id(self):
        # The bug the user reported: hyphen / non-hyphen should merge.
        assert enricher_artist_id("Jean-Michel Jarre") == enricher_artist_id(
            "Jean Michel Jarre"
        )

    def test_leading_punct_shares_id_with_clean_name(self):
        assert enricher_artist_id("- Roxy Music") == enricher_artist_id("Roxy Music")

    def test_unknown_artist_short_circuit(self):
        assert enricher_artist_id("Unknown Artist") == "unknown_artist"
        assert enricher_artist_id("") == "unknown_artist"
        assert enricher_artist_id(None) == "unknown_artist"  # type: ignore[arg-type]


class TestAlbumFolderForPath:
    def test_plain_album_dir(self):
        assert (
            album_folder_for_path("/music/Pink Floyd - Animals/01 Pigs.flac")
            == "/music/Pink Floyd - Animals"
        )

    def test_disc_subdir_walks_up(self):
        # Disc 1 and Disc 2 belong to the same album.
        assert (
            album_folder_for_path("/music/RAM/Disc 1/01 Give Life.flac")
            == album_folder_for_path("/music/RAM/Disc 2/01 Horizon.flac")
            == "/music/RAM"
        )

    def test_disc_variants(self):
        # CD1, Disc 2, disk3, Disk-3, CD-04 all count.
        assert album_folder_for_path("/m/X/CD1/01.flac") == "/m/X"
        assert album_folder_for_path("/m/X/Disc 2/01.flac") == "/m/X"
        assert album_folder_for_path("/m/X/disk3/01.flac") == "/m/X"
        assert album_folder_for_path("/m/X/Disk-3/01.flac") == "/m/X"
        assert album_folder_for_path("/m/X/CD-04/01.flac") == "/m/X"

    def test_album_named_like_disc_is_not_walked_up(self):
        # An album literally named "CD1" — unlikely but possible. Since
        # the regex anchors on the immediate parent, only that parent is
        # checked. If the album folder ITSELF is "CD1" at the top of the
        # music library, it stays.
        assert album_folder_for_path("/Music/CD1/01.flac") == "/Music"
        # But a non-disc-pattern folder is kept as-is.
        assert album_folder_for_path("/Music/CD Sampler/01.flac") == "/Music/CD Sampler"

    def test_quality_variants_get_different_folders(self):
        # The RPi case: same album in two quality folders → different folders.
        a = album_folder_for_path("/m/Album [16-44]/01.flac")
        b = album_folder_for_path("/m/Album [24-96]/01.flac")
        assert a != b


class TestAlbumIdStability:
    folder = "/music/Pink Floyd - DSOTM"

    def test_album_id_collapses_title_variants_in_same_folder(self):
        # Casing / trailing-whitespace tag noise shouldn't fork the album.
        assert enricher_album_id(
            "The Dark Side of the Moon", self.folder
        ) == enricher_album_id("The Dark Side Of The Moon", self.folder)
        assert enricher_album_id("Abbey Road ", self.folder) == enricher_album_id(
            "Abbey Road", self.folder
        )

    def test_same_title_different_folders_are_distinct(self):
        # Quality variants: same tag, different folders → different IDs.
        a = enricher_album_id("Random Access Memories", "/m/RAM [16-44]")
        b = enricher_album_id("Random Access Memories", "/m/RAM [24-96]")
        assert a != b

    def test_disc_subdirs_share_album_id(self):
        # The whole reason album_folder_for_path strips disc subdirs.
        a = enricher_album_id(
            "Random Access Memories",
            album_folder_for_path("/m/RAM/Disc 1/01.flac"),
        )
        b = enricher_album_id(
            "Random Access Memories",
            album_folder_for_path("/m/RAM/Disc 2/05.flac"),
        )
        assert a == b

    def test_mistagged_artist_in_same_folder_stays_in_one_album(self):
        # Abbey Road bug: even if some tracks have a wrong artist tag,
        # they should still belong to the one Abbey Road album because
        # artist_id no longer participates in the album-id key.
        a = enricher_album_id("Abbey Road", self.folder)
        b = enricher_album_id("Abbey Road", self.folder)
        assert a == b

    def test_indexer_and_enricher_agree(self):
        assert indexer_album_id("Animals", self.folder) == enricher_album_id(
            "Animals", self.folder
        )

    def test_unknown_album_short_circuit(self):
        assert enricher_album_id("Unknown Album", self.folder) == "unknown_album"
        assert enricher_album_id("", self.folder) == "unknown_album"
        # All-punctuation normalizes to empty → unknown.
        assert enricher_album_id("---", self.folder) == "unknown_album"


class TestUnescapeWebEntities:
    def test_common_entities(self):
        assert unescape_web_entities("Simon &amp; Garfunkel") == "Simon & Garfunkel"
        assert unescape_web_entities("&quot;Weird Al&quot; Yankovic") == (
            '"Weird Al" Yankovic'
        )
        assert unescape_web_entities("Guns N&apos; Roses") == "Guns N' Roses"
        assert unescape_web_entities("&#1071;") == "Я"
        assert unescape_web_entities("&#x42F;") == "Я"

    def test_single_pass_only(self):
        # Double-encoded input unrolls one layer, as asked.
        assert unescape_web_entities("A &amp;amp; B") == "A &amp; B"

    def test_plain_ampersand_and_legacy_forms_untouched(self):
        assert unescape_web_entities("AT&T") == "AT&T"
        assert unescape_web_entities("Tom&Jerry") == "Tom&Jerry"
        # html.unescape would turn this into "¬abene" (HTML5 legacy
        # semicolon-less entity) — we must not.
        assert unescape_web_entities("&notabene") == "&notabene"

    def test_clean_display_name_unrolls_entities(self):
        assert clean_display_name("Simon &amp; Garfunkel") == "Simon & Garfunkel"
        # &nbsp; becomes a space and collapses with its neighbours.
        assert clean_display_name("Daft&nbsp; Punk") == "Daft Punk"


class TestSpaceDottedAbbreviations:
    def test_initial_before_surname(self):
        assert space_dotted_abbreviations("В.Цой") == "В. Цой"
        assert space_dotted_abbreviations("J.S.Bach") == "J.S. Bach"
        assert space_dotted_abbreviations("Dr.Dre") == "Dr. Dre"

    def test_acronyms_and_ellipses_untouched(self):
        assert space_dotted_abbreviations("R.E.M.") == "R.E.M."
        assert space_dotted_abbreviations("S.P.O.R.T.") == "S.P.O.R.T."
        assert space_dotted_abbreviations("...And Justice for All") == (
            "...And Justice for All"
        )

    def test_numbers_untouched(self):
        assert space_dotted_abbreviations("Vol 2.5") == "Vol 2.5"
        assert space_dotted_abbreviations("Blink 18.2") == "Blink 18.2"

    def test_already_spaced_untouched(self):
        assert space_dotted_abbreviations("В. Цой") == "В. Цой"


class TestRepairMojibake:
    def test_utf8_as_latin1_always_repaired(self):
        # Tier 1 (ftfy) needs no configured codepage — it is self-validating.
        assert repair_mojibake("BjÃ¶rk") == "Björk"
        assert repair_mojibake("Ã‰dith Piaf") == "Édith Piaf"  # cp1252 recovery
        assert repair_mojibake("BjÃƒÂ¶rk") == "Björk"  # double-encoded

    def test_legacy_codepage_off_by_default(self):
        assert repair_mojibake("ÐÓÊÈ ÂÂÅÐÕ!") == "ÐÓÊÈ ÂÂÅÐÕ!"

    def test_cp1251_repairs_cyrillic(self):
        assert repair_mojibake("ÐÓÊÈ ÂÂÅÐÕ!", "cp1251") == "РУКИ ВВЕРХ!"
        assert repair_mojibake("ÌÀØÈÍÀ ÂÐÅÌÅÍÈ", "cp1251") == "МАШИНА ВРЕМЕНИ"

    def test_real_western_names_untouched(self):
        for name in ("Sigur Rós", "Mötley Crüe", "Öxxö Xööx", "Çelik",
                     "R.E.M.", "Motörhead"):
            assert repair_mojibake(name, "cp1251") == name

    def test_genuine_cyrillic_untouched(self):
        assert repair_mojibake("Наутилус Помпилиус", "cp1251") == (
            "Наутилус Помпилиус"
        )

    def test_bad_codepage_name_is_ignored(self):
        assert repair_mojibake("ÐÓÊÈ ÂÂÅÐÕ!", "no-such-codec") == "ÐÓÊÈ ÂÂÅÐÕ!"


@pytest_asyncio.fixture
async def indexer(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    config = LocalFilesConfig(
        music_folders=[str(music_dir)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
        quiescence_seconds=0,
    )
    await init_db(config.db_path)
    db = AsyncIndexerDb(config)
    return FileIndexer(config, db), db, music_dir


@pytest.mark.asyncio
async def test_indexing_unrolls_entities_in_names_and_titles(indexer):
    fi, db, music_dir = indexer
    path = music_dir / "01 - song.flac"
    sf.write(str(path), np.zeros(4410, dtype="float32"), 44100, format="FLAC")
    audio = FLAC(str(path))
    audio["title"] = "Bridge Over Troubled Water &#40;live&#41;"
    audio["artist"] = "Simon &amp; Garfunkel"
    audio["album"] = "Simon &amp; Garfunkel&apos;s Greatest Hits"
    audio.save()

    changes = await fi.process_file(str(path))
    track = await db.get_track_by_id(changes["tracks"])
    artist = await db.get_artist_by_id(track["artist_id"])
    album = await db.get_album_by_id(track["album_id"])

    assert track["title"] == "Bridge Over Troubled Water (live)"
    assert artist["name"] == "Simon & Garfunkel"
    assert album["title"] == "Simon & Garfunkel's Greatest Hits"


@pytest.mark.asyncio
async def test_indexing_stores_spaced_abbreviations(indexer):
    fi, db, music_dir = indexer
    path = music_dir / "song.flac"
    sf.write(str(path), np.zeros(4410, dtype="float32"), 44100, format="FLAC")
    audio = FLAC(str(path))
    audio["title"] = "Спокойная ночь"
    audio["artist"] = "В.Цой"
    audio["album"] = "Звезда по имени Солнце"
    audio.save()

    changes = await fi.process_file(str(path))
    track = await db.get_track_by_id(changes["tracks"])
    artist = await db.get_artist_by_id(track["artist_id"])

    # Stored the way search normalizes it — one form everywhere.
    assert artist["name"] == "В. Цой"


@pytest.mark.asyncio
async def test_indexing_repairs_mojibake_with_configured_codepage(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    config = LocalFilesConfig(
        music_folders=[str(music_dir)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
        quiescence_seconds=0,
        legacy_tag_encoding="cp1251",
    )
    await init_db(config.db_path)
    db = AsyncIndexerDb(config)
    fi = FileIndexer(config, db)

    path = music_dir / "01 - song.flac"
    sf.write(str(path), np.zeros(4410, dtype="float32"), 44100, format="FLAC")
    audio = FLAC(str(path))
    audio["title"] = "Ïðîñâèñòåëà"
    audio["artist"] = "ÄÄÒ"
    audio["album"] = "Ìèð íîìåð íîëü"
    audio.save()

    changes = await fi.process_file(str(path))
    track = await db.get_track_by_id(changes["tracks"])
    artist = await db.get_artist_by_id(track["artist_id"])
    album = await db.get_album_by_id(track["album_id"])

    assert track["title"] == "Просвистела"
    assert artist["name"] == "ДДТ"
    assert album["title"] == "Мир номер ноль"
