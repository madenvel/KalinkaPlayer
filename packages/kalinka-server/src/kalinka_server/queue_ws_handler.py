import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect
from kalinka_plugin_sdk.api import PlayQueueController
from kalinka_eventbus import EventBus
from kalinka_plugin_sdk.events import PlayQueueEventType


logger = logging.getLogger(__name__.split(".")[-1])


async def handle_websocket_connection(
    websocket: WebSocket,
    playqueue_eventbus: EventBus,
    playqueue: PlayQueueController,
):
    """Handle WebSocket connection for real-time playback control and event streaming.

    Supports:
    - Reading: Queue events
    - Writing: Playback control and device volume control
    """
    await websocket.accept()

    async def send_events():
        """Send queued events to the client."""

        async with playqueue_eventbus.stream(list(PlayQueueEventType)) as stream:

            try:
                async for event in stream:
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
                        await playqueue.play(index)

                    elif command == "pause":
                        paused = data.get("paused", True)
                        await playqueue.pause(paused)

                    elif command == "next":
                        await playqueue.next()

                    elif command == "prev":
                        await playqueue.prev()

                    elif command == "stop":
                        await playqueue.stop()

                    elif command == "seek":
                        position_ms = data.get("position_ms")
                        if position_ms is None:
                            raise ValueError("position_ms is required for seek command")
                        await playqueue.seek(position_ms)

                    elif command == "set_playback_mode":
                        shuffle = data.get("shuffle")
                        repeat_single = data.get("repeat_single")
                        repeat_all = data.get("repeat_all")
                        await playqueue.set_playback_mode(
                            shuffle, repeat_single, repeat_all
                        )

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
        try:
            await websocket.close()
        except Exception:
            pass
