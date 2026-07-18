#!/usr/bin/env python3
"""Benchmark the procedural artwork generator.

Measures render time, encode time and total async call time per size, plus
approximate peak process memory (resource.getrusage).  Runs on both desktop
Linux and a Raspberry Pi.

Usage:
    python benchmark_artwork.py                     # 64/256/512/1024, 20 samples
    python benchmark_artwork.py --size 512 --count 50 --concurrency 2

Note: high --concurrency values can pressure Raspberry Pi 4 memory; 1-2 is
the recommended range there.
"""

import argparse
import asyncio
import io
import resource
import statistics
import sys
import time

from kalinka_plugin_localfiles.procedural_artwork import (
    AlbumArtworkInput,
    ProceduralArtworkGenerator,
)

GENRES = ("ambient", "techno", "jazz", "metal", "folk", "electronic", "rock", None)


def sample_album(index: int) -> AlbumArtworkInput:
    return AlbumArtworkInput(
        artist=f"Benchmark Artist {index % 17}",
        title=f"Benchmark Album {index}",
        genre=GENRES[index % len(GENRES)],
        track_titles=tuple(f"Track {t}" for t in range(1, 13)),
    )


def describe(values: list[float]) -> str:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return (
        f"mean {statistics.mean(values) * 1000:7.1f} ms  "
        f"median {statistics.median(values) * 1000:7.1f} ms  "
        f"p95 {p95 * 1000:7.1f} ms  "
        f"max {max(values) * 1000:7.1f} ms"
    )


def peak_memory_mb() -> float:
    # ru_maxrss is in kilobytes on Linux.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


async def benchmark_size(
    generator: ProceduralArtworkGenerator, size: int, count: int, concurrency: int
) -> None:
    render_times: list[float] = []
    encode_times: list[float] = []

    for index in range(count):
        params = generator.resolve_parameters(sample_album(index), size=size)
        started = time.perf_counter()
        image = generator.render(params)
        render_times.append(time.perf_counter() - started)

        buffer = io.BytesIO()
        started = time.perf_counter()
        image.save(buffer, format="PNG")
        encode_times.append(time.perf_counter() - started)

    semaphore = asyncio.Semaphore(concurrency)
    total_times: list[float] = []

    async def one_call(index: int) -> None:
        async with semaphore:
            started = time.perf_counter()
            await generator.generate_bytes(sample_album(index), size=size)
            total_times.append(time.perf_counter() - started)

    await asyncio.gather(*(one_call(i) for i in range(count)))

    print(f"size {size}x{size}  ({count} samples, concurrency {concurrency})")
    print(f"  render      {describe(render_times)}")
    print(f"  encode PNG  {describe(encode_times)}")
    print(f"  async total {describe(total_times)}")
    print(f"  peak RSS so far: {peak_memory_mb():.0f} MB")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--size",
        type=int,
        action="append",
        help="output size to benchmark (repeatable; default 64 256 512 1024)",
    )
    parser.add_argument("--count", type=int, default=20, help="samples per size (default 20)")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="concurrent async calls (default 1; more than 2 may pressure Pi 4 memory)",
    )
    args = parser.parse_args()
    sizes = args.size or [64, 256, 512, 1024]
    if args.concurrency > 2:
        print(
            "warning: concurrency > 2 may pressure Raspberry Pi 4 memory",
            file=sys.stderr,
        )

    generator = ProceduralArtworkGenerator()
    for size in sizes:
        await benchmark_size(generator, size, args.count, args.concurrency)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
