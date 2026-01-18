import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice
from kalinka_eventbus import EventBus
from kalinka_plugin_sdk.ext_device_events import ExtDeviceEventType


logger = logging.getLogger(__name__.split(".")[-1])


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
                command = data.get("command")

                try:
                    if command == "power_on":
                        await device.power_on()
                        await websocket.send_json(
                            {"status": "success", "message": "Device powered on"}
                        )

                    elif command == "power_off":
                        await device.power_off()
                        await websocket.send_json(
                            {"status": "success", "message": "Device powered off"}
                        )

                    elif command == "set_volume":
                        volume = data.get("volume")
                        if volume is None:
                            raise ValueError(
                                "volume is required for set_volume command"
                            )
                        await device.set_volume(volume)
                        await websocket.send_json(
                            {"status": "success", "message": f"Volume set to {volume}"}
                        )

                    elif command == "get_volume":
                        volume_info = await device.get_volume()
                        await websocket.send_json(
                            {
                                "status": "success",
                                "data": volume_info.model_dump(exclude_unset=True),
                            }
                        )

                    elif command == "is_power_on":
                        power_state = await device.is_power_on()
                        await websocket.send_json(
                            {"status": "success", "data": {"power_on": power_state}}
                        )

                    elif command == "supported_functions":
                        functions = device.supported_functions()
                        await websocket.send_json(
                            {
                                "status": "success",
                                "data": {
                                    "functions": [func.value for func in functions]
                                },
                            }
                        )

                    else:
                        logger.warning(f"Unknown command: {command}")
                        await websocket.send_json(
                            {
                                "status": "error",
                                "message": f"Unknown command: {command}",
                            }
                        )

                except ValueError as e:
                    logger.error(f"Error processing command {command}: {e}")
                    await websocket.send_json({"status": "error", "message": str(e)})
                except NotImplementedError:
                    logger.warning(f"Command {command} is not supported by the device")
                    await websocket.send_json(
                        {
                            "status": "error",
                            "message": f"Command {command} is not supported by the device",
                        }
                    )
                except Exception as e:
                    logger.error(f"Error processing command {command}: {e}")
                    await websocket.send_json(
                        {
                            "status": "error",
                            "message": f"Error processing command {command}: {str(e)}",
                        }
                    )

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
