"""Proof that consecutive tracks are joined without a gap.

Two files are played back to back through the real native pipeline (FileInputNode
-> FlacStreamDecoder -> AudioStreamSwitcher -> AlsaAudioEmitter) while every frame
the emitter hands to ALSA is captured. The captured stream must be *sample
identical* to the same audio rendered as one continuous file: nothing inserted,
nothing dropped, nothing duplicated at the track boundary.

Capture works by pointing ``output.alsa.device`` at an ALSA ``file`` plugin whose
slave is ``null``, so the frames are written to disk instead of a sound card. No
audio hardware is required and the test is silent.

Playback runs in a child process for two reasons: alsa-lib caches its
configuration globally on first use, and the child can exit via ``os._exit`` to
avoid unrelated shutdown noise from the native module.

Set ``KALINKA_GAPLESS_ARTIFACTS=<dir>`` to also dump inspectable artifacts:
16-bit WAVs of both inputs, of the captured output, and of the same output with
silence spliced into the join for comparison, plus SVG waveform plots. The WAVs
open directly in Audacity; ``captured-joined.raw`` is the same data as raw
interleaved PCM.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

SAMPLE_RATE = 44100

# One continuous tone, cut in half. Both tracks therefore hold *the same*
# waveform, and track B must resume exactly where A stopped — in phase, to the
# sample. That makes the join audible: a clean run is one uninterrupted tone,
# while any inserted, dropped or duplicated frame is a click.
#
# 441.5 Hz over 1.5 s puts the cut at 662.25 cycles, i.e. on a positive peak
# rather than a zero crossing, which is the worst case for splicing silence in
# and so the easiest to hear and to see.
FREQ = 441.5
HALF_FRAMES = 66150  # 1.5 s per track, 3 s total

REPO_SRC = Path(__file__).resolve().parents[1] / "src"

# Playback child. Kept as source text rather than an importable helper so the
# subprocess has no dependency on how pytest happens to name this module.
_CHILD_SOURCE = r'''
import json, os, sys, threading

conf_path, capture_path, states_path = sys.argv[1:4]
tracks = sys.argv[4:]

with open(conf_path, "w") as fh:
    fh.write(
        'pcm.kalinka_capture {\n'
        '    type file\n'
        '    slave.pcm "null"\n'
        '    file "%s"\n'
        '    format raw\n'
        '}\n' % capture_path
    )

# Must be set before alsa-lib reads its configuration.
os.environ["ALSA_CONFIG_PATH"] = "/usr/share/alsa/alsa.conf:" + conf_path

from native_player.native_player import (  # noqa: E402
    AudioFormat,
    AudioGraphNodeState,
    AudioPlayer,
    py_dict_to_config,
)

config = py_dict_to_config(
    {"output": {"alsa": {"device": "kalinka_capture", "latency_ms": 100, "period_ms": 25}}}
)
player = AudioPlayer(config)
monitor = player.monitor()

for track in tracks:
    player.append("file://" + track, AudioFormat.FLAC)

states = []
finished = threading.Event()


def pump():
    while monitor.is_running():
        state = monitor.wait_state()
        states.append(int(state.state))
        if state.state in (AudioGraphNodeState.FINISHED, AudioGraphNodeState.ERROR):
            finished.set()
            return


thread = threading.Thread(target=pump, daemon=True)
thread.start()
ok = finished.wait(timeout=30)

with open(states_path, "w") as fh:
    json.dump({"states": states, "finished": ok}, fh)

sys.stderr.flush()
sys.stdout.flush()
os._exit(0)
'''


def _tone(frequency: float, frames: int) -> np.ndarray:
    """Deterministic 16-bit stereo tone."""
    t = np.arange(frames) / SAMPLE_RATE
    mono = np.round(0.5 * np.sin(2 * np.pi * frequency * t) * 32767).astype("<i2")
    return np.stack([mono, mono], axis=1)


def _write_flac(path: Path, frames: np.ndarray) -> None:
    sf.write(str(path), frames, SAMPLE_RATE, subtype="PCM_16")


def longest_silent_run(frames: np.ndarray) -> int:
    """Longest run of consecutive all-zero frames."""
    silent = (frames == 0).all(axis=1)
    if not silent.any():
        return 0
    # Reset the running count at every non-silent frame.
    idx = np.arange(len(silent))
    boundaries = np.maximum.accumulate(np.where(silent, 0, idx + 1))
    return int((idx + 1 - boundaries)[silent].max())


def _play_and_capture(tmp_path: Path, tracks: list[Path]) -> tuple[np.ndarray, dict, str]:
    capture = tmp_path / "capture.raw"
    states_file = tmp_path / "states.json"

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_SRC), env.get("PYTHONPATH", "")])

    child = tmp_path / "capture_child.py"
    child.write_text(_CHILD_SOURCE)

    result = subprocess.run(
        [sys.executable, str(child), str(tmp_path / "asound.conf"), str(capture), str(states_file)]
        + [str(t) for t in tracks],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    if not capture.exists() or capture.stat().st_size == 0:
        if "Cannot open audio device" in result.stderr:
            pytest.skip("ALSA file/null plugins unavailable in this environment")
        pytest.fail(
            f"capture produced no data (rc={result.returncode})\n"
            f"--- stderr ---\n{result.stderr[-4000:]}"
        )

    states = json.loads(states_file.read_text())
    frames = np.fromfile(capture, dtype="<i2").reshape(-1, 2)
    # The native logger sinks to stdout; keep both streams so assertions on log
    # output cannot pass just because they are looking at an empty string.
    log = result.stdout + result.stderr
    assert "native:" in log, (
        "no native log output captured — assertions on log content would be "
        "vacuous. Check that the native logger is initialised."
    )
    return frames, states, log


@pytest.fixture(scope="module")
def gapless_capture(tmp_path_factory) -> dict:
    """Play two tracks back to back once; share the result across assertions."""
    tmp_path = tmp_path_factory.mktemp("gapless")

    # One continuous tone, then cut. Generating the halves separately would
    # restart B's phase at zero and quietly weaken the whole test.
    reference = _tone(FREQ, 2 * HALF_FRAMES)
    track_a = reference[:HALF_FRAMES]
    track_b = reference[HALF_FRAMES:]
    _write_flac(tmp_path / "a.flac", track_a)
    _write_flac(tmp_path / "b.flac", track_b)

    captured, states, log = _play_and_capture(
        tmp_path, [tmp_path / "a.flac", tmp_path / "b.flac"]
    )

    result = {
        "track_a": track_a,
        "track_b": track_b,
        "reference": reference,
        "captured": captured,
        "boundary": HALF_FRAMES,
        "states": states,
        "log": log,
    }

    artifacts = os.environ.get("KALINKA_GAPLESS_ARTIFACTS")
    if artifacts:
        _write_artifacts(Path(artifacts), result)

    return result


def test_playback_reached_the_end(gapless_capture):
    assert gapless_capture["states"]["finished"], "playback did not reach FINISHED"


def test_the_cut_lands_on_a_peak(gapless_capture):
    """Guards the premise: a split on a zero crossing would hide a small gap."""
    boundary = gapless_capture["boundary"]
    at_cut = abs(int(gapless_capture["reference"][boundary - 1, 0]))
    peak = int(np.abs(gapless_capture["reference"][:, 0]).max())
    assert at_cut > 0.98 * peak, (
        f"track A ends at {at_cut} against a peak of {peak}; the cut has drifted "
        "off the crest, so adjust FREQ/HALF_FRAMES"
    )


def test_no_discontinuity_is_audible_at_the_join(gapless_capture):
    """A click is a step between neighbouring samples that the source never had.

    Independent of the reference comparison: a continuous 441.5 Hz sine has a
    bounded sample-to-sample slope, so any splice shows up as a larger step.
    """
    captured = gapless_capture["captured"][:, 0].astype(np.int32)
    reference = gapless_capture["reference"][:, 0].astype(np.int32)
    assert np.abs(np.diff(captured)).max() <= np.abs(np.diff(reference)).max()


def test_both_tracks_were_played_as_separate_sources(gapless_capture):
    """A real switch happened — this is not one file played twice."""
    source_changed = 5  # AudioGraphNodeState.SOURCE_CHANGED
    changes = gapless_capture["states"]["states"].count(source_changed)
    assert changes >= 2, (
        "expected an initial source change plus one at the track boundary, "
        f"saw {changes}"
    )


def test_frame_count_is_exact(gapless_capture):
    """No frames inserted, dropped or duplicated across the boundary."""
    captured = gapless_capture["captured"]
    reference = gapless_capture["reference"]
    assert len(captured) == len(reference), (
        f"captured {len(captured)} frames, expected {len(reference)} "
        f"(difference of {len(captured) - len(reference)} frames)"
    )


def test_output_is_sample_identical_to_one_continuous_file(gapless_capture):
    """The strong claim: two tracks are indistinguishable from one file."""
    captured = gapless_capture["captured"]
    reference = gapless_capture["reference"]

    if not np.array_equal(captured, reference):
        differing = np.flatnonzero((captured != reference).any(axis=1))
        pytest.fail(
            f"{len(differing)} of {len(reference)} frames differ; "
            f"first at frame {differing[0]} "
            f"(boundary is at {gapless_capture['boundary']})"
        )


def test_no_silence_was_inserted_at_the_boundary(gapless_capture):
    """Guards the specific failure of padding a partial buffer with zeros."""
    captured = gapless_capture["captured"]
    reference = gapless_capture["reference"]
    assert longest_silent_run(captured) <= longest_silent_run(reference)


def test_boundary_did_not_drain_the_device(gapless_capture):
    """Same-format boundary must take the cheap path, not reconfigure ALSA."""
    assert "draining" not in gapless_capture["log"], (
        "emitter drained the PCM device at a same-format boundary:\n"
        + gapless_capture["log"][-2000:]
    )


@pytest.fixture(scope="module")
def format_change_capture(tmp_path_factory) -> dict:
    """Same audio, second track declared at 48 kHz: an incompatible boundary."""
    tmp_path = tmp_path_factory.mktemp("format_change")
    tone = _tone(FREQ, HALF_FRAMES)
    sf.write(str(tmp_path / "a.flac"), tone, 44100, subtype="PCM_16")
    sf.write(str(tmp_path / "b48.flac"), tone, 48000, subtype="PCM_16")

    captured, states, log = _play_and_capture(
        tmp_path, [tmp_path / "a.flac", tmp_path / "b48.flac"]
    )
    return {"captured": captured, "states": states, "log": log}


def test_format_change_boundary_reconfigures_instead(format_change_capture):
    """The counterpart to the gapless path — and proof it is distinguishable.

    Without this, ``test_boundary_did_not_drain_the_device`` could pass simply
    because the drain message never appears under any circumstances.
    """
    log = format_change_capture["log"]
    assert "draining" in log, (
        "expected a drain at a 44.1 kHz -> 48 kHz boundary; the same-format "
        "assertion is only meaningful if this path is reachable"
    )
    assert log.count("Audio format set up") >= 2, "device was not reconfigured"


def test_silence_detector_would_catch_a_gap(gapless_capture):
    """Negative control: the assertions above have teeth.

    Splice 5 ms of silence into the boundary and confirm both the equality check
    and the silence detector reject it.
    """
    gap_frames = int(0.005 * SAMPLE_RATE)
    boundary = gapless_capture["boundary"]
    reference = gapless_capture["reference"]

    gapped = np.concatenate(
        [
            reference[:boundary],
            np.zeros((gap_frames, 2), dtype="<i2"),
            reference[boundary:],
        ]
    )

    assert not np.array_equal(gapped, reference)
    assert longest_silent_run(gapped) >= gap_frames
    assert longest_silent_run(reference) < gap_frames

    # The click detector must fire too. Cutting on a crest means the splice
    # introduces a step of a full peak amplitude, against ~1k for the tone.
    step = lambda x: np.abs(np.diff(x[:, 0].astype(np.int32))).max()  # noqa: E731
    assert step(gapped) > 10 * step(reference)


# --------------------------------------------------------------------------
# SVG artifacts (documentation only; no plotting dependency)
# --------------------------------------------------------------------------


def _waveform_path(samples: np.ndarray, width: int, height: int, peak: float) -> str:
    """Waveform of `samples` as an SVG path, y centred in `height`.

    With several samples per pixel this is a min/max envelope. With roughly one
    sample per pixel an envelope would collapse to zero height (min == max), so
    trace a polyline through the samples instead.
    """
    if len(samples) == 0:
        return ""
    values = samples.astype(np.float64)
    mid, scale = height / 2, (height / 2) * 0.9 / max(peak, 1.0)

    if len(values) < 2 * width:
        step = (width - 1) / max(len(values) - 1, 1)
        points = [f"{i * step:.2f},{mid - v * scale:.2f}" for i, v in enumerate(values)]
        # Close the area back along the centre line, otherwise the fill spans a
        # diagonal from the last sample to the first.
        points = [f"0,{mid:.2f}"] + points + [f"{(len(values) - 1) * step:.2f},{mid:.2f}"]
        return "M" + " L".join(points) + " Z"

    top, bottom = [], []
    for x, column in enumerate(np.array_split(values, width)):
        if len(column) == 0:
            continue
        top.append(f"{x},{mid - column.max() * scale:.2f}")
        bottom.append(f"{x},{mid - column.min() * scale:.2f}")
    return "M" + " L".join(top + list(reversed(bottom))) + " Z"


TITLE_BAND = 22
PLOT_HEIGHT = 112
COLOUR_A = "#3b6fd4"  # frames that came from track A
COLOUR_B = "#e07b1f"  # frames that came from track B


def _sample_points(
    values: np.ndarray, first_index: int, total: int, width: int, height: int, peak: float
) -> list[tuple[float, float]]:
    """Pixel positions for `values`, whose first element is `first_index` of `total`."""
    mid, scale = height / 2, (height / 2) * 0.9 / max(peak, 1.0)
    step = (width - 1) / max(total - 1, 1)
    return [
        ((first_index + i) * step, mid - float(v) * scale) for i, v in enumerate(values)
    ]


def _source_split_panel(
    title: str,
    samples: np.ndarray,
    split: int,
    width: int,
    peak: float,
    dots: bool,
) -> str:
    """One panel of captured audio, coloured by which track each frame came from.

    `split` is the index of the first frame that came from track B. With `dots`
    the individual samples are drawn as stems, the way an editor shows them at
    high zoom; otherwise the samples are joined into a filled waveform.
    """
    mid = PLOT_HEIGHT / 2
    parts = [
        f'<text x="1" y="14" font-family="sans-serif" font-size="12.5" '
        f'fill="#33333d">{title}</text>',
        f'<rect x="0" y="{TITLE_BAND}" width="{width}" height="{PLOT_HEIGHT}" '
        f'fill="#fbfbfd" stroke="#d8d8e0" stroke-width="1"/>',
    ]
    body = [
        f'<line x1="0" y1="{mid}" x2="{width}" y2="{mid}" stroke="#e2e2ea" '
        f'stroke-width="1"/>'
    ]

    total = len(samples)
    # Overlap by one sample so the two colours meet without a visual gap.
    segments = [
        (samples[: split + 1], 0, COLOUR_A),
        (samples[split:], split, COLOUR_B),
    ]
    for values, first, colour in segments:
        points = _sample_points(values, first, total, width, PLOT_HEIGHT, peak)
        if dots:
            for x, y in points:
                body.append(
                    f'<line x1="{x:.2f}" y1="{mid}" x2="{x:.2f}" y2="{y:.2f}" '
                    f'stroke="{colour}" stroke-width="1.3"/>'
                    f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.4" fill="{colour}"/>'
                )
        else:
            # Stroke only. Filling to the centre line self-intersects at every
            # zero crossing, which the nonzero fill rule renders as a band.
            line = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in points)
            body.append(
                f'<path d="{line}" fill="none" stroke="{colour}" stroke-width="1.6" '
                f'stroke-linejoin="round"/>'
            )

    x_split = _sample_points(samples[split : split + 1], split, total, width, PLOT_HEIGHT, peak)[0][0]
    body.append(
        f'<line x1="{x_split:.2f}" y1="0" x2="{x_split:.2f}" y2="{PLOT_HEIGHT}" '
        f'stroke="#8a8a99" stroke-width="1" stroke-dasharray="4 3"/>'
    )

    parts.append(f'<g transform="translate(0,{TITLE_BAND})">' + "".join(body) + "</g>")
    return "".join(parts)


def _write_source_split_svg(
    path: Path, panels: list[tuple[str, np.ndarray, int, bool]]
) -> None:
    width, gap = 880, 16
    panel_height = TITLE_BAND + PLOT_HEIGHT
    legend_height = 26
    total = len(panels) * (panel_height + gap) - gap + legend_height
    peak = max(float(np.abs(s).max()) for _, s, _, _ in panels)

    body = []
    for i, (title, samples, split, dots) in enumerate(panels):
        body.append(f'<g transform="translate(0,{i * (panel_height + gap)})">')
        body.append(_source_split_panel(title, samples, split, width, peak, dots))
        body.append("</g>")

    y = len(panels) * (panel_height + gap) - gap + 17
    body.append(
        f'<rect x="1" y="{y - 9}" width="11" height="11" fill="{COLOUR_A}"/>'
        f'<text x="18" y="{y}" font-family="sans-serif" font-size="12" '
        f'fill="#33333d">frames decoded from a.flac</text>'
        f'<rect x="215" y="{y - 9}" width="11" height="11" fill="{COLOUR_B}"/>'
        f'<text x="232" y="{y}" font-family="sans-serif" font-size="12" '
        f'fill="#33333d">frames decoded from b.flac</text>'
    )
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{total}" '
        f'viewBox="0 0 {width} {total}">' + "".join(body) + "</svg>"
    )


def _panel(
    title: str,
    samples: np.ndarray,
    width: int,
    marker: int | None,
    peak: float,
) -> str:
    mid = TITLE_BAND + PLOT_HEIGHT / 2
    parts = [
        f'<text x="1" y="14" font-family="sans-serif" font-size="12.5" '
        f'fill="#33333d">{title}</text>',
        f'<rect x="0" y="{TITLE_BAND}" width="{width}" height="{PLOT_HEIGHT}" '
        f'fill="#fbfbfd" stroke="#d8d8e0" stroke-width="1"/>',
        f'<line x1="0" y1="{mid}" x2="{width}" y2="{mid}" stroke="#e2e2ea" '
        f'stroke-width="1"/>',
        f'<g transform="translate(0,{TITLE_BAND})">'
        f'<path d="{_waveform_path(samples, width, PLOT_HEIGHT, peak)}" '
        f'fill="#3b6fd4" fill-opacity="0.22" stroke="#3b6fd4" '
        f'stroke-width="1.2" stroke-linejoin="round"/></g>',
    ]
    if marker is not None and len(samples):
        x = marker / len(samples) * width
        parts.append(
            f'<line x1="{x:.1f}" y1="{TITLE_BAND}" x2="{x:.1f}" '
            f'y2="{TITLE_BAND + PLOT_HEIGHT}" stroke="#d1344a" stroke-width="1.5" '
            f'stroke-dasharray="5 3"/>'
        )
        parts.append(
            f'<text x="{x + 7:.1f}" y="{TITLE_BAND + 15}" font-family="sans-serif" '
            f'font-size="11.5" fill="#d1344a" stroke="#fbfbfd" stroke-width="3.5" '
            f'paint-order="stroke">track boundary</text>'
        )
    return "".join(parts)


def _write_svg(path: Path, panels: list[tuple[str, np.ndarray, int | None]]) -> None:
    width, gap = 880, 16
    panel_height = TITLE_BAND + PLOT_HEIGHT
    total = len(panels) * (panel_height + gap) - gap
    # One amplitude scale across all panels so they can be compared by eye.
    peak = max((float(np.abs(s).max()) for _, s, _ in panels if len(s)), default=1.0)
    body = []
    for i, (title, samples, marker) in enumerate(panels):
        body.append(f'<g transform="translate(0,{i * (panel_height + gap)})">')
        body.append(_panel(title, samples, width, marker, peak))
        body.append("</g>")
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{total}" viewBox="0 0 {width} {total}">'
        + "".join(body)
        + "</svg>"
    )


GAP_SECONDS = 0.003  # size of the spliced-in gap used for comparison


def _write_wavs(out_dir: Path, result: dict) -> None:
    """Write the inputs, the captured output, and a gapped counterexample.

    All are 16-bit stereo WAV at 44.1 kHz, openable directly in Audacity or any
    editor. ``captured-joined.wav`` is exactly what the emitter handed to ALSA.
    """
    boundary = result["boundary"]
    captured = result["captured"]
    gap = np.zeros((int(GAP_SECONDS * SAMPLE_RATE), 2), dtype="<i2")

    files = {
        "track-a.wav": result["track_a"],
        "track-b.wav": result["track_b"],
        "captured-joined.wav": captured,
        "captured-with-gap.wav": np.concatenate(
            [captured[:boundary], gap, captured[boundary:]]
        ),
    }
    for name, frames in files.items():
        sf.write(str(out_dir / name), frames, SAMPLE_RATE, subtype="PCM_16")

    # The exact FLAC inputs that were played, so the whole result can be
    # re-verified with the reference `flac` decoder and `cmp` — no numpy, and
    # none of this file, in the measurement chain.
    _write_flac(out_dir / "a.flac", result["track_a"])
    _write_flac(out_dir / "b.flac", result["track_b"])

    # Raw interleaved PCM too, for Audacity's File > Import > Raw Data
    # (signed 16-bit little-endian, 2 channels, 44100 Hz).
    captured.tofile(str(out_dir / "captured-joined.raw"))


def _write_artifacts(out_dir: Path, result: dict) -> None:
    """Write WAV clips plus SVGs of the boundary (panels span 12 ms each)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_wavs(out_dir, result)

    span = int(0.012 * SAMPLE_RATE)  # 12 ms per panel
    half = span // 2
    boundary = result["boundary"]
    captured = result["captured"][:, 0]

    # The captured output, coloured by which file each frame was decoded from.
    # Both files hold the same tone, so an uncoloured plot of this would be an
    # unremarkable sine; the colour is what shows the two tracks meeting.
    close = 22  # ~0.5 ms either side of the join: individual samples
    _write_source_split_svg(
        out_dir / "gapless-join.svg",
        [
            (
                "Captured ALSA output across the track boundary (12 ms)",
                captured[boundary - half : boundary + half],
                half,
                False,
            ),
            (
                "The same boundary at sample resolution (1 ms): A's last frame, "
                "then B's first",
                captured[boundary - close : boundary + close],
                close,
                True,
            ),
        ],
    )

    gap_frames = int(GAP_SECONDS * SAMPLE_RATE)
    gapped = np.concatenate(
        [captured[:boundary], np.zeros(gap_frames, dtype="<i2"), captured[boundary:]]
    )
    _write_svg(
        out_dir / "gapless-vs-gap.svg",
        [
            (
                "What Kalinka writes: B's first frame follows A's last frame",
                captured[boundary - half : boundary + half],
                half,
            ),
            (
                f"What padding a partial buffer would write: {GAP_SECONDS * 1000:.0f} ms of silence spliced in",
                gapped[boundary - half : boundary + half + gap_frames],
                half,
            ),
        ],
    )
