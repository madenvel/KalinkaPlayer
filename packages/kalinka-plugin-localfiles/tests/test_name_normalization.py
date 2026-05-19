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


class TestAlbumIdStability:
    def test_album_id_collapses_title_variants_under_same_artist(self):
        artist_id = enricher_artist_id("Pink Floyd")
        assert enricher_album_id(
            "The Dark Side of the Moon", artist_id
        ) == enricher_album_id("The Dark Side Of The Moon", artist_id)
        # Trailing whitespace from tag artifacts shouldn't fork the album.
        assert enricher_album_id("Abbey Road ", artist_id) == enricher_album_id(
            "Abbey Road", artist_id
        )

    def test_different_artists_keep_separate_albums(self):
        a = enricher_artist_id("Pink Floyd")
        b = enricher_artist_id("Roxy Music")
        assert enricher_album_id("Greatest Hits", a) != enricher_album_id(
            "Greatest Hits", b
        )

    def test_indexer_and_enricher_agree(self):
        artist_id = enricher_artist_id("Pink Floyd")
        assert indexer_album_id("Animals", artist_id) == enricher_album_id(
            "Animals", artist_id
        )

    def test_unknown_album_short_circuit(self):
        artist_id = enricher_artist_id("Some Artist")
        assert enricher_album_id("Unknown Album", artist_id) == "unknown_album"
        assert enricher_album_id("", artist_id) == "unknown_album"
        # All punctuation normalizes to empty → unknown album.
        assert enricher_album_id("---", artist_id) == "unknown_album"
