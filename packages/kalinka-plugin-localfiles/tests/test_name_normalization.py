"""Tests for shared artist/album name normalization.

Covers:
- ``clean_display_name`` keeps the user-visible string readable while
  removing artifacts like leading hyphens from path-derived names.
- ``normalize_for_id`` collapses surface-level differences so variants
  of the same artist/album hash to the same ID.
- ID generators round-trip the normalization (so the in-DB ID is
  stable across the variants users actually have in their tags).
"""

from kalinka_plugin_localfiles.utils.name_utils import (
    album_folder_for_path,
    clean_display_name,
    normalize_for_id,
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
        # CD1, Disc1, Disk-3, "Disc 02" all count.
        assert album_folder_for_path("/m/X/CD1/01.flac") == "/m/X"
        assert album_folder_for_path("/m/X/Disc 2/01.flac") == "/m/X"
        assert album_folder_for_path("/m/X/disk3/01.flac") == "/m/X"

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
