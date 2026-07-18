"""Parameter-resolution tests: determinism, edition bounds, genre, semantics."""

import dataclasses
import math

import numpy as np
import pytest

from kalinka_plugin_localfiles.procedural_artwork import (
    AlbumArtworkInput,
    ProceduralArtworkGenerator,
)
from kalinka_plugin_localfiles.procedural_artwork.styles import (
    GENRE_PROFILES,
    match_genre_profile,
)

BASE_TRACKS = tuple(f"Track Number {i}" for i in range(1, 13))

SCALAR_FIELDS = (
    "hue",
    "saturation",
    "lightness",
    "softness",
    "complexity",
    "contrast",
    "grain",
    "angularity",
    "texture_density",
)


def _album(**kwargs) -> AlbumArtworkInput:
    defaults = dict(
        artist="Aurora Fields",
        title="Night Currents",
        genre="ambient",
        track_titles=BASE_TRACKS,
    )
    defaults.update(kwargs)
    return AlbumArtworkInput(**defaults)


def _param_distance(a, b) -> float:
    """Simple parameter-space distance; hue is circular."""
    total = 0.0
    for field in SCALAR_FIELDS:
        delta = abs(getattr(a, field) - getattr(b, field))
        if field == "hue":
            delta = min(delta, 1.0 - delta)
        total += delta * delta
    total += 0.0 if a.template == b.template else 4.0
    return math.sqrt(total)


def test_repeated_resolution_identical():
    gen = ProceduralArtworkGenerator()
    album = _album()
    assert gen.resolve_parameters(album) == gen.resolve_parameters(album)


def test_fresh_generator_instances_agree():
    album = _album()
    a = ProceduralArtworkGenerator().resolve_parameters(album)
    b = ProceduralArtworkGenerator().resolve_parameters(album)
    assert a == b


def test_size_changes_only_resolved_size():
    gen = ProceduralArtworkGenerator()
    album = _album()
    small = dataclasses.asdict(gen.resolve_parameters(album, size=64))
    large = dataclasses.asdict(gen.resolve_parameters(album, size=1024))
    differing = {k for k in small if small[k] != large[k]}
    assert differing == {"resolved_size"}


def test_default_size_used_when_unspecified():
    gen = ProceduralArtworkGenerator(default_size=256)
    assert gen.resolve_parameters(_album()).resolved_size == 256
    assert gen.resolve_parameters(_album(), size=600).resolved_size == 600


def test_read_only_attributes():
    gen = ProceduralArtworkGenerator(default_size=256, generator_version=1)
    assert gen.default_size == 256
    assert gen.generator_version == 1
    with pytest.raises(AttributeError):
        gen.default_size = 128
    with pytest.raises(AttributeError):
        gen.generator_version = 2


# ---------------------------------------------------------------------------
# Edition relationships (parameter-level, not image-similarity)
# ---------------------------------------------------------------------------


def test_edition_offsets_within_strength_bounds():
    gen = ProceduralArtworkGenerator()
    original = gen.resolve_parameters(_album())
    assert original.edition_strength == 0.0
    for title, strength in (
        ("Night Currents (2019 Remaster)", 0.05),
        ("Night Currents (Deluxe Edition)", 0.14),
    ):
        edition = gen.resolve_parameters(_album(title=title))
        assert edition.edition_strength == pytest.approx(strength)
        # Style scalars are interpolated within the shared profile range, so
        # each offset from the family value is bounded by strength * width
        # and width <= 1 for every range.
        for field in SCALAR_FIELDS:
            delta = abs(getattr(edition, field) - getattr(original, field))
            if field == "hue":
                delta = min(delta, 1.0 - delta)
            assert delta <= strength + 1e-9, (title, field, delta)


def test_remaster_closer_to_original_than_unrelated():
    gen = ProceduralArtworkGenerator()
    original = gen.resolve_parameters(_album())
    remaster = gen.resolve_parameters(_album(title="Night Currents (2019 Remaster)"))
    unrelated = gen.resolve_parameters(
        _album(artist="Iron Meridian", title="Ash Doctrine", genre="metal")
    )
    assert _param_distance(original, remaster) < _param_distance(original, unrelated)


def test_deluxe_differs_more_than_remaster_but_keeps_family_look():
    gen = ProceduralArtworkGenerator()
    original = gen.resolve_parameters(_album())
    remaster = gen.resolve_parameters(_album(title="Night Currents (2019 Remaster)"))
    deluxe = gen.resolve_parameters(_album(title="Night Currents (Deluxe Edition)"))

    assert original.family_seed == remaster.family_seed == deluxe.family_seed
    # Family look preserved: same template, principal palette colors close.
    assert original.template == remaster.template == deluxe.template
    for edition in (remaster, deluxe):
        for color_a, color_b in zip(original.palette[:4], edition.palette[:4]):
            for chan_a, chan_b in zip(color_a, color_b):
                assert abs(chan_a - chan_b) <= 60

    dist_remaster = _param_distance(original, remaster)
    dist_deluxe = _param_distance(original, deluxe)
    assert dist_remaster <= dist_deluxe + 1e-9


# ---------------------------------------------------------------------------
# Genre profiles
# ---------------------------------------------------------------------------


def test_known_genres_have_profiles():
    for genre in (
        "ambient",
        "electronic",
        "techno",
        "jazz",
        "classical",
        "rock",
        "hip hop",
        "metal",
        "folk",
    ):
        assert match_genre_profile(genre).name == genre


def test_multi_value_genre_matching():
    assert match_genre_profile("alternative rock").name == "rock"
    assert match_genre_profile("jazz fusion").name == "jazz"
    blended = match_genre_profile("ambient electronic")
    assert "ambient" in blended.name and "electronic" in blended.name
    assert match_genre_profile("Hip-Hop").name == "hip hop"
    assert match_genre_profile("something nobody tagged").name == "default"
    assert match_genre_profile(None).name == "default"


def test_genre_guides_template_and_contrast():
    gen = ProceduralArtworkGenerator()
    ambient = gen.resolve_parameters(_album(genre="ambient"))
    metal = gen.resolve_parameters(
        _album(artist="Iron Meridian", title="Ash Doctrine", genre="metal")
    )
    assert ambient.template in GENRE_PROFILES["ambient"].templates
    assert metal.template in GENRE_PROFILES["metal"].templates
    # Profile contrast ranges are disjoint (ambient <= 0.35 < 0.70 <= metal).
    assert metal.contrast > ambient.contrast


# ---------------------------------------------------------------------------
# Semantic (embedding) style
# ---------------------------------------------------------------------------


def _unit_vector(seed: int, dim: int = 64) -> np.ndarray:
    rng = np.random.Generator(np.random.PCG64(seed))
    vec = rng.standard_normal(dim)
    return vec / np.linalg.norm(vec)


def test_embedding_style_is_deterministic():
    gen = ProceduralArtworkGenerator()
    emb = _unit_vector(7)
    a = gen.resolve_parameters(_album(album_embedding=emb, embedding_version="v1"))
    b = gen.resolve_parameters(_album(album_embedding=emb.copy(), embedding_version="v1"))
    assert a == b


def test_similar_embeddings_map_to_nearby_styles():
    gen = ProceduralArtworkGenerator()
    base = _unit_vector(7)
    near = base + 0.02 * _unit_vector(8)
    far = _unit_vector(99)
    p_base = gen.resolve_parameters(_album(album_embedding=base))
    p_near = gen.resolve_parameters(_album(album_embedding=near))
    p_far = gen.resolve_parameters(_album(album_embedding=far))
    assert _param_distance(p_base, p_near) < _param_distance(p_base, p_far)


def test_embedding_does_not_change_family_identity():
    gen = ProceduralArtworkGenerator()
    plain = gen.resolve_parameters(_album())
    with_embedding = gen.resolve_parameters(_album(album_embedding=_unit_vector(7)))
    assert plain.family_seed == with_embedding.family_seed
    assert plain.edition_seed == with_embedding.edition_seed
    assert plain.template == with_embedding.template


def test_embedding_input_not_mutated():
    gen = ProceduralArtworkGenerator()
    emb = _unit_vector(7)
    snapshot = emb.copy()
    gen.resolve_parameters(_album(album_embedding=emb))
    np.testing.assert_array_equal(emb, snapshot)
