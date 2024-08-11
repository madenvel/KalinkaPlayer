import time
import requests
import logging
from src.events import EventType

from src.ext_device import SupportedFunction, Volume
from src.playqueue import PlayQueue
from src.async_common import EventEmitter
from src.config import config

import threading

import socket
import random
import json

logger = logging.getLogger(__name__.split(".")[-1])


def is_port_available(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    sock.settimeout(1)

    try:
        # Try to bind to the specified port
        sock.bind(("0.0.0.0", port))
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

        logger.info(f"Checking port {port}")
        if is_port_available(port):
            return port

        logger.info(f"Port {port} is not available")

    # If no available port is found, return None
    return None


class Device:
    def __init__(self, playqueue: PlayQueue, event_emitter: EventEmitter):
        self.playqueue = playqueue
        self.event_emitter = event_emitter
        self.connected_input = config["addons"]["device"]["musiccast"][
            "connected_input"
        ]
        self.device_addr = config["addons"]["device"]["musiccast"]["device_addr"]
        self.device_port = config["addons"]["device"]["musiccast"]["device_port"]
        self.session = requests.Session()
        self.base_url = (
            f"http://{self.device_addr}:{self.device_port}/YamahaExtendedControl/v1"
        )
        status = self._get_status()
        self.volume = Volume(
            max_volume=status["max_volume"], current_volume=status["volume"]
        )

        self.poweroff_timer = None
        self.udp_port = find_available_port()
        if self.udp_port is None:
            raise Exception("Could not find available UDP port")
        logger.info(f"Using UDP port {self.udp_port}")
        self.terminate = False
        self.event_loop_thread = threading.Thread(target=self._event_loop, daemon=True)
        self.event_loop_thread.start()
        self.timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self.timer_thread.start()

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
            try:
                udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

                # Bind the socket to a specific address and port
                address = "0.0.0.0"
                udp_socket.bind((address, self.udp_port))
                logger.info(
                    f"Listening for MusicCast events on {address}:{self.udp_port}"
                )

                while self.terminate is False:
                    # Receive data from the client
                    data, client_address = udp_socket.recvfrom(1024)
                    event_json = json.loads(data.decode("utf-8"))
                    self._handle_event(event_json)

            except Exception as e:
                logger.error(f"Caught an exception, restarting: {e}")
                udp_socket.close()
                time.sleep(10)

    def _handle_event(self, event_json):
        # "main":{
        # "power":"on",
        # "input":"optical2",
        # "volume":30,
        # "mute":false,
        # "status_updated":true
        # }
        if "main" not in event_json:
            return

        state = event_json["main"]

        if (
            state.get("volume", self.volume.current_volume)
            != self.volume.current_volume
        ):
            self.volume.current_volume = state["volume"]
            self.event_emitter.dispatch(
                EventType.VolumeChanged, self.volume.current_volume
            )

        if (
            state.get("power", None) == "standby"
            or state.get("input", self.connected_input) != self.connected_input
        ):
            self.playqueue.stop()
            return

    def _on_state_changed(self, state):
        if "state" not in state:
            return
        if state["state"] == "PLAYING":
            self._on_playing()
        elif state["state"] == "PAUSED" or state["state"] == "STOPPED":
            self._on_paused_or_stopped()

    def _on_paused_or_stopped(self):
        if self.poweroff_timer is not None:
            self.poweroff_timer.cancel()
            self.poweroff_timer = None

        self.poweroff_timer = threading.Timer(60.0, self._self_power_off)
        # Make sure the timer does not prevent the program from exiting
        self.poweroff_timer.daemon = True
        self.poweroff_timer.start()

    def _self_power_off(self):
        status = self._get_status()
        if status["input"] == self.connected_input:
            self.power_off()

    def _on_playing(self):
        if self.poweroff_timer is not None:
            self.poweroff_timer.cancel()
            self.poweroff_timer = None
        self.power_on()

    def _get_status(self, headers=None):
        response = self._request_musiccast("/main/getStatus", headers=headers)
        if response["response_code"] != 0:
            logger.warn("MusicCast returned error code %d", response["response_code"])

        return response

    def _set_input(self):
        self._request_musiccast(f"/main/setInput?input={self.connected_input}")

    def _request_musiccast(self, endpoint, headers=None):
        response = self.session.get(
            self.base_url + endpoint, headers=headers, timeout=5
        )
        if response.status_code != 200:
            raise Exception(f"MusicCast returned {response.status_code}")

        return response.json()

    def get_volume(self) -> Volume:
        return self.volume

    def set_volume(self, volume: int) -> None:
        if volume != self.volume.current_volume:
            self.volume.current_volume = volume
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
