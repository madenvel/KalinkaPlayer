"""Flowing wave lines template.

Layered sinusoid polylines flow horizontally across a soft vertical
gradient.  Line lanes, wave shapes and widths are family-seeded; the edition
contributes bounded phase/position jitter and slight width scaling.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import CURVE_SAMPLES, geometry_rngs, linear_gradient, shade, stroke_width


def render(params, canvas: int) -> Image.Image:
    frng, erng = geometry_rngs(params)
    strength = params.edition_strength
    palette = params.palette

    background = linear_gradient(
        canvas,
        shade(palette[0], 1.25),
        shade(palette[0], 0.70),
        angle_deg=90.0 + (float(frng.random()) - 0.5) * 30.0,
    )
    draw = ImageDraw.Draw(background)

    n_lines = 5 + int(round(params.complexity * 15))
    lanes = np.sort(frng.random(n_lines)) * 0.9 + 0.05
    xs = np.linspace(0.0, 1.0, CURVE_SAMPLES)
    # Softer styles get larger, slower undulations.
    amp_scale = 0.35 + 0.9 * params.softness

    for i in range(n_lines):
        amps = frng.random(3) * np.array([0.045, 0.025, 0.012]) * amp_scale
        freqs = 0.5 + frng.random(3) * 3.0
        phases = frng.random(3)
        width_unit = 0.0035 + 0.009 * float(frng.random())
        use_accent = float(frng.random()) < 0.15

        phases = phases + (erng.random(3) - 0.5) * 2.0 * strength * 0.25
        lane = float(lanes[i]) + (float(erng.random()) - 0.5) * 2.0 * strength * 0.04
        width_unit *= 1.0 + (float(erng.random()) - 0.5) * 2.0 * strength * 0.3

        ys = lane + np.sum(
            amps[:, None] * np.sin(2.0 * np.pi * (freqs[:, None] * xs[None, :] + phases[:, None])),
            axis=0,
        )
        points = list(zip((xs * canvas).tolist(), (ys * canvas).tolist()))
        color = palette[4] if use_accent else palette[1 + i % 3]
        draw.line(points, fill=color, width=stroke_width(width_unit, canvas), joint="curve")

    if params.softness > 0.05:
        background = background.filter(
            ImageFilter.GaussianBlur(radius=params.softness * canvas * 0.0035)
        )
    return background
