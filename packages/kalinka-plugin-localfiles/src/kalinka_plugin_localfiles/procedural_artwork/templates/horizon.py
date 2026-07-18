"""Layered horizon / landscape bands template.

Stacked bands with smooth (low angularity) or jagged (high angularity)
boundary curves, painted top to bottom over a sky gradient, with an
abstract accent disc partially hidden behind the top band.  Boundary
levels and shapes are family-seeded; the edition shifts phases and band
levels slightly.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import CURVE_SAMPLES, geometry_rngs, linear_gradient, mix, shade


def _boundary_curve(
    frng, erng, level: float, angularity: float, strength: float, xs: np.ndarray
) -> np.ndarray:
    """One band boundary in unit space; smooth sinusoids or jagged ridges."""
    # Draw both shape variants' randomness unconditionally so the RNG stream
    # consumption is identical regardless of angularity.
    amps = frng.random(3) * np.array([0.035, 0.02, 0.01])
    freqs = 0.5 + frng.random(3) * 2.5
    phases = frng.random(3)
    ridge_levels = frng.random(9)

    phases = phases + (erng.random(3) - 0.5) * 2.0 * strength * 0.25
    level = level + (float(erng.random()) - 0.5) * 2.0 * strength * 0.03

    smooth = np.sum(
        amps[:, None] * np.sin(2.0 * np.pi * (freqs[:, None] * xs[None, :] + phases[:, None])),
        axis=0,
    )
    if angularity < 0.5:
        return level + smooth
    # Jagged ridge: piecewise-linear interpolation between control points.
    n_points = 5 + int(round(angularity * 6))
    controls = (ridge_levels[:n_points] - 0.5) * 0.10
    control_x = np.linspace(0.0, 1.0, n_points)
    return level + np.interp(xs, control_x, controls)


def render(params, canvas: int) -> Image.Image:
    frng, erng = geometry_rngs(params)
    strength = params.edition_strength
    palette = params.palette

    sky_top = shade(palette[2], 1.35)
    sky_bottom = mix(palette[2], palette[0], 0.5)
    background = linear_gradient(canvas, sky_top, sky_bottom, angle_deg=90.0)
    draw = ImageDraw.Draw(background)

    n_bands = 4 + int(round(params.complexity * 5))
    levels = np.sort(frng.random(n_bands)) * 0.72 + 0.16
    xs = np.linspace(0.0, 1.0, CURVE_SAMPLES)

    # Accent disc in the sky region, partially occluded by the first band.
    disc_x = 0.2 + float(frng.random()) * 0.6
    disc_y = max(0.06, float(levels[0]) - 0.02 - float(frng.random()) * 0.12)
    disc_r = 0.05 + float(frng.random()) * 0.09
    draw.ellipse(
        (
            (disc_x - disc_r) * canvas,
            (disc_y - disc_r) * canvas,
            (disc_x + disc_r) * canvas,
            (disc_y + disc_r) * canvas,
        ),
        fill=palette[4],
    )

    for i in range(n_bands):
        curve = _boundary_curve(
            frng, erng, float(levels[i]), params.angularity, strength, xs
        )
        # Bands darken with depth; cycle the principal palette colors.
        base_color = palette[1 + i % 3]
        color = shade(base_color, 1.05 - (i + 1) * (0.75 / n_bands))
        points = list(zip((xs * canvas).tolist(), (curve * canvas).tolist()))
        points += [(canvas, canvas + 4), (0, canvas + 4)]
        draw.polygon(points, fill=color)

    if params.softness > 0.05:
        background = background.filter(
            ImageFilter.GaussianBlur(radius=params.softness * canvas * 0.003)
        )
    return background
