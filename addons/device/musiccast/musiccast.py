import time
import httpx
import logging
import urllib.parse
from .config_model import MusicCastConfig
from src.events import EventType
from ssdpy import SSDPClient
import netifaces


from src.ext_device import ExternalOutputDevice, SupportedFunction, DeviceVolume
from src.async_common import EventEmitter

import threading
import socket
import random
import json
from typing import Optional, Dict, Any

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


class Device(ExternalOutputDevice):
    def __init__(self, config: MusicCastConfig, playqueue, event_emitter: EventEmitter):
        self.playqueue = playqueue
        self.event_emitter = event_emitter
        self.connected_input = config.connected_input
        self.zone_name = config.zone_name
        self.volume_step_to_db = config.volume_step_to_db
        self.auto_volume = config.auto_volume_correction
        self.discovery_timeout = config.discovery_timeout
        self.session = httpx.Client(timeout=5)
        self.ready = False

        if config.device_addr and config.device_addr.strip():
            # Use configured address
            device_addr = config.device_addr
            device_port = config.device_port
            yxc_control_url = "/YamahaExtendedControl/v1/"
            logger.info(
                f"Using configured MusicCast device: {device_addr}:{device_port}"
            )
            self.base_url = f"http://{device_addr}:{device_port}{yxc_control_url}"
            self.get_ready()
        else:
            self.run_discovery()

    def get_ready(self):
        # Test connection and get initial status
        status = self._get_status()
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
        self.terminate = False
        self.ready = True
        self.event_loop_thread = threading.Thread(
            target=self._event_loop, name="MusicCastEventLoopThread", daemon=True
        )
        self.event_loop_thread.start()
        self.timer_thread = threading.Thread(
            target=self._timer_loop, name="MusicCastTimerThread", daemon=True
        )
        self.timer_thread.start()
        self.volume_changed_event = threading.Event()
        threading.Thread(
            target=self._event_sender, name="MusicCastEventSenderThread", daemon=True
        ).start()

    def run_discovery(self):
        self.discovery_thread = threading.Thread(
            target=self._run_discovery_thread,
            name="MusicCastDiscoveryThread",
            daemon=True,
        )
        self.discovery_thread.start()

    def _run_discovery_thread(self):
        interfaces = get_network_interfaces()
        if not interfaces:
            logger.error("No network interfaces found for discovery")
            return

        for iface_name, iface_ip in interfaces:
            logger.info(f"Running discovery on interface {iface_name} ({iface_ip})")
            device_info = discover_musiccast_devices(
                iface=iface_name, timeout_seconds=self.discovery_timeout
            )
            if device_info:
                logger.info(f"Device control URL: {device_info['api_base_url']}")
                self.base_url = device_info["api_base_url"]
                self.get_ready()
                return

        logger.error("MusicCast device discovery failed on all interfaces")

    def __del__(self):
        """Ensure clean shutdown when object is destroyed"""
        self.shutdown()

    def shutdown(self):
        """Gracefully shutdown all threads and close connections"""
        logger.info("Shutting down MusicCast device...")
        self.terminate = True
        if not self.ready:
            return

        # Wait for threads to finish (with timeout)
        if hasattr(self, "event_loop_thread") and self.event_loop_thread.is_alive():
            self.event_loop_thread.join(timeout=2)
        if hasattr(self, "timer_thread") and self.timer_thread.is_alive():
            self.timer_thread.join(timeout=2)

        # Close HTTP session
        if hasattr(self, "session"):
            self.session.close()

    def _db_to_device_units(self, db):
        return round(db / self.volume_step_to_db)

    # This event sender runs in its own thread
    # and used to throttle volume change notifications to once per second
    def _event_sender(self):
        last_sent_volume = None
        last_sent_at = 0.0
        debounce_sec = 0.10
        min_interval_sec = 0.00  # set to 0.10 to cap at 10 Hz

        while not self.terminate:
            self.volume_changed_event.wait()
            self.volume_changed_event.clear()

            logger.info(f"Volume changed event received: {self.volume.current_volume}")
            # Debounce: wait for quiet
            while self.volume_changed_event.wait(timeout=debounce_sec):
                self.volume_changed_event.clear()

            target = self.volume.current_volume

            # Throttle: ensure at least min_interval between sends
            if min_interval_sec > 0:
                now = time.monotonic()
                remaining = (last_sent_at + min_interval_sec) - now
                if remaining > 0:
                    # During throttle wait, keep coalescing new changes
                    if self.volume_changed_event.wait(timeout=remaining):
                        # new change arrived; restart loop to re-debounce
                        self.volume_changed_event.clear()
                        continue

            if target != last_sent_volume:
                self.event_emitter.dispatch(EventType.VolumeChanged, target)
                last_sent_volume = target
                last_sent_at = time.monotonic()

    def _timer_loop(self):
        # Recommended poll time for main zone is 5 seconds
        # But we do not poll and instead rely on events
        while self.terminate is False:
            try:
                self._get_status(
                    headers={
                        "X-AppName": "MusicCast/1.0(Linux)",
                        "X-AppPort": str(self.udp_port),
                    }
                )

                time.sleep(300)
            except Exception as e:
                logger.error(e)
                time.sleep(10)

    def _event_loop(self):
        while self.terminate is False:
            udp_socket = None
            try:
                udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                # Set socket timeout to allow periodic checking of terminate flag
                udp_socket.settimeout(5.0)

                # Bind to local interface, not remote device address
                udp_socket.bind(("", self.udp_port))
                logger.info(
                    f"Listening for MusicCast events on 0.0.0.0:{self.udp_port}"
                )

                device_addr = urllib.parse.urlparse(self.base_url).hostname

                while self.terminate is False:
                    try:
                        # Receive data from the client with larger buffer
                        data, client_address = udp_socket.recvfrom(4096)

                        # Validate that the event came from the expected device
                        if client_address[0] != device_addr:
                            logger.warning(
                                f"Received event from unexpected address: {client_address[0]}"
                            )
                            continue

                        try:
                            event_json = json.loads(data.decode("utf-8"))
                            self._handle_event(event_json)
                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                            logger.warning(f"Failed to decode event data: {e}")
                            continue

                    except socket.timeout:
                        # Timeout is expected, just continue to check terminate flag
                        continue
                    except socket.error as e:
                        logger.error(f"Socket error in event loop: {e}")
                        break

            except socket.error as e:
                logger.error(f"Failed to create/bind UDP socket: {e}")
                time.sleep(10)
            except Exception as e:
                logger.error(f"Unexpected exception in event loop: {e}")
                time.sleep(10)
            finally:
                if udp_socket is not None:
                    try:
                        udp_socket.close()
                    except:
                        pass

    def _handle_event(self, event_json):
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
                self.volume_changed_event.set()
                logger.debug(f"Volume changed to: {new_volume}")

        # Handle power state changes
        power_state = zone_state.get("power")
        input_state = zone_state.get("input")

        if power_state == "standby":
            logger.info("Device entered standby mode")
            self.playqueue.stop()
            return

        if input_state is not None and input_state != self.connected_input:
            logger.info(f"Input changed from {self.connected_input} to {input_state}")
            self.playqueue.stop()
            return

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

    def _on_state_changed(self, state):
        # Don't process state changes if device is not ready yet
        if not self.ready:
            return

        if "state" not in state:
            return
        if state["state"] == "PLAYING":
            self._on_playing(state)
        elif state["state"] == "PAUSED" or state["state"] == "STOPPED":
            self._on_paused_or_stopped(state["state"] == "STOPPED")

    def _on_paused_or_stopped(self, stopped: bool):
        if self.poweroff_timer is not None:
            self.poweroff_timer.cancel()
            self.poweroff_timer = None

        self.poweroff_timer = threading.Timer(60.0, self._self_power_off)
        # Make sure the timer does not prevent the program from exiting
        self.poweroff_timer.daemon = True
        self.poweroff_timer.start()

        # ReplayGain
        if stopped is True and self.volume.volume_gain != 0:
            self.set_volume(self.volume.current_volume - self.volume.volume_gain)
            self.volume.volume_gain = 0

    def _self_power_off(self):
        status = self._get_status()
        if status["input"] == self.connected_input:
            self.power_off()

    def _on_playing(self, state):
        if self.poweroff_timer is not None:
            self.poweroff_timer.cancel()
            self.poweroff_timer = None
        self.power_on()

        # ReplayGain
        if (
            self.auto_volume is True
            and state.get("current_track", {}).get("replaygain_gain", None) is not None
        ):
            device_gain_units = self._db_to_device_units(
                state["current_track"]["replaygain_gain"]
            )
            if self.volume.volume_gain == device_gain_units:
                return

            new_volume = (
                self.volume.current_volume - self.volume.volume_gain + device_gain_units
            )
            self.volume.volume_gain = device_gain_units
            self.set_volume(new_volume)
            logger.info(f"Loudness correction applied: {device_gain_units}")

    def _get_status(self, headers=None):
        response = self._request_musiccast(
            f"/{self.zone_name}/getStatus", headers=headers
        )
        if response["response_code"] != 0:
            logger.warning(
                "MusicCast returned error code %d", response["response_code"]
            )

        return response

    def _set_input(self):
        self._request_musiccast(
            f"/{self.zone_name}/setInput?input={self.connected_input}"
        )

    def _request_musiccast(self, endpoint, headers=None):
        try:
            response = self.session.get(
                safe_urljoin(self.base_url, endpoint),
                headers=headers,
                timeout=5,
            )
            if response.status_code != 200:
                raise Exception(f"MusicCast returned {response.status_code}")

            return response.json()
        except Exception as e:
            logger.error(f"MusicCast request failed for {endpoint}: {e}")
            # If the device is unreachable, we could trigger rediscovery here
            # For now, just re-raise the exception
            raise

    def rediscover_device(self):
        """
        Re-discover MusicCast device using SSDP if the current one becomes unavailable.
        Returns True if a new device was found, False otherwise.
        """
        logger.info("Current device unreachable, attempting SSDP rediscovery...")
        self.ready = False
        self.run_discovery()

    def get_volume(self) -> DeviceVolume:
        if not self.ready:
            return DeviceVolume(
                max_volume=0, current_volume=0, volume_gain=0, supported=False
            )

        return self.volume

    def set_volume(self, volume: int) -> None:
        if not self.ready:
            return

        volume = max(0, min(volume, self.volume.max_volume))
        self._request_musiccast(f"/{self.zone_name}/setVolume?volume={volume}")

    def power_on(self) -> None:
        if not self.ready:
            return

        if self.is_power_on():
            return
        self._request_musiccast(f"/{self.zone_name}/setPower?power=on")
        self._set_input()

    def power_off(self) -> None:
        if not self.ready:
            return
        self._request_musiccast(f"/{self.zone_name}/setPower?power=standby")

    def is_power_on(self) -> bool:
        if not self.ready:
            return False

        status = self._get_status()

        return status["power"] == "on" and status["input"] == self.connected_input

    def supported_functions(self) -> list[SupportedFunction]:
        return [
            SupportedFunction.GET_VOLUME,
            SupportedFunction.SET_VOLUME,
            SupportedFunction.POWER_ON,
            SupportedFunction.IS_POWER_ON,
            SupportedFunction.POWER_OFF,
        ]
