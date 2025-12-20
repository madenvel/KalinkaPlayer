import asyncio
import logging
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice
from kalinka_server.async_common import EventListener
from kalinka_server.playqueue import PlayQueue
from kalinka_server.state_manager import StateManager

from .rest_event_proxy import EventStream, WireEvent

logger = logging.getLogger(__name__.split(".")[-1])


async def handle_websocket_connection(
    websocket: WebSocket,
    event_listener: EventListener,
    playqueue: PlayQueue,
    state_manager: StateManager,
    device: Optional[ExternalOutputDevice],
):
    """Handle WebSocket connection for real-time playback control and event streaming.

    Supports:
    - Reading: Queue events
    - Writing: Playback control and device volume control
    """
    await websocket.accept()
    event_stream = EventStream(event_listener, state_manager)

    async def send_events():
        """Send queued events to the client."""
        try:
            while True:
                event: Optional[WireEvent] = await event_stream.get_last_event()
                if event is not None:
                    await websocket.send_json(
                        {"type": "event", "data": event.model_dump()}
                    )
        except Exception as e:
            logger.error(f"Error sending events: {e}")

    async def receive_commands():
        """Receive and process control commands from the client."""
        try:
            while True:
                data = await websocket.receive_json()
                command = data.get("command")

                try:
                    if command == "play":
                        index = data.get("index")
                        playqueue.play(index)

                    elif command == "pause":
                        paused = data.get("paused", True)
                        playqueue.pause(paused)

                    elif command == "next":
                        playqueue.next()

                    elif command == "prev":
                        playqueue.prev()

                    elif command == "stop":
                        playqueue.stop()

                    elif command == "seek":
                        position_ms = data.get("position_ms")
                        if position_ms is None:
                            raise ValueError("position_ms is required for seek command")
                        playqueue.seek(position_ms).get()

                    elif command == "set_playback_mode":
                        shuffle = data.get("shuffle")
                        repeat_single = data.get("repeat_single")
                        repeat_all = data.get("repeat_all")
                        playqueue.set_playback_mode(shuffle, repeat_single, repeat_all)

                    elif command == "set_volume":
                        volume = data.get("volume")
                        if volume is None:
                            raise ValueError(
                                "volume is required for set_volume command"
                            )
                        if device is not None:
                            device.set_volume(volume)

                except ValueError as e:
                    logger.error(f"Error processing command {command}: {e}")
                except Exception as e:
                    logger.error(f"Error processing command {command}: {e}")

        except WebSocketDisconnect:
            logger.info("WebSocket client disconnected")

    try:
        # Run both send and receive concurrently
        await asyncio.gather(send_events(), receive_commands(), return_exceptions=False)
    except asyncio.CancelledError:
        logger.debug("WebSocket connection cancelled")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        event_stream.close()
        try:
            await websocket.close()
        except Exception:
            pass
