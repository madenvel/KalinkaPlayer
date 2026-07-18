"""Template renderers and shared drawing helpers.

Each template module exposes ``render(params, canvas) -> PIL.Image.Image``
producing an RGB image of ``canvas`` x ``canvas`` pixels.  ``canvas`` may be
a supersampled multiple of the requested output size; all geometry is
computed in unit coordinates ``[0, 1]`` and scaled at draw time, so the
composition is identical at every output size.

Conventions shared by all templates:

* family geometry comes from ``rng_for(family_seed, DOMAIN_GEOMETRY)``;
* edition jitter comes from ``rng_for(edition_seed, DOMAIN_EDITION_GEOMETRY)``
  and is always scaled by ``edition_strength`` so the original release
  (strength 0) shows the pure family composition;
* RNG draw counts depend only on resolved style values (never on size), so
  the same parameter set consumes the same random stream everywhere.
"""

import numpy as np
from PIL import Image

from ..identity import rng_for

DOMAIN_GEOMETRY = 21
DOMAIN_EDITION_GEOMETRY = 22
DOMAIN_GRAIN = 23

TEMPLATE_NAMES = ("waves", "orbits", "horizon", "blocks", "slashes")

# Fixed sample count for unit-space curves; drawing scales to any canvas.
CURVE_SAMPLES = 193


def geometry_rngs(params) -> tuple[np.random.Generator, np.random.Generator]:
    """Return the (family, edition) geometry RNG streams for a parameter set."""
    return (
        rng_for(params.family_seed, DOMAIN_GEOMETRY),
        rng_for(params.edition_seed, DOMAIN_EDITION_GEOMETRY),
    )


def shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    """Darken (< 1) or lighten (> 1) an RGB color, clamped to [0, 255]."""
    return tuple(int(max(0, min(255, round(c * factor)))) for c in color)


def mix(
    color_a: tuple[int, int, int], color_b: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    """Linear blend of two RGB colors, ``t`` in [0, 1]."""
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(color_a, color_b))


def linear_gradient(
    canvas: int,
    color_start: tuple[int, int, int],
    color_end: tuple[int, int, int],
    angle_deg: float,
) -> Image.Image:
    """RGB linear gradient across the canvas along the given angle."""
    theta = np.deg2rad(angle_deg)
    axis = np.linspace(0.0, 1.0, canvas, dtype=np.float32)
    t = np.cos(theta, dtype=np.float32) * axis[None, :] + np.sin(theta, dtype=np.float32) * axis[:, None]
    t -= t.min()
    peak = float(t.max())
    if peak > 0.0:
        t /= peak
    start = np.asarray(color_start, dtype=np.float32)
    end = np.asarray(color_end, dtype=np.float32)
    arr = start + t[:, :, None] * (end - start)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def radial_gradient(
    canvas: int,
    center: tuple[float, float],
    color_inner: tuple[int, int, int],
    color_outer: tuple[int, int, int],
    radius: float = 0.9,
) -> Image.Image:
    """RGB radial gradient; ``center`` and ``radius`` in unit coordinates."""
    axis = np.linspace(0.0, 1.0, canvas, dtype=np.float32)
    dx = axis[None, :] - np.float32(center[0])
    dy = axis[:, None] - np.float32(center[1])
    t = np.sqrt(dx * dx + dy * dy) / np.float32(max(radius, 1e-6))
    np.clip(t, 0.0, 1.0, out=t)
    inner = np.asarray(color_inner, dtype=np.float32)
    outer = np.asarray(color_outer, dtype=np.float32)
    arr = inner + t[:, :, None] * (outer - inner)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def stroke_width(unit_width: float, canvas: int) -> int:
    """Convert a unit-space stroke width to pixels (at least 1)."""
    return max(1, int(round(unit_width * canvas)))


# Imported at the bottom so the submodules can import the helpers above.
from . import blocks, horizon, orbits, slashes, waves  # noqa: E402

RENDERERS = {
    "waves": waves.render,
    "orbits": orbits.render,
    "horizon": horizon.render,
    "blocks": blocks.render,
    "slashes": slashes.render,
}
