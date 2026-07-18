"""Composed background art for catalog cards.

Pure, synchronous Pillow/NumPy rendering — no I/O, no event loop. The
service layer (``catalog_art_service``) fetches inputs and offloads these
calls to a worker thread.

Two variants share one visual language (dark, high-contrast, film grain,
berry/brass accents echoing the localfiles procedural album art):

- **covers**: up to three album covers — the first becomes a heavily
  blurred, darkened backdrop with colour glows pulled from each cover's
  dominant colour; the covers themselves sit as rotated, rounded,
  drop-shadowed tiles on the right, leaving the top-left clear for the
  client-drawn title.
- **textual**: seeded procedural blobs (no covers to show) with up to
  three category names baked into the lower-left, each keyed to a stable
  per-name colour.

Determinism: identical inputs (covers bytes, names, seed) render identical
pixels, so re-generation with unchanged inputs produces an identical file.

Bump ``STYLE_VERSION`` when the look changes — it is part of the content
fingerprint, so every card regenerates on upgrade.
"""

from __future__ import annotations

import colorsys
import hashlib
import io
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

STYLE_VERSION = 1

CANVAS_W = 960
CANVAS_H = 540

# Berry/brass accent family shared with the localfiles procedural artwork.
_ACCENT_BERRY = (176, 66, 106)
_ACCENT_BRASS = (201, 168, 106)

# Candidate fonts for baked category names, in preference order. DejaVu and
# Liberation ship on Debian/RPi and Fedora and cover Cyrillic; the Pillow
# bundled fallback is Latin-only but better than nothing.
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
]


def _rng(seed: str) -> np.random.Generator:
    digest = hashlib.sha256(f"{STYLE_VERSION}:{seed}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        # Pillow >= 10.1 renders its bundled font at any size.
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _fit_cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale keeping aspect and centre-crop to exactly width x height."""
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _dominant_color(image: Image.Image) -> tuple[int, int, int]:
    """The most vivid colour of a small thumbnail — saturation-weighted so
    glows stay colourful instead of averaging to mud."""
    thumb = image.convert("RGB").resize((12, 12), Image.Resampling.LANCZOS)
    pixels = np.asarray(thumb, dtype=np.float32) / 255.0
    flat = pixels.reshape(-1, 3)
    maxc = flat.max(axis=1)
    minc = flat.min(axis=1)
    sat = np.where(maxc > 0, (maxc - minc) / np.maximum(maxc, 1e-6), 0.0)
    # Vividness: saturated and reasonably bright, but not blown-out white.
    score = sat * np.clip(maxc, 0.15, 0.9)
    r, g, b = flat[int(score.argmax())]
    return (int(r * 255), int(g * 255), int(b * 255))


def _hue_color(hue_deg: float, saturation: float, lightness: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb((hue_deg % 360.0) / 360.0, lightness, saturation)
    return (int(r * 255), int(g * 255), int(b * 255))


def _name_color(name: str) -> tuple[int, int, int]:
    """Stable per-name tint for textual rows (md5-based, not runtime hash)."""
    digest = hashlib.md5(name.strip().lower().encode()).digest()
    hue = int.from_bytes(digest[:2], "big") % 360
    return _hue_color(hue, 0.55, 0.66)


def _radial_glow(
    width: int,
    height: int,
    center: tuple[float, float],
    radius: float,
    color: tuple[int, int, int],
    alpha: float,
) -> Image.Image:
    """RGBA layer holding one soft radial gradient blob."""
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    dist = np.sqrt((xs - center[0]) ** 2 + (ys - center[1]) ** 2)
    fall = np.clip(1.0 - dist / max(radius, 1.0), 0.0, 1.0) ** 2
    layer = np.zeros((height, width, 4), dtype=np.uint8)
    layer[..., 0] = color[0]
    layer[..., 1] = color[1]
    layer[..., 2] = color[2]
    layer[..., 3] = (fall * alpha * 255).astype(np.uint8)
    return Image.fromarray(layer, "RGBA")


def _linear_scrim(
    width: int,
    height: int,
    *,
    horizontal: bool,
    start_alpha: float,
    mid_alpha: float,
    end_alpha: float,
) -> Image.Image:
    """Black RGBA gradient along one axis (start -> mid at 50% -> end)."""
    steps = width if horizontal else height
    ramp = np.interp(
        np.linspace(0.0, 1.0, steps),
        [0.0, 0.5, 1.0],
        [start_alpha, mid_alpha, end_alpha],
    ).astype(np.float32)
    alpha = np.tile(ramp, (height, 1)) if horizontal else np.tile(ramp[:, None], (1, width))
    layer = np.zeros((height, width, 4), dtype=np.uint8)
    layer[..., 3] = (alpha * 255).astype(np.uint8)
    return Image.fromarray(layer, "RGBA")


def _vignette(width: int, height: int, strength: float = 0.28) -> Image.Image:
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    nx = (xs / width) * 2.0 - 1.0
    ny = (ys / height) * 2.0 - 1.0
    dist = np.sqrt(nx * nx + ny * ny)
    fall = np.clip((dist - 0.7) / 0.6, 0.0, 1.0)
    layer = np.zeros((height, width, 4), dtype=np.uint8)
    layer[..., 3] = (fall * strength * 255).astype(np.uint8)
    return Image.fromarray(layer, "RGBA")


def _apply_grain(image: Image.Image, rng: np.random.Generator, amplitude: float) -> Image.Image:
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    noise = rng.standard_normal((image.height, image.width, 1)).astype(np.float32)
    array += noise * amplitude
    np.clip(array, 0.0, 255.0, out=array)
    return Image.fromarray(array.astype(np.uint8), "RGB")


def _rings_and_dots(base: Image.Image, rng: np.random.Generator) -> None:
    """Faint concentric rings on the right half and two accent dots, drawn in
    place. Kept away from the top-left title area."""
    draw = ImageDraw.Draw(base, "RGBA")
    cx = base.width * (0.62 + rng.random() * 0.3)
    cy = base.height * (0.2 + rng.random() * 0.6)
    for radius, alpha in ((base.height * 0.42, 9), (base.height * 0.26, 7)):
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=(255, 255, 255, alpha),
            width=2,
        )
    for color in (_ACCENT_BERRY, _ACCENT_BRASS):
        dot_x = base.width * (0.45 + rng.random() * 0.45)
        dot_y = base.height * (0.15 + rng.random() * 0.7)
        radius = 2.0 + rng.random() * 1.5
        draw.ellipse(
            [dot_x - radius, dot_y - radius, dot_x + radius, dot_y + radius],
            fill=(*color, 210),
        )


def _rounded_tile(cover: Image.Image, side: int, radius: int) -> Image.Image:
    """Square cover tile with anti-aliased rounded corners and a subtle
    light border, as RGBA."""
    scale = 4  # supersampled mask for smooth corners
    tile = _fit_cover(cover.convert("RGB"), side, side)
    mask = Image.new("L", (side * scale, side * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, side * scale - 1, side * scale - 1], radius=radius * scale, fill=255
    )
    mask = mask.resize((side, side), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    border = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(
        [0, 0, side - 1, side - 1],
        radius=radius,
        outline=(255, 255, 255, 36),
        width=1,
    )
    return Image.alpha_composite(out, border)


def _paste_with_shadow(
    base: Image.Image,
    tile: Image.Image,
    center: tuple[int, int],
    angle_deg: float,
) -> None:
    """Rotate a tile, put a soft drop shadow under it, paste both in place."""
    rotated = tile.rotate(angle_deg, expand=True, resample=Image.Resampling.BICUBIC)
    shadow_src = Image.new("RGBA", rotated.size, (0, 0, 0, 0))
    shadow_alpha = rotated.split()[3].point(lambda a: int(a * 0.55))
    shadow_src.paste((0, 0, 0, 255), (0, 0), shadow_alpha)
    pad = 24
    shadow = Image.new("RGBA", (rotated.width + pad * 2, rotated.height + pad * 2), (0, 0, 0, 0))
    shadow.paste(shadow_src, (pad, pad))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    sx = center[0] - shadow.width // 2
    sy = center[1] - shadow.height // 2 + 8
    base.alpha_composite(shadow, (sx, sy))
    base.alpha_composite(rotated, (center[0] - rotated.width // 2, center[1] - rotated.height // 2))


def _procedural_base(
    width: int, height: int, rng: np.random.Generator
) -> Image.Image:
    """Dark seeded base for coverless cards: near-black ground with a few
    distinct saturated colour fields grading in from the edges. The top-left
    (client title) and lower-left (baked names) quadrants stay dark."""
    base = Image.new("RGBA", (width, height), (11, 12, 16, 255))

    # Primary field: berry or brass family, anchored past the right edge so
    # it grades across the canvas.
    berry = rng.random() < 0.6
    primary_hue = (330.0 + rng.random() * 30.0) if berry else (28.0 + rng.random() * 22.0)
    base.alpha_composite(_radial_glow(
        width, height,
        (width * (1.02 + rng.random() * 0.1), height * (0.15 + rng.random() * 0.7)),
        height * (1.1 + rng.random() * 0.4),
        _hue_color(primary_hue, 0.68, 0.34),
        1.0,
    ))
    # Secondary: the sibling family, smaller, upper-right region.
    secondary_hue = (28.0 + rng.random() * 22.0) if berry else (330.0 + rng.random() * 30.0)
    base.alpha_composite(_radial_glow(
        width, height,
        (width * (0.55 + rng.random() * 0.3), height * (-0.1 + rng.random() * 0.35)),
        height * (0.5 + rng.random() * 0.3),
        _hue_color(secondary_hue, 0.62, 0.30),
        0.9,
    ))
    # Cool violet counterweight low-centre for depth.
    base.alpha_composite(_radial_glow(
        width, height,
        (width * (0.35 + rng.random() * 0.3), height * (0.95 + rng.random() * 0.2)),
        height * (0.5 + rng.random() * 0.3),
        _hue_color(255.0 + rng.random() * 30.0, 0.55, 0.24),
        0.85,
    ))
    return base.filter(ImageFilter.GaussianBlur(16))


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _bake_names(base: Image.Image, names: Sequence[str]) -> None:
    """Up to three category rows in the lower-left: colour dot + tinted bold
    name with a soft shadow. The top-left stays clear for the client title."""
    width, height = base.size
    font = _load_font(max(18, round(height * 0.062)))
    row_h = round(height * 0.105)
    pad_x = round(width * 0.055)
    pad_y = round(height * 0.085)
    rows = list(names[:3])

    shadow_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    text_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)

    for i, name in enumerate(reversed(rows)):
        baseline = height - pad_y - i * row_h
        color = _name_color(name)
        dot_r = round(height * 0.014)
        ascent, descent = font.getmetrics()
        cy = baseline - (ascent + descent) // 2
        text_draw.ellipse(
            [pad_x - dot_r, cy - dot_r, pad_x + dot_r, cy + dot_r],
            fill=(*color, 255),
        )
        text_x = pad_x + dot_r + round(width * 0.018)
        label = _ellipsize(text_draw, name, font, round(width * 0.58) - text_x)
        shadow_draw.text(
            (text_x, baseline + 2), label,
            font=font, fill=(0, 0, 0, 190), anchor="ls",
        )
        text_draw.text(
            (text_x, baseline), label, font=font, fill=(*color, 242), anchor="ls"
        )

    base.alpha_composite(shadow_layer.filter(ImageFilter.GaussianBlur(3)))
    base.alpha_composite(text_layer)


def _scrims(base: Image.Image, *, floor_alpha: int = 70) -> None:
    """Legibility stack for the backdrop: flat floor, title gradient from the
    top-left, bottom scrim — mirrors what the client used to draw. Applied
    *before* cover tiles so they stay bright against the darkened ground."""
    width, height = base.size
    if floor_alpha:
        base.alpha_composite(Image.new("RGBA", base.size, (0, 0, 0, floor_alpha)))
    base.alpha_composite(
        _linear_scrim(width, height, horizontal=True,
                      start_alpha=0.60, mid_alpha=0.20, end_alpha=0.0)
    )
    base.alpha_composite(
        _linear_scrim(width, height, horizontal=False,
                      start_alpha=0.26, mid_alpha=0.02, end_alpha=0.28)
    )


def render_catalog_art(
    covers: Sequence[Image.Image],
    names: Sequence[str],
    seed: str,
    *,
    width: int = CANVAS_W,
    height: int = CANVAS_H,
) -> Image.Image:
    """Render one card background.

    ``covers`` non-empty -> cover collage variant (names ignored);
    otherwise the textual/procedural variant with up to three ``names``
    baked in (or none, for a plain procedural backdrop).
    """
    rng = _rng(seed)

    if covers:
        hero = _fit_cover(covers[0].convert("RGB"), width, height)
        hero = hero.filter(ImageFilter.GaussianBlur(38))
        hero = ImageEnhance.Brightness(hero).enhance(0.52)
        hero = ImageEnhance.Color(hero).enhance(0.85)
        base = hero.convert("RGBA")

        for cover in covers[:3]:
            color = _dominant_color(cover)
            center = (width * (0.2 + rng.random() * 0.6), height * rng.random())
            radius = width * (0.30 + rng.random() * 0.25)
            base.alpha_composite(
                _radial_glow(width, height, center, radius, color, 0.45)
            )
        _rings_and_dots(base, rng)
        # Darken the ground first; the tiles pasted on top keep full
        # brightness, which is where the contrast comes from.
        _scrims(base)

        # Cover tiles cascade toward the right; largest in front, drawn last.
        # Layout tuned for three; with fewer the leftover slots just vanish.
        slots = [
            (0.58, 0.56, 0.545),  # (tile side factor of H, cx/W, cy/H) front
            (0.46, 0.745, 0.47),
            (0.38, 0.875, 0.62),
        ]
        order = list(range(min(len(covers), 3)))[::-1]  # back to front
        for idx in order:
            side_f, cx_f, cy_f = slots[idx]
            side = round(height * side_f)
            tile = _rounded_tile(covers[idx], side, radius=round(side * 0.055))
            angle = float(rng.uniform(-8.0, 8.0))
            _paste_with_shadow(
                base, tile, (round(width * cx_f), round(height * cy_f)), angle
            )

        base.alpha_composite(_vignette(width, height, 0.20))
        out = base.convert("RGB")
        out = ImageEnhance.Contrast(out).enhance(1.06)
        return _apply_grain(out, rng, amplitude=5.0)

    base = _procedural_base(width, height, rng)
    _rings_and_dots(base, rng)
    _scrims(base, floor_alpha=36)
    base.alpha_composite(_vignette(width, height, 0.22))
    if names:
        _bake_names(base, names)
    out = base.convert("RGB")
    out = ImageEnhance.Contrast(out).enhance(1.05)
    return _apply_grain(out, rng, amplitude=4.0)


def encode_jpeg(image: Image.Image, quality: int = 85) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, progressive=True, optimize=True)
    return buffer.getvalue()


def content_fingerprint(
    cover_bytes: Sequence[bytes], names: Sequence[str], seed: str
) -> str:
    """Stable identity of a card's rendered content: style version + variant
    inputs. Unchanged fingerprint -> the cached file is still current."""
    hasher = hashlib.sha1()
    hasher.update(f"style:{STYLE_VERSION}".encode())
    hasher.update(f"seed:{seed}".encode())
    for blob in cover_bytes:
        hasher.update(b"cover:")
        hasher.update(hashlib.sha1(blob).digest())
    for name in names:
        hasher.update(f"name:{name}".encode())
    return hasher.hexdigest()
