import asyncio
import json
import logging
import random
import socket
import time
import urllib.parse
from typing import Any, Dict, Optional

import httpx
from kalinka_plugin_sdk.datamodel import PlaybackState, PlayerStateEnum
from kalinka_plugin_sdk.ext_device_events import (
    DevicePowerStateChangedEvent,
    ExtDeviceState,
    VolumeChangedEvent,
)
import netifaces
from ssdpy import SSDPClient

from kalinka_plugin_sdk.api import EventEmitter, EventListener, ReplayEvent
from kalinka_plugin_sdk.events import PlayQueueEvent, PlayQueueEventType, PlayQueueState
from kalinka_plugin_sdk.events import PlaybackStateChangedEvent
from kalinka_plugin_sdk.ext_device import (
    DeviceVolume,
    ExternalOutputDevice,
    SupportedFunction,
)
from .config_model import KalinkaPluginMusiccastConfig

logger = logging.getLogger(__name__.split(".")[-1])


def is_port_available(port):
    """Check if a port is available for binding on the local machine"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1)

    try:
        # Try to bind to the specified port on local interface
        # Use '' or '0.0.0.0' to bind to all local interfaces
        sock.bind(("", port))
        return True
    except socket.error:
        return False
    finally:
        # Close the socket
        sock.close()


def find_available_port(start_range=49152, end_range=65535):
    # We expect that we will find an available port within 100 tries
    for _ in range(100):
        port = random.randint(start_range, end_range)

        logger.debug(f"Checking port {port}")
        if is_port_available(port):
            return port

        logger.debug(f"Port {port} is not available")

    # If no available port is found, return None
    logger.error(f"Could not find available port in range {start_range}-{end_range}")
    return None


def get_network_interfaces():
    """Get all active network interfaces with their IP addresses"""

    interfaces = []
    for interface_name in netifaces.interfaces():
        try:
            addresses = netifaces.ifaddresses(interface_name)
            if netifaces.AF_INET in addresses:
                for addr_info in addresses[netifaces.AF_INET]:
                    ip = addr_info.get("addr")
                    if ip and not ip.startswith("127.") and ip != "0.0.0.0":
                        interfaces.append((interface_name, ip))
                        logger.debug(f"Found interface {interface_name}: {ip}")
        except (KeyError, ValueError):
            continue

    # Final fallback: use default interface
    if not interfaces:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                interfaces.append(("default", local_ip))
                logger.info(f"Using default interface: {local_ip}")
        except Exception:
            logger.warning("Could not detect any network interfaces")

    return interfaces


def discover_musiccast_devices(
    iface: str, timeout_seconds=10
) -> Optional[Dict[str, Any]]:
    """
    Discover MusicCast devices using SSDP via the ssdpy library.
    Much more compact and reliable than manual SSDP implementation.
    """
    logger.info("Starting SSDP MusicCast device discovery...")

    # Create SSDP client with specified timeout
    client = SSDPClient(iface=iface.encode("utf-8"), timeout=timeout_seconds)

    # Search for MediaRenderer devices (per MusicCast specification)
    logger.info("Sending SSDP M-SEARCH for MediaRenderer devices...")
    responses = client.m_search(
        "urn:schemas-upnp-org:device:MediaRenderer:1", mx=timeout_seconds
    )

    logger.info(f"Received {len(responses)} SSDP responses")

    # Process each response to find MusicCast devices
    for response in responses:
        logger.debug(f"Processing SSDP response: {response}")

        # Extract location URL for device description
        location = response.get("location")
        if not location:
            logger.debug("No location header in SSDP response")
            continue

        # Parse device IP from response source
        device_ip = None
        if "source" in response:
            # ssdpy includes source IP in some versions
            device_ip = response["source"][0]
        else:
            # Extract from location URL as fallback
            parsed_url = urllib.parse.urlparse(location)
            device_ip = parsed_url.hostname

        if not device_ip:
            logger.debug("Could not determine device IP")
            continue

        logger.debug(f"Fetching device description from: {location}")

        # Fetch and parse device description XML
        try:
            http_response = httpx.get(location, timeout=5)
            if http_response.status_code != 200:
                logger.debug(
                    f"Failed to fetch device description: HTTP {http_response.status_code}"
                )
                continue

            device_desc_xml = http_response.text
            logger.debug(f"Device description XML:\n{device_desc_xml}")

            # Parse XML to check for Yamaha MusicCast device
            device_info = parse_device_description(device_desc_xml, device_ip)
            if device_info:
                return device_info

        except Exception as e:
            logger.debug(f"Error fetching device description from {location}: {e}")
            continue

    logger.info("No MusicCast devices found via SSDP discovery")
    return None


def parse_device_description(
    xml_content: str, device_ip: str
) -> Optional[Dict[str, Any]]:
    """
    Parse device description XML to verify it's a Yamaha MusicCast device
    and extract necessary information per specification.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_content)

        # Define namespaces
        namespaces = {
            "upnp": "urn:schemas-upnp-org:device-1-0",
            "yamaha": "urn:schemas-yamaha-com:device-1-0",
        }

        # Find the device element
        device_elem = root.find(".//upnp:device", namespaces)
        if device_elem is None:
            logger.debug("No device element found in XML")
            return None

        # Check manufacturer - must be "Yamaha Corporation"
        manufacturer = device_elem.find("upnp:manufacturer", namespaces)
        if manufacturer is None or manufacturer.text != "Yamaha Corporation":
            logger.debug(
                f"Not a Yamaha device, manufacturer: {manufacturer.text if manufacturer is not None else 'None'}"
            )
            return None

        # Check for Yamaha X_device tag
        x_device = root.find(".//yamaha:X_device", namespaces)
        if x_device is None:
            logger.debug("No yamaha:X_device tag found")
            return None

        # Extract device information
        model_name = device_elem.find("upnp:modelName", namespaces)
        model_desc = device_elem.find("upnp:modelDescription", namespaces)
        friendly_name = device_elem.find("upnp:friendlyName", namespaces)
        serial_number = device_elem.find("upnp:serialNumber", namespaces)
        udn = device_elem.find("upnp:UDN", namespaces)

        # Extract Yamaha-specific information
        url_base = x_device.find("yamaha:X_URLBase", namespaces)

        # Find the YXC control URL
        yxc_control_url = None
        service_list = x_device.find("yamaha:X_serviceList", namespaces)
        if service_list is not None:
            for service in service_list.findall("yamaha:X_service", namespaces):
                spec_type = service.find("yamaha:X_specType", namespaces)
                if (
                    spec_type is not None
                    and spec_type.text
                    and "YamahaExtendedControl" in spec_type.text
                ):
                    yxc_url_elem = service.find("yamaha:X_yxcControlURL", namespaces)
                    if yxc_url_elem is not None:
                        yxc_control_url = yxc_url_elem.text
                        break

        if not yxc_control_url:
            logger.debug("No YamahaExtendedControl URL found")
            return None

        # Extract base URL and port
        base_url: str = (
            url_base.text
            if (url_base is not None and url_base.text is not None)
            else f"http://{device_ip}:80/"
        )

        # Verify this is actually a MusicCast device by testing the API
        api_base_url = safe_urljoin(base_url, yxc_control_url)
        device_details = verify_musiccast_api(api_base_url)

        if not device_details:
            logger.debug("Device does not respond to MusicCast API")
            return None

        logger.info(
            f"Found MusicCast device: {device_details['model_name']} at {base_url}"
        )

        return {
            "api_base_url": api_base_url,
            "yxc_control_url": yxc_control_url,
            "friendly_name": friendly_name.text if friendly_name is not None else None,
            "serial_number": serial_number.text if serial_number is not None else None,
            "udn": udn.text if udn is not None else None,
            **device_details,
        }

    except ET.ParseError as e:
        logger.debug(f"XML parsing error: {e}")
        return None
    except Exception as e:
        logger.debug(f"Error parsing device description: {e}")
        return None


def safe_urljoin(base: str, path: str) -> str:
    """
    Safely join a base URL with a path, handling cases where both base and path
    have slashes to avoid double slashes or missing path components.
    """
    # Ensure base ends with /
    if not base.endswith("/"):
        base += "/"

    # Remove leading / from path to avoid urljoin treating it as absolute
    if path.startswith("/"):
        path = path[1:]

    return urllib.parse.urljoin(base, path)


def verify_musiccast_api(api_base_url: str) -> Optional[Dict[str, Any]]:
    """
    Verify the device responds to MusicCast Extended Control API
    and get device information.
    """
    try:
        # Test the getDeviceInfo endpoint
        url = safe_urljoin(api_base_url, "/system/getDeviceInfo")
        response = httpx.get(url, timeout=3)

        if response.status_code == 200:
            data = response.json()
            if data.get("response_code") == 0 and "model_name" in data:
                return {
                    "model_name": data.get("model_name"),
                    "device_id": data.get("device_id"),
                    "system_id": data.get("system_id"),
                    "version": data.get("system_version"),
                    "api_version": data.get("api_version"),
                }
    except Exception as e:
        logger.debug(f"API verification failed for {api_base_url}: {e}")

    return None


class KalinkaPluginMusiccastDevice(ExternalOutputDevice):
    def __init__(
        self,
        config: KalinkaPluginMusiccastConfig,
        event_emitter: EventEmitter,
        listener: EventListener[PlayQueueEventType, PlayQueueEvent, PlayQueueState],
    ):
        self.event_emitter = event_emitter
        self.listener = listener
        self.connected_input = config.connected_input
        self.zone_name = config.zone_name
        self.volume_step_to_db = config.volume_step_to_db
        self.auto_volume = config.auto_volume_correction
        self.discovery_timeout = config.discovery_timeout
        self.session = httpx.AsyncClient(timeout=5)
        self.ready = False

        self.config = config
        self.base_url = None
        self.tasks = []
        self._discovery_task = None

    async def get_ready(self):
        # Test connection and get initial status
        status = await self._get_status()
        self.volume = DeviceVolume(
            max_volume=status["max_volume"],
            current_volume=status["volume"],
            volume_gain=0,
        )

        self.poweroff_timer = None
        self.udp_port = find_available_port()
        if self.udp_port is None:
            raise Exception("Could not find available UDP port")
        logger.info(f"Using UDP port {self.udp_port}")
        self.shutdown_event = asyncio.Event()
        self.ready = True

        # Track "effective on" state: device powered on AND correct input selected
        self._device_power_on: bool = (
            status["power"] == "on" and status["input"] == self.connected_input
        )

        # Set initial device state after we have retrieved it from the device
        # This must be done before any event emission tasks start
        initial_state = ExtDeviceState(
            power_on=self._device_power_on,
            volume=self.volume,
        )
        self.event_emitter.set_initial_state(initial_state)

    async def run_discovery(self):
        """Run SSDP discovery to find MusicCast device"""
        interfaces = get_network_interfaces()
        if not interfaces:
            logger.error("No network interfaces found for discovery")
            return

        for iface_name, iface_ip in interfaces:
            logger.info(f"Running discovery on interface {iface_name} ({iface_ip})")
            device_info = discover_musiccast_devices(
                iface=iface_name, timeout_seconds=self.config.discovery_timeout
            )
            if device_info:
                logger.info(f"Device control URL: {device_info['api_base_url']}")
                self.base_url = device_info["api_base_url"]
                await self.get_ready()
                return

        logger.error("MusicCast device discovery failed on all interfaces")

    async def start(self):
        """Start the MusicCast device and initialize tasks"""
        logger.info("Starting MusicCast device...")

        # Initialize device connection
        if self.config.device_addr and self.config.device_addr.strip():
            # Use configured address
            device_addr = self.config.device_addr
            device_port = self.config.device_port
            yxc_control_url = "/YamahaExtendedControl/v1/"
            logger.info(
                f"Using configured MusicCast device: {device_addr}:{device_port}"
            )
            self.base_url = f"http://{device_addr}:{device_port}{yxc_control_url}"
            await self.get_ready()
        else:
            # Run discovery with retries as a task
            self._discovery_task = asyncio.create_task(
                self._discovery_worker(max_retries=3)
            )

        # Create and start asyncio tasks
        self.tasks.append(asyncio.create_task(self._event_loop()))
        self.tasks.append(asyncio.create_task(self._timer_loop()))
        self.tasks.append(asyncio.create_task(self._event_sender()))
        self.tasks.append(asyncio.create_task(self._playback_state_listener()))

    async def terminate(self):
        """Gracefully shutdown all tasks and close connections"""
        logger.info("Terminating MusicCast device...")
        if self.ready:
            self.shutdown_event.set()

        # Cancel discovery task if running
        if self._discovery_task and not self._discovery_task.done():
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass

        # Cancel all tasks
        for task in self.tasks:
            if not task.done():
                task.cancel()

        # Wait for all tasks to complete
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

        # Close HTTP session
        if hasattr(self, "session"):
            await self.session.aclose()

    def __del__(self):
        """Ensure clean shutdown when object is destroyed"""
        # Note: Cannot call async methods in __del__, cleanup should be handled explicitly

    def _db_to_device_units(self, db):
        return round(db / self.volume_step_to_db)

    # This event sender runs in its own task
    # and used to throttle volume change notifications to once per second
    async def _event_sender(self):
        last_sent_volume = None
        last_sent_at = 0.0
        debounce_sec = 0.10
        min_interval_sec = 0.00  # set to 0.10 to cap at 10 Hz
        volume_changed = asyncio.Event()
        self._volume_changed_event = volume_changed

        try:
            while not self.shutdown_event.is_set():
                try:
                    # Wait for volume change with timeout to allow checking shutdown_event
                    await asyncio.wait_for(volume_changed.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                volume_changed.clear()

                logger.info(
                    f"Volume changed event received: {self.volume.current_volume}"
                )
                # Debounce: wait for quiet
                try:
                    while True:
                        await asyncio.wait_for(
                            volume_changed.wait(), timeout=debounce_sec
                        )
                        volume_changed.clear()
                except asyncio.TimeoutError:
                    pass

                target = self.volume.current_volume

                # Throttle: ensure at least min_interval between sends
                if min_interval_sec > 0:
                    now = time.monotonic()
                    remaining = (last_sent_at + min_interval_sec) - now
                    if remaining > 0:
                        # During throttle wait, keep coalescing new changes
                        try:
                            await asyncio.wait_for(
                                volume_changed.wait(), timeout=remaining
                            )
                            # new change arrived; restart loop to re-debounce
                            volume_changed.clear()
                            continue
                        except asyncio.TimeoutError:
                            pass

                if target != last_sent_volume:
                    self.event_emitter.dispatch(
                        VolumeChangedEvent(
                            volume=DeviceVolume(
                                max_volume=self.volume.max_volume,
                                current_volume=target,
                                volume_gain=self.volume.volume_gain,
                            )
                        )
                    )
                    last_sent_volume = target
                    last_sent_at = time.monotonic()
        except asyncio.CancelledError:
            logger.debug("Event sender task cancelled")
            raise

    async def _playback_state_listener(self):
        """Listen to playback state changes and call appropriate handlers"""
        try:
            logger.debug("Playback state listener started")
            async with self.listener.stream(
                [PlayQueueEventType.PlaybackStateChanged]
            ) as stream:  # type: ignore
                async for item in stream:
                    # Skip replay events as they are just initial state
                    if isinstance(item, ReplayEvent):
                        logger.debug(
                            f"Received replay event with state: {item.state.playback_state}"
                        )
                        continue

                    # Handle actual playback state change events
                    if isinstance(item, PlaybackStateChangedEvent):
                        new_state = item.state.state
                        logger.debug(f"Playback state changed to: {new_state}")

                        if new_state == PlayerStateEnum.PLAYING:
                            logger.info("Playback started, calling _on_playing")
                            await self._on_playing(item.state)
                        elif new_state == PlayerStateEnum.STOPPED:
                            logger.info("Playback stopped, calling _on_stopped")
                            await self._on_stopped()
        except asyncio.CancelledError:
            logger.debug("Playback state listener task cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in playback state listener: {e}")
            raise

    async def _timer_loop(self):
        """Timer loop that periodically polls device status"""
        # Recommended poll time for main zone is 5 seconds
        # But we do not poll and instead rely on events
        try:
            while not self.shutdown_event.is_set():
                try:
                    await self._get_status(
                        headers={
                            "X-AppName": "MusicCast/1.0(Linux)",
                            "X-AppPort": str(self.udp_port),
                        }
                    )

                    await asyncio.sleep(300)
                except (httpx.ConnectError, httpx.TimeoutException, ConnectionError):
                    # Network error - trigger rediscovery and retry sooner
                    logger.warning(
                        "Network error in timer loop, triggering rediscovery"
                    )
                    await self.rediscover_device()
                    await asyncio.sleep(10)
                except Exception as e:
                    logger.error(f"Timer loop error: {e}")
                    await asyncio.sleep(10)
        except asyncio.CancelledError:
            logger.debug("Timer loop task cancelled")
            raise

    async def _event_loop(self):
        """Event loop that listens for UDP events from MusicCast device"""
        try:
            while not self.shutdown_event.is_set():
                udp_socket = None
                try:
                    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    # Set socket non-blocking for async operation
                    udp_socket.setblocking(False)

                    # Bind to local interface, not remote device address
                    udp_socket.bind(("", self.udp_port))
                    logger.info(
                        f"Listening for MusicCast events on 0.0.0.0:{self.udp_port}"
                    )

                    loop = asyncio.get_event_loop()
                    device_addr = urllib.parse.urlparse(self.base_url).hostname

                    while not self.shutdown_event.is_set():
                        try:
                            data, client_address = await loop.sock_recvfrom(udp_socket, 4096)
                        except OSError as e:
                            logger.error(f"Socket error in event loop: {e}")
                            break

                        # Validate that the event came from the expected device
                        if client_address[0] != device_addr:
                            logger.warning(
                                f"Received event from unexpected address: {client_address[0]}"
                            )
                            continue

                        try:
                            event_json = json.loads(data.decode("utf-8"))
                            await self._handle_event(event_json)
                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                            logger.warning(f"Failed to decode event data: {e}")

                except socket.error as e:
                    logger.error(f"Failed to create/bind UDP socket: {e}")
                    await asyncio.sleep(10)
                except Exception as e:
                    logger.error(f"Unexpected exception in event loop: {e}")
                    await asyncio.sleep(10)
                finally:
                    if udp_socket is not None:
                        try:
                            udp_socket.close()
                        except:
                            pass
        except asyncio.CancelledError:
            logger.debug("Event loop task cancelled")
            raise

    async def _handle_event(self, event_json):
        """Handle incoming MusicCast events from the device"""
        # MusicCast events can contain multiple zones (main, zone2, zone3, etc.)
        # Each zone can have different event data
        logger.debug(f"Received MusicCast event: {event_json}")

        if self.zone_name not in event_json:
            logger.debug(f"No event data for configured zone: {self.zone_name}")
            return

        zone_state = event_json.get(self.zone_name)

        # Handle volume changes
        if "volume" in zone_state:
            new_volume = zone_state["volume"]
            if isinstance(new_volume, int) and new_volume != self.volume.current_volume:
                self.volume.current_volume = new_volume
                if hasattr(self, "_volume_changed_event"):
                    self._volume_changed_event.set()
                logger.debug(f"Volume changed to: {new_volume}")

        # Handle power state changes
        power_state = zone_state.get("power")
        input_state = zone_state.get("input")

        if power_state == "standby":
            logger.info("Device entered standby mode")
            if self._device_power_on:
                self._device_power_on = False
                self.event_emitter.dispatch(DevicePowerStateChangedEvent(power_on=False))
            return

        if input_state is not None and input_state != self.connected_input:
            logger.info(f"Input changed from {self.connected_input} to {input_state}")
            if self._device_power_on:
                self._device_power_on = False
                self.event_emitter.dispatch(DevicePowerStateChangedEvent(power_on=False))
            return

        # Device is on and input matches (or input not reported) — track effective-on state
        if power_state == "on" and (
            input_state is None or input_state == self.connected_input
        ):
            if not self._device_power_on:
                self._device_power_on = True
                self.event_emitter.dispatch(DevicePowerStateChangedEvent(power_on=True))

        # Handle mute state if needed
        if "mute" in zone_state:
            mute_state = zone_state["mute"]
            logger.debug(f"Mute state: {mute_state}")

        # Handle sleep timer if present
        if "sleep" in zone_state:
            sleep_time = zone_state["sleep"]
            logger.debug(f"Sleep timer: {sleep_time}")

        # Handle status_updated flag
        if zone_state.get("status_updated", False):
            logger.debug("Status update event received")

    async def _on_stopped(self):
        # ReplayGain
        if self.volume.volume_gain != 0:
            await self.set_volume(self.volume.current_volume - self.volume.volume_gain)
            self.volume.volume_gain = 0

    async def _on_playing(self, state: PlaybackState):
        # ReplayGain
        if (
            self.auto_volume is True
            and state.current_track is not None
            and state.current_track.replaygain_gain is not None
        ):
            device_gain_units = self._db_to_device_units(
                state.current_track.replaygain_gain
            )
            if self.volume.volume_gain == device_gain_units:
                return

            new_volume = (
                self.volume.current_volume - self.volume.volume_gain + device_gain_units
            )
            self.volume.volume_gain = device_gain_units
            await self.set_volume(new_volume)
            logger.info(f"Loudness correction applied: {device_gain_units}")

    async def _get_status(self, headers=None):
        response = await self._request_musiccast(
            f"/{self.zone_name}/getStatus", headers=headers
        )
        if response["response_code"] != 0:
            logger.warning(
                "MusicCast returned error code %d", response["response_code"]
            )

        return response

    async def _set_input(self):
        await self._request_musiccast(
            f"/{self.zone_name}/setInput?input={self.connected_input}"
        )

    async def _request_musiccast(self, endpoint, headers=None):
        try:
            if self.base_url is None:
                raise Exception("MusicCast device not initialized")

            response = await self.session.get(
                safe_urljoin(self.base_url, endpoint),
                headers=headers,
                timeout=5,
            )
            if response.status_code != 200:
                raise Exception(f"MusicCast returned {response.status_code}")

            return response.json()
        except (httpx.ConnectError, httpx.TimeoutException, ConnectionError) as e:
            logger.error(f"Network error accessing MusicCast device: {e}")
            # Trigger rediscovery on network errors
            await self.rediscover_device()
            raise
        except Exception as e:
            logger.error(f"MusicCast request failed for {endpoint}: {e}")
            raise

    async def rediscover_device(self):
        """
        Re-discover MusicCast device using SSDP if the current one becomes unavailable.
        This runs in the background and doesn't block the caller.
        """
        logger.info("Current device unreachable, attempting SSDP rediscovery...")
        self.ready = False

        # Cancel any existing discovery/rediscovery task
        if self._discovery_task and not self._discovery_task.done():
            self._discovery_task.cancel()

        # Start a new discovery task with retries
        self._discovery_task = asyncio.create_task(
            self._discovery_worker(max_retries=3)
        )

    async def _discovery_worker(self, max_retries: int):
        """Unified discovery worker for both initial discovery and rediscovery with retries."""

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Discovery attempt {attempt}/{max_retries}")
                # Create discovery as a task and wait for it
                discovery_task = asyncio.create_task(self.run_discovery())
                await discovery_task

                if self.ready:
                    logger.info(f"Device discovery successful")
                    # Initial state is already set by get_ready() via set_initial_state()
                    # No need to emit additional events here
                    self._discovery_task = None
                    return
            except Exception as e:
                logger.error(f"Discovery attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    # Exponential backoff: 2s, 5s, 10s
                    wait_time = 2**attempt
                    logger.info(f"Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)

        logger.error(f"Failed discovery device after {max_retries} attempts")
        self._discovery_task = None

    async def get_volume(self) -> DeviceVolume:
        if not self.ready:
            return DeviceVolume(
                max_volume=0, current_volume=0, volume_gain=0, supported=False
            )

        return self.volume

    async def set_volume(self, volume: int) -> None:
        if not self.ready:
            return

        volume = max(0, min(volume, self.volume.max_volume))
        await self._request_musiccast(f"/{self.zone_name}/setVolume?volume={volume}")
        # YXC suppresses the UDP echo for self-issued setVolume, so the cache
        # would otherwise stay frozen until an external source (knob, phone app)
        # nudges it. Update locally and signal the event sender.
        if volume != self.volume.current_volume:
            self.volume.current_volume = volume
            if hasattr(self, "_volume_changed_event"):
                self._volume_changed_event.set()

    async def power_on(self) -> None:
        if not self.ready:
            return

        if await self.is_power_on():
            return
        await self._request_musiccast(f"/{self.zone_name}/setPower?power=on")
        await self._set_input()
        if not self._device_power_on:
            self._device_power_on = True
            self.event_emitter.dispatch(DevicePowerStateChangedEvent(power_on=True))

    async def power_off(self) -> None:
        if not self.ready:
            return
        await self._request_musiccast(f"/{self.zone_name}/setPower?power=standby")

    async def is_power_on(self) -> bool:
        if not self.ready:
            return False

        status = await self._get_status()

        return status["power"] == "on" and status["input"] == self.connected_input

    def supported_functions(self) -> list[SupportedFunction]:
        return [
            SupportedFunction.GET_VOLUME,
            SupportedFunction.SET_VOLUME,
            SupportedFunction.POWER_ON,
            SupportedFunction.IS_POWER_ON,
            SupportedFunction.POWER_OFF,
        ]
