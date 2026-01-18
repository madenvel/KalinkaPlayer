import asyncio
import logging
from typing import Literal, Union, Annotated

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
    volume: int = Field(..., ge=0, le=100, description="Volume level (0-100)")


# Discriminated union of all commands
DeviceCommand = Annotated[
    Union[PowerOnCommand, PowerOffCommand, SetVolumeCommand],
    Field(discriminator="command"),
]

command_adapter = TypeAdapter(DeviceCommand)


async def handle_websocket_connection(
    websocket: WebSocket,
    device_eventbus: EventBus,
    device: ExternalOutputDevice,
):
    """Handle WebSocket connection for real-time device control and event streaming.

    Supports:
    - Reading: Device events (power state changes, volume changes)
    - Writing: Device control commands (power_on, power_off, set_volume, is_power_on, get_volume)
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

                    if isinstance(cmd, PowerOnCommand):
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

    try:
        # Run both send and receive concurrently
        await asyncio.gather(send_events(), receive_commands(), return_exceptions=False)
    except asyncio.CancelledError:
        logger.debug("Device WebSocket connection cancelled")
    except Exception as e:
        logger.error(f"Device WebSocket error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
