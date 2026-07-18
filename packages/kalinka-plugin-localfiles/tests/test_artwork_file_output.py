"""File-output tests: atomic writes, format inference, failure cleanup."""

import io

import pytest
from PIL import Image

from kalinka_plugin_localfiles.procedural_artwork import (
    AlbumArtworkInput,
    FileWriteError,
    ProceduralArtworkGenerator,
    UnsupportedFormatError,
)


def _album() -> AlbumArtworkInput:
    return AlbumArtworkInput(artist="Aurora Fields", title="Night Currents", genre="ambient")


def _tmp_leftovers(directory) -> list:
    return list(directory.glob(".artwork-*"))


@pytest.mark.asyncio
async def test_successful_png_write(tmp_path):
    gen = ProceduralArtworkGenerator()
    path = tmp_path / "cover.png"
    params = await gen.generate_to_file(_album(), path, size=128)
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.size == (128, 128)
    assert params.resolved_size == 128
    assert params == gen.resolve_parameters(_album(), size=128)
    assert _tmp_leftovers(tmp_path) == []


@pytest.mark.asyncio
async def test_parent_directories_created(tmp_path):
    gen = ProceduralArtworkGenerator()
    path = tmp_path / "nested" / "deeper" / "cover.png"
    await gen.generate_to_file(_album(), path, size=64)
    assert path.is_file()


@pytest.mark.asyncio
async def test_existing_file_atomically_replaced(tmp_path):
    gen = ProceduralArtworkGenerator()
    path = tmp_path / "cover.png"
    path.write_bytes(b"stale artwork")
    await gen.generate_to_file(_album(), path, size=64)
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG")
    assert _tmp_leftovers(tmp_path) == []


@pytest.mark.asyncio
async def test_write_failure_cleans_up_and_preserves_original(tmp_path, monkeypatch):
    gen = ProceduralArtworkGenerator()
    path = tmp_path / "cover.png"
    path.write_bytes(b"previous artwork")

    def failing_save(self, fp, format=None, **kwargs):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(Image.Image, "save", failing_save)
    with pytest.raises(FileWriteError, match="disk full"):
        await gen.generate_to_file(_album(), path, size=64)
    monkeypatch.undo()

    assert path.read_bytes() == b"previous artwork"
    assert _tmp_leftovers(tmp_path) == []


@pytest.mark.asyncio
async def test_format_inference_from_extension(tmp_path):
    gen = ProceduralArtworkGenerator()
    for name, expected in (("a.png", "PNG"), ("b.jpg", "JPEG"), ("c.webp", "WEBP")):
        path = tmp_path / name
        await gen.generate_to_file(_album(), path, size=64)
        with Image.open(path) as image:
            assert image.format == expected


@pytest.mark.asyncio
async def test_unknown_extension_requires_explicit_format(tmp_path):
    gen = ProceduralArtworkGenerator()
    path = tmp_path / "cover.artwork"
    with pytest.raises(UnsupportedFormatError):
        await gen.generate_to_file(_album(), path, size=64)
    # Supplying the format explicitly overrides the unknown extension.
    await gen.generate_to_file(_album(), path, size=64, image_format="PNG")
    assert path.read_bytes().startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_returned_parameters_match_written_image(tmp_path):
    gen = ProceduralArtworkGenerator()
    path = tmp_path / "cover.png"
    params = await gen.generate_to_file(_album(), path, size=256)
    rendered = gen.render(params)
    buffer = io.BytesIO()
    rendered.save(buffer, format="PNG")
    assert path.read_bytes() == buffer.getvalue()


@pytest.mark.asyncio
async def test_generate_bytes_jpeg_and_webp():
    gen = ProceduralArtworkGenerator()
    jpeg = await gen.generate_bytes(_album(), size=64, image_format="JPEG")
    assert jpeg[:3] == b"\xff\xd8\xff"
    webp = await gen.generate_bytes(_album(), size=64, image_format="webp")
    assert webp[:4] == b"RIFF"
