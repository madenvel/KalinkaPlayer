"""Diagonal slashes / collage template.

Translucent thick bands layered along a family-seeded dominant angle over a
diagonal gradient.  Higher angularity tightens the angle spread for a more
parallel, aggressive look.  The edition jitters angles and positions within
small bounds.
"""

import math

import numpy as np
from PIL import Image, ImageDraw

from . import geometry_rngs, linear_gradient, shade


def render(params, canvas: int) -> Image.Image:
    frng, erng = geometry_rngs(params)
    strength = params.edition_strength
    palette = params.palette

    dominant_angle = (float(frng.random()) - 0.5) * 120.0
    background = linear_gradient(
        canvas,
        shade(palette[0], 1.2),
        shade(palette[0], 0.65),
        angle_deg=dominant_angle + 90.0,
    ).convert("RGBA")

    n_slashes = 5 + int(round(params.complexity * 11))
    spread = 8.0 + (1.0 - params.angularity) * 27.0

    for i in range(n_slashes):
        center = 0.1 + frng.random(2) * 0.8
        angle = dominant_angle + (float(frng.random()) - 0.5) * 2.0 * spread
        length = 0.6 + float(frng.random()) * 0.9
        width = 0.02 + float(frng.random()) * 0.10
        alpha = 120 + int(float(frng.random()) * 115)
        use_accent = float(frng.random()) < 0.18

        angle += (float(erng.random()) - 0.5) * 2.0 * strength * 8.0
        center = center + (erng.random(2) - 0.5) * 2.0 * strength * 0.03

        theta = math.radians(angle)
        direction = np.array([math.cos(theta), math.sin(theta)])
        normal = np.array([-math.sin(theta), math.cos(theta)])
        corners = [
            center + direction * (length / 2.0) + normal * (width / 2.0),
            center + direction * (length / 2.0) - normal * (width / 2.0),
            center - direction * (length / 2.0) - normal * (width / 2.0),
            center - direction * (length / 2.0) + normal * (width / 2.0),
        ]
        color = palette[4] if use_accent else palette[1 + i % 3]
        overlay = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        ImageDraw.Draw(overlay).polygon(
            [tuple(point * canvas) for point in corners], fill=color + (alpha,)
        )
        background = Image.alpha_composite(background, overlay)

    return background.convert("RGB")
