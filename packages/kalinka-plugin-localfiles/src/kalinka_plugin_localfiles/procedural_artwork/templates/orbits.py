"""Circles and orbital geometry template.

Concentric rings and arcs around a family-seeded off-center point, with a
few satellite dots sitting on the rings.  The edition rotates the whole
system slightly and jitters ring radii within bounds.
"""

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import geometry_rngs, radial_gradient, shade, stroke_width


def _ring_bbox(cx: float, cy: float, radius: float, canvas: int) -> tuple:
    return (
        (cx - radius) * canvas,
        (cy - radius) * canvas,
        (cx + radius) * canvas,
        (cy + radius) * canvas,
    )


def render(params, canvas: int) -> Image.Image:
    frng, erng = geometry_rngs(params)
    strength = params.edition_strength
    palette = params.palette

    center = 0.32 + frng.random(2) * 0.36
    cx, cy = float(center[0]), float(center[1])
    background = radial_gradient(
        canvas,
        (cx, cy),
        shade(palette[0], 1.35),
        shade(palette[0], 0.65),
        radius=0.95,
    )
    draw = ImageDraw.Draw(background)

    n_rings = 3 + int(round(params.complexity * 9))
    radii = np.sort(0.10 + frng.random(n_rings) * 0.62)
    # Edition rotates the whole orbital system by a few degrees at most.
    rotation = (float(erng.random()) - 0.5) * 2.0 * strength * 0.35

    ring_geometry = []
    for k in range(n_rings):
        width_unit = 0.0035 + float(frng.random()) * 0.013
        is_full = float(frng.random()) < 0.65
        arc_start = float(frng.random()) * 360.0
        arc_extent = 60.0 + float(frng.random()) * 240.0
        use_accent = float(frng.random()) < 0.18
        radius = float(radii[k]) * (1.0 + (float(erng.random()) - 0.5) * 2.0 * strength * 0.05)
        ring_geometry.append((radius, width_unit, is_full, arc_start, arc_extent, use_accent))

    for k, (radius, width_unit, is_full, arc_start, arc_extent, use_accent) in enumerate(
        ring_geometry
    ):
        color = palette[4] if use_accent else palette[1 + k % 3]
        width = stroke_width(width_unit, canvas)
        bbox = _ring_bbox(cx, cy, radius, canvas)
        if is_full:
            draw.ellipse(bbox, outline=color, width=width)
        else:
            start = arc_start + math.degrees(rotation)
            draw.arc(bbox, start=start, end=start + arc_extent, fill=color, width=width)

    n_dots = 2 + int(round(params.texture_density * 7))
    for _ in range(n_dots):
        ring_index = int(frng.integers(n_rings))
        angle = float(frng.random()) * 2.0 * math.pi + rotation
        dot_radius = 0.006 + float(frng.random()) * 0.018
        use_accent = float(frng.random()) < 0.5
        orbit_radius = ring_geometry[ring_index][0]
        dot_x = cx + orbit_radius * math.cos(angle)
        dot_y = cy + orbit_radius * math.sin(angle)
        color = palette[4] if use_accent else palette[2]
        draw.ellipse(_ring_bbox(dot_x, dot_y, dot_radius, canvas), fill=color)

    if params.softness > 0.05:
        background = background.filter(
            ImageFilter.GaussianBlur(radius=params.softness * canvas * 0.0025)
        )
    return background
