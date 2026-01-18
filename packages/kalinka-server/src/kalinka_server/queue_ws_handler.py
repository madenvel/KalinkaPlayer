import asyncio
import logging
from typing import Optional, Literal, Union, Annotated

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, TypeAdapter
from kalinka_plugin_sdk.api import PlayQueueController
from kalinka_eventbus import EventBus
from kalinka_plugin_sdk.events import PlayQueueEventType


logger = logging.getLogger(__name__.split(".")[-1])


# Command Models
class PlayCommand(BaseModel):
    command: Literal["play"] = "play"
    index: Optional[int] = None


class PauseCommand(BaseModel):
    command: Literal["pause"] = "pause"
    paused: bool = True


class NextCommand(BaseModel):
    command: Literal["next"] = "next"


class PrevCommand(BaseModel):
    command: Literal["prev"] = "prev"


class StopCommand(BaseModel):
    command: Literal["stop"] = "stop"


class SeekCommand(BaseModel):
    command: Literal["seek"] = "seek"
    position_ms: int = Field(..., description="Position in milliseconds")


class SetPlaybackModeCommand(BaseModel):
    command: Literal["set_playback_mode"] = "set_playback_mode"
    shuffle: Optional[bool] = None
    repeat_single: Optional[bool] = None
    repeat_all: Optional[bool] = None


# Discriminated union of all commands
QueueCommand = Annotated[
    Union[
        PlayCommand,
        PauseCommand,
        NextCommand,
        PrevCommand,
        StopCommand,
        SeekCommand,
        SetPlaybackModeCommand,
    ],
    Field(discriminator="command"),
]

command_adapter = TypeAdapter(QueueCommand)


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
                        await websocket.send_text(event.model_dump_json())
            except Exception as e:
                logger.error(f"Error sending events: {e}")

    async def receive_commands():
        """Receive and process control commands from the client."""
        try:
            while True:
                data = await websocket.receive_json()

                try:
                    # Pydantic automatically deserializes to the correct command type
                    cmd = command_adapter.validate_python(data)

                    if isinstance(cmd, PlayCommand):
                        await playqueue.play(cmd.index)
                    elif isinstance(cmd, PauseCommand):
                        await playqueue.pause(cmd.paused)
                    elif isinstance(cmd, NextCommand):
                        await playqueue.next()
                    elif isinstance(cmd, PrevCommand):
                        await playqueue.prev()
                    elif isinstance(cmd, StopCommand):
                        await playqueue.stop()
                    elif isinstance(cmd, SeekCommand):
                        await playqueue.seek(cmd.position_ms)
                    elif isinstance(cmd, SetPlaybackModeCommand):
                        await playqueue.set_playback_mode(
                            cmd.shuffle, cmd.repeat_single, cmd.repeat_all
                        )

                except ValueError as e:
                    logger.error(f"Error validating command: {e}")
                except Exception as e:
                    logger.error(f"Error processing command: {e}")

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
