"""Geometric blocks / grid template.

A family-seeded grid of colored cells with occasional inner shapes (round
or angular depending on the angularity style value).  The edition nudges
individual cells by tiny offsets.
"""

import numpy as np
from PIL import Image, ImageDraw

from . import geometry_rngs, shade


def render(params, canvas: int) -> Image.Image:
    frng, erng = geometry_rngs(params)
    strength = params.edition_strength
    palette = params.palette

    background = Image.new("RGB", (canvas, canvas), shade(palette[0], 0.9))
    draw = ImageDraw.Draw(background)

    cols = 3 + int(round(params.complexity * 5))
    rows = cols + int(frng.integers(-1, 2))
    margin = 0.05 + float(frng.random()) * 0.04
    gutter = 0.006 + float(frng.random()) * 0.014
    cell_w = (1.0 - 2.0 * margin - (cols - 1) * gutter) / cols
    cell_h = (1.0 - 2.0 * margin - (rows - 1) * gutter) / rows

    # Palette pick weights: background rare, principals dominant, accent spice.
    color_cdf = np.cumsum([0.06, 0.28, 0.26, 0.24, 0.16])
    inner_chance = 0.22 + params.texture_density * 0.38

    for row in range(rows):
        for col in range(cols):
            # Fixed per-cell draw counts keep the RNG stream size-independent
            # and branch-independent.
            u_color = float(frng.random())
            u_inner = float(frng.random())
            u_shape = float(frng.random())
            u_inner_size = float(frng.random())
            u_inner_color = float(frng.random())
            jitter = (erng.random(2) - 0.5) * 2.0 * strength * 0.02

            x0 = margin + col * (cell_w + gutter) + float(jitter[0])
            y0 = margin + row * (cell_h + gutter) + float(jitter[1])
            x1, y1 = x0 + cell_w, y0 + cell_h
            color_index = int(np.searchsorted(color_cdf, u_color * color_cdf[-1]))
            color_index = min(color_index, len(palette) - 1)
            draw.rectangle(
                (x0 * canvas, y0 * canvas, x1 * canvas, y1 * canvas),
                fill=palette[color_index],
            )

            if u_inner >= inner_chance:
                continue
            inner_index = (color_index + 1 + int(u_inner_color * 3)) % len(palette)
            inner_color = palette[inner_index]
            half = (0.15 + u_inner_size * 0.2) * min(cell_w, cell_h)
            mid_x, mid_y = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            bbox = (
                (mid_x - half) * canvas,
                (mid_y - half) * canvas,
                (mid_x + half) * canvas,
                (mid_y + half) * canvas,
            )
            if u_shape > params.angularity:
                draw.ellipse(bbox, fill=inner_color)
            elif u_shape > params.angularity / 2.0:
                draw.rectangle(bbox, fill=inner_color)
            else:
                draw.polygon(
                    (
                        (mid_x * canvas, (mid_y - half) * canvas),
                        ((mid_x + half) * canvas, (mid_y + half) * canvas),
                        ((mid_x - half) * canvas, (mid_y + half) * canvas),
                    ),
                    fill=inner_color,
                )
    return background
