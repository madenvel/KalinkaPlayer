"""Async API tests: thread offloading, concurrency, errors, cancellation."""

import asyncio
import inspect

import pytest
from PIL import Image

from kalinka_plugin_localfiles.procedural_artwork import (
    AlbumArtworkInput,
    ProceduralArtworkGenerator,
    UnsupportedFormatError,
)
from kalinka_plugin_localfiles.procedural_artwork import generator as generator_module


def _album(index: int = 0) -> AlbumArtworkInput:
    genres = ("ambient", "techno", "jazz", "metal")
    return AlbumArtworkInput(
        artist=f"Artist {index}",
        title=f"Album Number {index}",
        genre=genres[index % len(genres)],
    )


@pytest.mark.asyncio
async def test_generate_inside_running_loop():
    gen = ProceduralArtworkGenerator()
    image = await gen.generate(_album(), size=128)
    assert isinstance(image, Image.Image)
    assert image.size == (128, 128)


@pytest.mark.asyncio
async def test_generate_bytes_is_deterministic():
    gen = ProceduralArtworkGenerator()
    first = await gen.generate_bytes(_album(), size=128)
    second = await gen.generate_bytes(_album(), size=128)
    assert first == second
    assert first.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_concurrent_generation_different_albums():
    gen = ProceduralArtworkGenerator()
    results = await asyncio.gather(
        *(gen.generate_bytes(_album(i), size=128) for i in range(4))
    )
    assert len(set(results)) == 4
    # Concurrent output matches sequential output for the same albums.
    for i, concurrent in enumerate(results):
        assert concurrent == await gen.generate_bytes(_album(i), size=128)


@pytest.mark.asyncio
async def test_event_loop_stays_responsive_during_render():
    gen = ProceduralArtworkGenerator()
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    try:
        await gen.generate(_album(), size=512)
    finally:
        task.cancel()
    # If the render blocked the loop the ticker could not have advanced.
    assert ticks > 0


@pytest.mark.asyncio
async def test_renderer_exception_propagates(monkeypatch):
    gen = ProceduralArtworkGenerator()

    def boom(parameters):
        raise RuntimeError("simulated render failure")

    monkeypatch.setattr(gen, "render", boom)
    with pytest.raises(RuntimeError, match="simulated render failure"):
        await gen.generate(_album(), size=64)


@pytest.mark.asyncio
async def test_invalid_format_raises_before_rendering():
    gen = ProceduralArtworkGenerator()
    with pytest.raises(UnsupportedFormatError):
        await gen.generate_bytes(_album(), image_format="TIFF")


@pytest.mark.asyncio
async def test_cancellation_is_clean():
    gen = ProceduralArtworkGenerator()
    task = asyncio.create_task(gen.generate(_album(), size=512))
    await asyncio.sleep(0)
    task.cancel()
    # Cancellation must either surface CancelledError or, if the render
    # finished first, a valid image; nothing else may escape.
    try:
        result = await task
    except asyncio.CancelledError:
        pass
    else:
        assert isinstance(result, Image.Image)


@pytest.mark.asyncio
async def test_file_and_bytes_generation_through_async_api(tmp_path):
    gen = ProceduralArtworkGenerator()
    data = await gen.generate_bytes(_album(), size=64)
    path = tmp_path / "art.png"
    params = await gen.generate_to_file(_album(), path, size=64)
    assert path.read_bytes() == data
    assert params.resolved_size == 64


def test_no_internal_asyncio_run():
    source = inspect.getsource(generator_module)
    assert "asyncio.run(" not in source
    assert "new_event_loop" not in source
