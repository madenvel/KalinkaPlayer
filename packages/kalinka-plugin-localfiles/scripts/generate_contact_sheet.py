#!/usr/bin/env python3
"""Render a contact sheet for visual inspection of the artwork generator.

The sheet shows: an original album, its remaster and deluxe edition, another
album by the same artist, a semantically similar album by a different artist
(nearby embedding), unrelated albums across genres, and one album rendered
at 64/128/256/512 to check size consistency.

Usage:
    python generate_contact_sheet.py /path/to/contact_sheet.png
"""

import argparse
import asyncio
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from kalinka_plugin_localfiles.procedural_artwork import (
    AlbumArtworkInput,
    ProceduralArtworkGenerator,
)

CELL = 256
PAD = 16
LABEL_H = 34
BACKGROUND = (24, 24, 28)
LABEL_COLOR = (225, 225, 230)

BASE_TRACKS = tuple(f"Night Current {i}" for i in range(1, 13))


def _embedding(seed: int, jitter_from: np.ndarray | None = None) -> np.ndarray:
    rng = np.random.Generator(np.random.PCG64(seed))
    if jitter_from is not None:
        return jitter_from + 0.05 * rng.standard_normal(jitter_from.size)
    vec = rng.standard_normal(64)
    return vec / np.linalg.norm(vec)


def build_rows() -> list[list[tuple[str, AlbumArtworkInput, int]]]:
    """Rows of (label, album, size) cells."""
    base_embedding = _embedding(41)

    def family(title: str) -> AlbumArtworkInput:
        return AlbumArtworkInput(
            artist="Aurora Fields",
            title=title,
            genre="ambient",
            track_titles=BASE_TRACKS,
        )

    row_family = [
        ("original", family("Night Currents"), CELL),
        ("remaster", family("Night Currents (2019 Remaster)"), CELL),
        ("deluxe", family("Night Currents (Deluxe Edition)"), CELL),
        (
            "same artist, other album",
            AlbumArtworkInput(artist="Aurora Fields", title="Morning Statics", genre="ambient"),
            CELL,
        ),
    ]
    row_semantic = [
        (
            "embedding A",
            AlbumArtworkInput(
                artist="Aurora Fields",
                title="Night Currents",
                genre="ambient",
                track_titles=BASE_TRACKS,
                album_embedding=base_embedding,
            ),
            CELL,
        ),
        (
            "similar embedding, other artist",
            AlbumArtworkInput(
                artist="Meadow Circuit",
                title="Slow Tide Atlas",
                genre="ambient",
                album_embedding=_embedding(7, jitter_from=base_embedding),
            ),
            CELL,
        ),
        (
            "techno",
            AlbumArtworkInput(artist="Voltage Union", title="Steel Habits", genre="techno"),
            CELL,
        ),
        (
            "jazz",
            AlbumArtworkInput(artist="Blue Note Trio", title="Corner Lights", genre="jazz"),
            CELL,
        ),
    ]
    row_genres = [
        (
            "metal",
            AlbumArtworkInput(artist="Iron Meridian", title="Ash Doctrine", genre="metal"),
            CELL,
        ),
        (
            "folk",
            AlbumArtworkInput(artist="Hollow Pines", title="Ridgeline", genre="folk"),
            CELL,
        ),
        (
            "hip hop",
            AlbumArtworkInput(artist="Concrete Lexicon", title="Marble Verbs", genre="hip hop"),
            CELL,
        ),
        (
            "classical",
            AlbumArtworkInput(artist="Vela Ensemble", title="Studies in Grey", genre="classical"),
            CELL,
        ),
    ]
    row_sizes = [
        (f"original @ {size}", family("Night Currents"), size) for size in (64, 128, 256, 512)
    ]
    return [row_family, row_semantic, row_genres, row_sizes]


async def render_sheet(output_path: Path) -> None:
    generator = ProceduralArtworkGenerator()
    rows = build_rows()

    # Rows may contain renders larger than CELL (the size-series row), so
    # each row is as tall as its largest cell and columns advance per cell.
    row_heights = [max(max(size, CELL) for _, _, size in row) for row in rows]
    sheet_w = PAD + max(
        sum(max(size, CELL) + PAD for _, _, size in row) for row in rows
    )
    sheet_h = PAD + sum(height + LABEL_H + PAD for height in row_heights)
    sheet = Image.new("RGB", (sheet_w, sheet_h), BACKGROUND)
    draw = ImageDraw.Draw(sheet)

    # Renders are sequential on purpose: mirrors the recommended Pi usage.
    y = PAD
    for row, row_height in zip(rows, row_heights):
        x = PAD
        for label, album, size in row:
            image = await generator.generate(album, size=size)
            cell_w = max(size, CELL)
            # Center smaller renders in their cell at native resolution.
            sheet.paste(
                image,
                (x + (cell_w - image.width) // 2, y + (row_height - image.height) // 2),
            )
            draw.text((x, y + row_height + 8), label, fill=LABEL_COLOR)
            x += cell_w + PAD
        y += row_height + LABEL_H + PAD

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)
    print(f"wrote {output_path} ({sheet_w}x{sheet_h})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="output image path (e.g. sheet.png)")
    args = parser.parse_args()
    asyncio.run(render_sheet(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
