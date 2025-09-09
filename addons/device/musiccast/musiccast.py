import time
import httpx
import logging
from .config_model import MusicCastConfig
from src.events import EventType

from src.ext_device import ExternalOutputDevice, SupportedFunction, DeviceVolume
from src.playqueue import PlayQueue
from src.async_common import EventEmitter

import threading
import socket
import random
import json
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__.split(".")[-1])


def is_port_available(address, port):
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


def find_available_port(address, start_range=49152, end_range=65535):
    # We expect that we will find an available port within 100 tries
    for _ in range(100):
        port = random.randint(start_range, end_range)

        logger.debug(f"Checking port {port}")
        if is_port_available(address, port):
            return port

        logger.debug(f"Port {port} is not available")

    # If no available port is found, return None
    logger.error(f"Could not find available port in range {start_range}-{end_range}")
    return None


def get_network_interfaces():
    """Get all active network interfaces with their IP addresses"""
    interfaces = []

    try:
        import netifaces

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

    except ImportError:
        logger.info("netifaces not available, using socket-based interface detection")

        # Fallback: try to detect interfaces using socket
        try:
            import subprocess

            result = subprocess.run(
                ["ip", "addr", "show"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                current_interface = None
                for line in result.stdout.split("\n"):
                    # Parse interface names
                    if line.strip() and not line.startswith(" "):
                        parts = line.split(":")
                        if len(parts) >= 2:
                            current_interface = parts[1].strip()
                    # Parse IP addresses
                    elif "inet " in line and current_interface:
                        parts = line.strip().split()
                        for i, part in enumerate(parts):
                            if part == "inet" and i + 1 < len(parts):
                                ip = parts[i + 1].split("/")[0]
                                if not ip.startswith("127.") and ip != "0.0.0.0":
                                    interfaces.append((current_interface, ip))
                                    logger.debug(
                                        f"Found interface {current_interface}: {ip}"
                                    )
                                break
        except Exception as e:
            logger.debug(f"Failed to detect interfaces via ip command: {e}")

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


def discover_musiccast_devices(timeout_seconds=10) -> Optional[Dict[str, Any]]:
    """
    Discover MusicCast devices using SSDP (Simple Service Discovery Protocol).
    Sends M-SEARCH multicast requests on all network interfaces and listens for responses.
    Returns the first device found or None if no devices are discovered.
    """
    logger.info("Starting SSDP MusicCast device discovery...")

    # SSDP multicast address and port
    SSDP_ADDR = "239.255.255.250"
    SSDP_PORT = 1900

    # M-SEARCH request for MusicCast devices
    # Based on Yamaha MusicCast SSDP specification
    msearch_request = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {timeout_seconds}\r\n"
        "ST: urn:schemas-yamaha-com:device:MusicCast:1\r\n"
        "\r\n"
    ).encode("utf-8")

    found_device = None
    sockets = []

    # Get all network interfaces
    interfaces = get_network_interfaces()
    if not interfaces:
        logger.error("No network interfaces found for SSDP discovery")
        return None

    logger.info(f"Sending SSDP M-SEARCH on {len(interfaces)} interface(s)")

    try:
        # Create sockets for each interface
        for interface_name, interface_ip in interfaces:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(timeout_seconds)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

                # Bind to specific interface
                sock.bind((interface_ip, 0))

                # Send M-SEARCH request from this interface
                logger.debug(
                    f"Sending M-SEARCH from interface {interface_name} ({interface_ip}) to {SSDP_ADDR}:{SSDP_PORT}"
                )
                sock.sendto(msearch_request, (SSDP_ADDR, SSDP_PORT))

                sockets.append((sock, interface_name, interface_ip))

            except Exception as e:
                logger.debug(
                    f"Failed to create socket for interface {interface_name} ({interface_ip}): {e}"
                )
                continue

        if not sockets:
            logger.error("Failed to create any SSDP sockets")
            return None

        start_time = time.time()

        # Listen for responses on all interfaces
        while time.time() - start_time < timeout_seconds:
            try:
                # Use select to check all sockets for incoming data
                import select

                ready_sockets, _, _ = select.select(
                    [sock for sock, _, _ in sockets], [], [], 0.1
                )

                for ready_sock in ready_sockets:
                    try:
                        data, addr = ready_sock.recvfrom(4096)
                        response = data.decode("utf-8")

                        # Find which interface received this response
                        interface_info = next(
                            (name, ip)
                            for sock, name, ip in sockets
                            if sock == ready_sock
                        )
                        logger.debug(
                            f"Received SSDP response from {addr[0]} on interface {interface_info[0]} ({interface_info[1]}):\n{response}"
                        )

                        # Parse SSDP response
                        device = parse_ssdp_response(response, addr[0])
                        if device:
                            found_device = device
                            logger.info(
                                f"Found device on interface {interface_info[0]} ({interface_info[1]})"
                            )
                            break

                    except Exception as e:
                        logger.debug(f"Error receiving SSDP response: {e}")
                        continue

                if found_device:
                    break

            except ImportError:
                # Fallback if select is not available - check sockets sequentially
                for sock, interface_name, interface_ip in sockets:
                    try:
                        data, addr = sock.recvfrom(4096)
                        response = data.decode("utf-8")

                        logger.debug(
                            f"Received SSDP response from {addr[0]} on interface {interface_name} ({interface_ip}):\n{response}"
                        )

                        # Parse SSDP response
                        device = parse_ssdp_response(response, addr[0])
                        if device:
                            found_device = device
                            logger.info(
                                f"Found device on interface {interface_name} ({interface_ip})"
                            )
                            break

                    except socket.timeout:
                        continue
                    except Exception as e:
                        logger.debug(f"Error receiving SSDP response: {e}")
                        continue

                if found_device:
                    break

            except Exception as e:
                logger.debug(f"Error in select loop: {e}")
                continue

    except Exception as e:
        logger.error(f"SSDP discovery failed: {e}")
    finally:
        # Close all sockets
        for sock, _, _ in sockets:
            try:
                sock.close()
            except:
                pass

    if found_device:
        logger.info(
            f"SSDP discovery completed successfully: {found_device['model_name']} at {found_device['ip']}:{found_device['port']}"
        )
    else:
        logger.warning("No MusicCast devices found via SSDP on any interface")

    return found_device


def parse_ssdp_response(response: str, device_ip: str) -> Optional[Dict[str, Any]]:
    """
    Parse SSDP response and extract device information.
    If the response contains a LOCATION header, fetch device details via HTTP.
    """
    lines = response.strip().split("\r\n")

    # Check if this is a valid SSDP response
    if not lines[0].startswith("HTTP/1.1 200 OK"):
        return None

    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().upper()] = value.strip()

    # Look for LOCATION header pointing to device description
    location = headers.get("LOCATION", "")

    # Check if this is a MusicCast device response
    st = headers.get("ST", "")
    if "yamaha" not in st.lower() and "musiccast" not in st.lower():
        return None

    # Extract device information
    device_info = {"ip": device_ip, "port": 80, "location": location}  # Default port

    # If we have a location, try to get more device details
    if location:
        try:
            # Parse port from location URL if available
            import urllib.parse

            parsed = urllib.parse.urlparse(location)
            if parsed.port:
                device_info["port"] = parsed.port
            elif parsed.hostname == device_ip:
                # Try common MusicCast ports
                for port in [5000, 8080, 80]:
                    if verify_musiccast_device(device_ip, port):
                        device_info["port"] = port
                        break

            # Get device details via HTTP API
            device_details = get_device_details(device_ip, device_info["port"])
            if device_details:
                device_info.update(device_details)
                return device_info

        except Exception as e:
            logger.debug(f"Failed to get device details from {device_ip}: {e}")

    # Fallback: try to verify it's a MusicCast device using common ports
    for port in [5000, 8080, 80]:
        device_details = get_device_details(device_ip, port)
        if device_details:
            device_info.update(device_details)
            device_info["port"] = port
            return device_info

    return None


def verify_musiccast_device(ip: str, port: int) -> bool:
    """Quick check if IP:port responds to MusicCast API"""
    try:
        url = f"http://{ip}:{port}/YamahaExtendedControl/v1/system/getDeviceInfo"
        response = httpx.get(url, timeout=2)
        return response.status_code == 200 and response.json().get("response_code") == 0
    except:
        return False


def get_device_details(ip: str, port: int) -> Optional[Dict[str, Any]]:
    """Get detailed device information via HTTP API"""
    try:
        url = f"http://{ip}:{port}/YamahaExtendedControl/v1/system/getDeviceInfo"
        response = httpx.get(url, timeout=3)

        if response.status_code == 200:
            data = response.json()
            if data.get("response_code") == 0 and "model_name" in data:
                logger.info(
                    f"Found MusicCast device: {data.get('model_name')} at {ip}:{port}"
                )
                return {
                    "model_name": data.get("model_name"),
                    "device_id": data.get("device_id"),
                    "system_id": data.get("system_id"),
                    "version": data.get("system_version"),
                    "api_version": data.get("api_version"),
                }
    except Exception:
        pass
    return None


class Device(ExternalOutputDevice):
    def __init__(
        self, config: MusicCastConfig, playqueue: PlayQueue, event_emitter: EventEmitter
    ):
        self.playqueue = playqueue
        self.event_emitter = event_emitter
        self.connected_input = config.connected_input
        self.volume_step_to_db = config.volume_step_to_db
        self.auto_volume = config.auto_volume_correction
        self.session = httpx.Client(timeout=5)

        # Use discovery if no valid device address is configured
        self.device_addr = None
        self.device_port = None

        if config.device_addr and config.device_addr.strip():
            # Use configured address
            self.device_addr = config.device_addr
            self.device_port = config.device_port
            logger.info(
                f"Using configured MusicCast device: {self.device_addr}:{self.device_port}"
            )
        else:
            # Discover device using SSDP
            logger.info(
                "No valid device address configured, starting SSDP discovery..."
            )
            discovered_device = discover_musiccast_devices(
                timeout_seconds=config.discovery_timeout
            )

            if discovered_device:
                self.device_addr = discovered_device["ip"]
                self.device_port = discovered_device["port"]
                logger.info(
                    f"Using discovered device: {discovered_device['model_name']} at {self.device_addr}:{self.device_port}"
                )
            else:
                raise Exception(
                    "No MusicCast devices found via SSDP discovery. MusicCast module will be disabled. Please ensure a MusicCast device is available on the network or configure device_addr manually."
                )

        self.base_url = (
            f"http://{self.device_addr}:{self.device_port}/YamahaExtendedControl/v1"
        )

        # Test connection and get initial status
        status = self._get_status()
        self.volume = DeviceVolume(
            max_volume=status["max_volume"],
            current_volume=status["volume"],
            volume_gain=0,
        )

        self.poweroff_timer = None
        self.udp_port = find_available_port(self.device_addr)
        if self.udp_port is None:
            raise Exception("Could not find available UDP port")
        logger.info(f"Using UDP port {self.udp_port}")
        self.terminate = False
        self.event_loop_thread = threading.Thread(target=self._event_loop, daemon=True)
        self.event_loop_thread.start()
        self.timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self.timer_thread.start()
        self.volume_changed_event = threading.Event()
        threading.Thread(target=self._event_sender, daemon=True).start()

    def __del__(self):
        """Ensure clean shutdown when object is destroyed"""
        self.shutdown()

    def shutdown(self):
        """Gracefully shutdown all threads and close connections"""
        logger.info("Shutting down MusicCast device...")
        self.terminate = True

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

    def _event_sender(self):
        last_sent_volume = -1
        while True:
            self.volume_changed_event.wait()
            self.volume_changed_event.clear()

            if last_sent_volume == self.volume.current_volume:
                continue

            self.event_emitter.dispatch(
                EventType.VolumeChanged, self.volume.current_volume
            )
            last_sent_volume = self.volume.current_volume
            time.sleep(1)

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

                while self.terminate is False:
                    try:
                        # Receive data from the client with larger buffer
                        data, client_address = udp_socket.recvfrom(4096)

                        # Validate that the event came from the expected device
                        if client_address[0] != self.device_addr:
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

        # Handle main zone events
        if "main" in event_json:
            self._handle_zone_event(event_json["main"], "main")

        # Handle other zones if needed (zone2, zone3, etc.)
        for zone_name in ["zone2", "zone3", "zone4"]:
            if zone_name in event_json:
                self._handle_zone_event(event_json[zone_name], zone_name)

    def _handle_zone_event(self, zone_state, zone_name):
        # Only process main zone events for now
        if zone_name != "main":
            return

        logger.debug(f"Processing {zone_name} zone event: {zone_state}")

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
        response = self._request_musiccast("/main/getStatus", headers=headers)
        if response["response_code"] != 0:
            logger.warning(
                "MusicCast returned error code %d", response["response_code"]
            )

        return response

    def _set_input(self):
        self._request_musiccast(f"/main/setInput?input={self.connected_input}")

    def _request_musiccast(self, endpoint, headers=None):
        try:
            response = self.session.get(
                self.base_url + endpoint, headers=headers, timeout=5
            )
            if response.status_code != 200:
                raise Exception(f"MusicCast returned {response.status_code}")

            return response.json()
        except Exception as e:
            logger.error(f"MusicCast request failed for {endpoint}: {e}")
            # If the device is unreachable, we could trigger rediscovery here
            # For now, just re-raise the exception
            raise

    def rediscover_device(self) -> bool:
        """
        Re-discover MusicCast device using SSDP if the current one becomes unavailable.
        Returns True if a new device was found, False otherwise.
        """
        logger.info("Current device unreachable, attempting SSDP rediscovery...")

        discovered_device = discover_musiccast_devices(timeout_seconds=10)

        if discovered_device:
            old_addr = f"{self.device_addr}:{self.device_port}"
            self.device_addr = discovered_device["ip"]
            self.device_port = discovered_device["port"]
            self.base_url = (
                f"http://{self.device_addr}:{self.device_port}/YamahaExtendedControl/v1"
            )

            logger.info(
                f"Rediscovered device: {discovered_device['model_name']} at {self.device_addr}:{self.device_port} (was: {old_addr})"
            )
            return True
        else:
            logger.warning("No MusicCast devices found during SSDP rediscovery")
            return False

    def get_volume(self) -> DeviceVolume:
        return self.volume

    def set_volume(self, volume: int) -> None:
        if volume != self.volume.current_volume:
            self.volume.current_volume = volume
            volume = min(volume, self.volume.max_volume)
            volume = max(volume, 0)
            self._request_musiccast(f"/main/setVolume?volume={volume}")

    def power_on(self) -> None:
        if self.is_power_on():
            return
        self._request_musiccast("/main/setPower?power=on")
        self._set_input()

    def power_off(self) -> None:
        self._request_musiccast("/main/setPower?power=standby")

    def is_power_on(self) -> bool:
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
