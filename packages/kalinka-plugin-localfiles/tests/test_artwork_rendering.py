"""Rendering tests: pixel determinism, sizes, templates, edition similarity."""

import dataclasses

import numpy as np
import pytest

from kalinka_plugin_localfiles.procedural_artwork import (
    AlbumArtworkInput,
    ProceduralArtworkGenerator,
    RenderError,
)
from kalinka_plugin_localfiles.procedural_artwork.templates import TEMPLATE_NAMES

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


def test_render_is_pixel_deterministic():
    gen = ProceduralArtworkGenerator()
    params = gen.resolve_parameters(_album(), size=256)
    first = gen.render(params)
    second = gen.render(params)
    assert first.tobytes() == second.tobytes()
    assert first.mode == "RGB"


@pytest.mark.parametrize("size", [64, 128, 512, 1024])
def test_sizes_render_correctly(size):
    gen = ProceduralArtworkGenerator()
    params = gen.resolve_parameters(_album(), size=size)
    image = gen.render(params)
    assert image.size == (size, size)
    assert image.mode in ("RGB", "RGBA")
    # Not uniformly one color.
    assert float(np.asarray(image).std()) > 2.0


def test_family_identity_preserved_across_sizes():
    gen = ProceduralArtworkGenerator()
    small = gen.resolve_parameters(_album(), size=64)
    large = gen.resolve_parameters(_album(), size=512)
    assert small.family_seed == large.family_seed
    assert small.edition_seed == large.edition_seed
    assert small.template == large.template
    assert small.palette == large.palette


def test_cross_size_composition_related():
    """A 64px render should look like a shrunken 512px render, not a new one."""
    gen = ProceduralArtworkGenerator()
    album = _album()
    small = gen.render(gen.resolve_parameters(album, size=64))
    large = gen.render(gen.resolve_parameters(album, size=512)).resize((64, 64))
    other = gen.render(
        gen.resolve_parameters(
            _album(artist="Iron Meridian", title="Ash Doctrine", genre="metal"),
            size=64,
        )
    )
    small_arr = np.asarray(small, dtype=np.float32)
    same_rms = float(np.sqrt(np.mean((small_arr - np.asarray(large, dtype=np.float32)) ** 2)))
    other_rms = float(np.sqrt(np.mean((small_arr - np.asarray(other, dtype=np.float32)) ** 2)))
    assert same_rms < other_rms


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_each_template_renders(template):
    gen = ProceduralArtworkGenerator()
    base = gen.resolve_parameters(_album(genre=None), size=128)
    params = dataclasses.replace(base, template=template)
    image = gen.render(params)
    assert image.size == (128, 128)
    assert float(np.asarray(image).std()) > 2.0


def test_templates_are_visually_distinct():
    gen = ProceduralArtworkGenerator()
    base = gen.resolve_parameters(_album(genre=None), size=128)
    renders = {
        template: gen.render(dataclasses.replace(base, template=template)).tobytes()
        for template in TEMPLATE_NAMES
    }
    assert len(set(renders.values())) == len(TEMPLATE_NAMES)


def test_unknown_template_raises_render_error():
    gen = ProceduralArtworkGenerator()
    params = dataclasses.replace(
        gen.resolve_parameters(_album(), size=64), template="mandelbrot"
    )
    with pytest.raises(RenderError):
        gen.render(params)


def test_remaster_image_closer_than_unrelated_image():
    """Limited image-distance check on top of the parameter-level assertions."""
    gen = ProceduralArtworkGenerator()
    original = gen.render(gen.resolve_parameters(_album(), size=128))
    remaster = gen.render(
        gen.resolve_parameters(_album(title="Night Currents (2019 Remaster)"), size=128)
    )
    unrelated = gen.render(
        gen.resolve_parameters(
            _album(artist="Iron Meridian", title="Ash Doctrine", genre="metal"),
            size=128,
        )
    )
    base = np.asarray(original, dtype=np.float32)
    rms_remaster = float(np.sqrt(np.mean((base - np.asarray(remaster, dtype=np.float32)) ** 2)))
    rms_unrelated = float(np.sqrt(np.mean((base - np.asarray(unrelated, dtype=np.float32)) ** 2)))
    assert rms_remaster < rms_unrelated
