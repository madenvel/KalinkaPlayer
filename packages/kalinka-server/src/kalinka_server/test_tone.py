"""Speaker test: play a short sine tone through the native player.

Used by the client's first-run setup wizard to verify the selected ALSA
output and channel wiring. The tone goes through the exact same pipeline as
normal playback (AudioPlayer -> stream switcher -> ALSA emitter); only the
source differs — a ``tone://`` pseudo-stream generated in-process by the
native SineWaveNode, so no decoder or network is involved.

A dedicated, short-lived AudioPlayer instance is created per tone so the
play queue's player and its state machine are never touched. The caller
must stop queue playback first (see the ``/server/test_tone`` route) —
hw: ALSA devices are exclusive, and the tone needs to open the device.
"""

import asyncio
import logging
import time

from native_player.native_player import (
    AudioFormat,
    AudioGraphNodeState,
    AudioPlayer,
)

logger = logging.getLogger(__name__)

VALID_CHANNELS = ("left", "right", "both")

TONE_FREQUENCY_HZ = 440
TONE_DURATION_MS = 2000

# Device open + state propagation slack on top of the tone itself.
_COMPLETION_GRACE_S = 5.0
_POLL_INTERVAL_S = 0.1

# One tone at a time — two concurrent opens of the same exclusive ALSA
# device would make the second one fail spuriously.
_tone_lock = asyncio.Lock()


async def play_test_tone(
    player_config: dict,
    *,
    channel: str,
    device: str | None = None,
) -> None:
    """Play a test tone and return once it finished playing.

    ``player_config`` is the flattened native-player config (the play
    queue's ``config`` dict). ``device`` overrides the configured ALSA
    output — the client passes its not-yet-applied selection so the tone
    tests what the user actually picked.

    Raises ``ValueError`` for a bad channel, ``RuntimeError`` when the
    player reports an error (e.g. the device cannot be opened), and
    ``TimeoutError`` if playback never finishes.
    """
    if channel not in VALID_CHANNELS:
        raise ValueError(f"channel must be one of {VALID_CHANNELS}")

    config = dict(player_config)
    if device:
        config["output.alsa.device"] = str(device)

    async with _tone_lock:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _play_blocking, config, channel)


def _play_blocking(config: dict, channel: str) -> None:
    logger.info(
        "Playing %sms test tone on channel=%s device=%s",
        TONE_DURATION_MS,
        channel,
        config.get("output.alsa.device", "default"),
    )
    player = AudioPlayer(config)
    try:
        url = f"tone://{channel}?freq={TONE_FREQUENCY_HZ}&duration_ms={TONE_DURATION_MS}"
        # The format argument is ignored for tone:// (no decoder is attached).
        player.append(url, AudioFormat.FLAC)

        deadline = time.monotonic() + TONE_DURATION_MS / 1000 + _COMPLETION_GRACE_S
        while time.monotonic() < deadline:
            state = player.get_state()
            if state.state == AudioGraphNodeState.FINISHED:
                return
            if state.state == AudioGraphNodeState.ERROR:
                message = state.error.message if state.error else "unknown audio error"
                raise RuntimeError(message)
            time.sleep(_POLL_INTERVAL_S)
        raise TimeoutError("test tone did not finish in time")
    finally:
        # Always release the audio device, whatever state we ended up in.
        player.stop()
