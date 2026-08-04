import asyncio
import logging
from typing import Annotated, Callable, Literal, Optional, Union

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, TypeAdapter
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice
from kalinka_eventbus import EventBus
from kalinka_plugin_sdk.ext_device_events import ExtDeviceEventType


logger = logging.getLogger(__name__.split(".")[-1])


# Command Models
class PowerOnCommand(BaseModel):
    command: Literal["power_on"] = "power_on"


class PowerOffCommand(BaseModel):
    command: Literal["power_off"] = "power_off"


class SetVolumeCommand(BaseModel):
    command: Literal["set_volume"] = "set_volume"
    # Volume is in the device's native scale; the plugin clamps to its own
    # max_volume (e.g. MusicCast = 161, others = 100). Don't enforce an upper
    # bound here or commands targeting devices with larger ranges silently fail.
    volume: int = Field(..., ge=0, description="Volume level in device-native units")


# Discriminated union of all commands
DeviceCommand = Annotated[
    Union[PowerOnCommand, PowerOffCommand, SetVolumeCommand],
    Field(discriminator="command"),
]

command_adapter = TypeAdapter(DeviceCommand)


async def handle_websocket_connection(
    websocket: WebSocket,
    device_eventbus: EventBus,
    resolve_device: Callable[[], Optional[ExternalOutputDevice]],
):
    """Handle WebSocket connection for real-time device control and event streaming.

    Supports:
    - Reading: Device events (power state changes, volume changes)
    - Writing: Device control commands (power_on, power_off, set_volume, is_power_on, get_volume)

    The target is resolved per command, not per connection: which module owns
    volume and power follows the active renderer, and a long-lived socket must
    not keep addressing the module that happened to own it at connect time.
    With none resolved the stream stays up (clients still get the initial state
    replay) and control commands are ignored.
    """
    await websocket.accept()

    async def send_events():
        """Send device events to the client."""

        async with device_eventbus.stream(list(ExtDeviceEventType)) as stream:

            try:
                async for event in stream:
                    if event is not None:
                        await websocket.send_text(event.model_dump_json())
            except Exception as e:
                logger.error(f"Error sending device events: {e}")

    async def receive_commands():
        """Receive and process device control commands from the client."""
        try:
            while True:
                data = await websocket.receive_json()

                try:
                    # Pydantic automatically deserializes to the correct command type
                    cmd = command_adapter.validate_python(data)

                    device = resolve_device()
                    if device is None:
                        logger.warning(
                            "Ignoring %s: no output device configured", cmd.command
                        )
                    elif isinstance(cmd, PowerOnCommand):
                        await device.power_on()
                    elif isinstance(cmd, PowerOffCommand):
                        await device.power_off()
                    elif isinstance(cmd, SetVolumeCommand):
                        await device.set_volume(cmd.volume)

                except ValueError as e:
                    logger.error(f"Error validating command: {e}")
                except NotImplementedError:
                    logger.warning(f"Command not supported by device")
                except Exception as e:
                    logger.error(f"Error processing command: {e}")

        except WebSocketDisconnect:
            logger.info("Device WebSocket client disconnected")

    send_task = asyncio.create_task(send_events())
    receive_task = asyncio.create_task(receive_commands())
    try:
        # Stop as soon as either side finishes (disconnect or send error)
        await asyncio.wait({send_task, receive_task}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        logger.debug("Device WebSocket connection cancelled")
        raise
    finally:
        send_task.cancel()
        receive_task.cancel()
        await asyncio.gather(send_task, receive_task, return_exceptions=True)
        try:
            await websocket.close()
        except Exception:
            pass
