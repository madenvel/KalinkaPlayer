"""Identity tests: normalization, edition markers, family/edition seeds."""

import numpy as np
import pytest

from kalinka_plugin_localfiles.procedural_artwork import (
    AlbumArtworkInput,
    InvalidEmbeddingError,
    InvalidInputError,
    InvalidSizeError,
    ProceduralArtworkGenerator,
    UnsupportedFormatError,
)
from kalinka_plugin_localfiles.procedural_artwork.identity import (
    EDITION_STRENGTHS,
    build_identity,
    canonical_track_signature,
    normalize_text,
    strip_edition_markers,
)

BASE_TRACKS = tuple(f"Track Number {i}" for i in range(1, 13))


def _album(**kwargs) -> AlbumArtworkInput:
    defaults = dict(
        artist="Aurora Fields",
        title="Night Currents",
        genre="ambient",
        track_titles=BASE_TRACKS,
    )
    defaults.update(kwargs)
    return AlbumArtworkInput(**defaults)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalize_text_case_whitespace_punctuation():
    assert normalize_text("  The  WALL!! ") == "the wall"
    assert normalize_text("AC/DC") == "ac dc"
    assert normalize_text("Sigur Rós") == "sigur ros"
    assert normalize_text("Björk & Friends") == "bjork and friends"


def test_normalize_text_preserves_non_latin():
    assert normalize_text("Кино") == "кино"


def test_strip_edition_markers_variants():
    cases = {
        "Abbey Road (2019 Remaster)": "Abbey Road",
        "Abbey Road [Remastered]": "Abbey Road",
        "Abbey Road - 2019 Remaster": "Abbey Road",
        "OK Computer (Deluxe Edition)": "OK Computer",
        "Blue Train (Expanded Edition)": "Blue Train",
        "Rumours (35th Anniversary Edition)": "Rumours",
        "Pet Sounds (Mono)": "Pet Sounds",
        "Pet Sounds (Stereo)": "Pet Sounds",
        "Loveless (Special Edition)": "Loveless",
        "Kid A (Collector's Edition)": "Kid A",
        "In Rainbows (Bonus Track Edition)": "In Rainbows",
    }
    for full, base in cases.items():
        stripped, markers = strip_edition_markers(full)
        assert stripped == base, full
        assert markers, full


def test_strip_edition_markers_stacked():
    base, markers = strip_edition_markers("OK Computer (Deluxe Edition) [2017 Remaster]")
    assert base == "OK Computer"
    assert len(markers) == 2


def test_meaningful_parentheses_preserved():
    for title in (
        "Live at Wembley (Live)",
        "Homework (Instrumental Versions)",
        "The Wall (Disc 1)",
        "Untrue (Acoustic Sessions)",
    ):
        base, markers = strip_edition_markers(title)
        assert base == title
        assert markers == ()


def test_edition_strength_values():
    assert EDITION_STRENGTHS["remaster"] == pytest.approx(0.05)
    assert EDITION_STRENGTHS["mono_stereo"] == pytest.approx(0.07)
    assert EDITION_STRENGTHS["alternate"] == pytest.approx(0.08)
    assert EDITION_STRENGTHS["anniversary"] == pytest.approx(0.10)
    assert EDITION_STRENGTHS["bonus"] == pytest.approx(0.12)
    assert EDITION_STRENGTHS["deluxe"] == pytest.approx(0.14)
    assert EDITION_STRENGTHS["original"] == 0.0


def test_edition_classification_from_title():
    remaster = build_identity(_album(title="Night Currents (2019 Remaster)"), 1)
    assert remaster.edition_kind == "remaster"
    assert remaster.edition_strength == pytest.approx(0.05)

    deluxe = build_identity(_album(title="Night Currents (Deluxe Edition)"), 1)
    assert deluxe.edition_kind == "deluxe"
    assert deluxe.edition_strength == pytest.approx(0.14)

    original = build_identity(_album(), 1)
    assert original.edition_kind == "original"
    assert original.edition_strength == 0.0

    alternate = build_identity(_album(release_id="rel-123"), 1)
    assert alternate.edition_kind == "alternate"
    assert alternate.edition_strength == pytest.approx(0.08)


# ---------------------------------------------------------------------------
# Track signature
# ---------------------------------------------------------------------------


def test_track_signature_normalizes_titles():
    sig_a = canonical_track_signature(("Intro!", "The  Song"))
    sig_b = canonical_track_signature(("intro", "the song"))
    assert sig_a == sig_b


def test_track_signature_caps_at_limit():
    base = tuple(f"t{i}" for i in range(12))
    assert canonical_track_signature(base) == canonical_track_signature(
        base + ("bonus one", "bonus two")
    )


# ---------------------------------------------------------------------------
# Family behaviour
# ---------------------------------------------------------------------------


def test_same_release_group_id_same_family_seed():
    a = build_identity(_album(release_group_id="rg-1"), 1)
    b = build_identity(
        _album(title="Night Currents (Deluxe Edition)", release_group_id="rg-1"), 1
    )
    assert a.family_seed == b.family_seed
    assert a.family_key == b.family_key


def test_release_group_id_takes_precedence_over_metadata():
    a = build_identity(_album(release_group_id="rg-1"), 1)
    b = build_identity(
        _album(artist="Different Artist", title="Different Title", release_group_id="rg-1"),
        1,
    )
    assert a.family_seed == b.family_seed


def test_fallback_family_original_and_remaster_share_seed():
    original = build_identity(_album(), 1)
    remaster = build_identity(_album(title="Night Currents (2019 Remaster)"), 1)
    assert original.family_seed == remaster.family_seed


def test_fallback_family_original_and_deluxe_share_seed():
    original = build_identity(_album(), 1)
    deluxe = build_identity(
        _album(
            title="Night Currents (Deluxe Edition)",
            track_titles=BASE_TRACKS + ("Bonus A", "Bonus B"),
        ),
        1,
    )
    assert original.family_seed == deluxe.family_seed


def test_appended_bonus_tracks_keep_fallback_family():
    original = build_identity(_album(), 1)
    with_bonus = build_identity(
        _album(track_titles=BASE_TRACKS + ("Hidden Gem", "Outro Jam")), 1
    )
    assert original.family_seed == with_bonus.family_seed


def test_editions_have_distinct_edition_seeds():
    original = build_identity(_album(), 1)
    remaster = build_identity(_album(title="Night Currents (2019 Remaster)"), 1)
    deluxe = build_identity(_album(title="Night Currents (Deluxe Edition)"), 1)
    seeds = {original.edition_seed, remaster.edition_seed, deluxe.edition_seed}
    assert len(seeds) == 3


def test_release_id_changes_edition_seed_only():
    a = build_identity(_album(release_id="rel-1"), 1)
    b = build_identity(_album(release_id="rel-2"), 1)
    assert a.family_seed == b.family_seed
    assert a.edition_seed != b.edition_seed


def test_unrelated_albums_different_family_seeds():
    a = build_identity(_album(), 1)
    b = build_identity(_album(artist="Voltage Union", title="Steel Habits"), 1)
    c = build_identity(_album(title="Different Album Entirely"), 1)
    assert len({a.family_seed, b.family_seed, c.family_seed}) == 3


def test_generator_version_changes_seeds():
    v1 = build_identity(_album(), 1)
    v2 = build_identity(_album(), 2)
    assert v1.family_seed != v2.family_seed
    assert v1.edition_seed != v2.edition_seed


def test_missing_optional_metadata_is_fine():
    identity = build_identity(AlbumArtworkInput(artist="Solo", title="Album"), 1)
    assert identity.family_seed > 0
    assert identity.edition_kind == "original"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_invalid_metadata_types_rejected():
    gen = ProceduralArtworkGenerator()
    with pytest.raises(InvalidInputError):
        gen.resolve_parameters(AlbumArtworkInput(artist=123, title="X"))
    with pytest.raises(InvalidInputError):
        gen.resolve_parameters(AlbumArtworkInput(artist="X", title=None))
    with pytest.raises(InvalidInputError):
        gen.resolve_parameters(AlbumArtworkInput(artist="X", title="Y", genre=5))
    with pytest.raises(InvalidInputError):
        gen.resolve_parameters(
            AlbumArtworkInput(artist="X", title="Y", track_titles=("a", 2))
        )
    with pytest.raises(InvalidInputError):
        gen.resolve_parameters("not an album")
    with pytest.raises(InvalidInputError):
        gen.resolve_parameters(AlbumArtworkInput(artist="", title="   "))


def test_invalid_sizes_rejected():
    gen = ProceduralArtworkGenerator()
    album = _album()
    for bad in ("512", 512.0, True, 0, -5, 31, 4096):
        with pytest.raises(InvalidSizeError):
            gen.resolve_parameters(album, size=bad)
    with pytest.raises(InvalidSizeError):
        ProceduralArtworkGenerator(default_size=10)


def test_invalid_embeddings_rejected():
    gen = ProceduralArtworkGenerator()
    bad_embeddings = [
        np.zeros((2, 2)),
        np.array([]),
        np.array([1.0, np.nan]),
        np.array([np.inf, 0.0]),
        np.array(["a", "b"]),
        [0.1, 0.2],
    ]
    for emb in bad_embeddings:
        with pytest.raises(InvalidEmbeddingError):
            gen.resolve_parameters(_album(album_embedding=emb))


def test_unsupported_format_rejected():
    gen = ProceduralArtworkGenerator()
    with pytest.raises(UnsupportedFormatError):
        gen._normalize_format("BMP")
    with pytest.raises(UnsupportedFormatError):
        gen._normalize_format(42)
