import requests
import logging
from src.events import EventType

from src.ext_device import SupportedFunction
from src.playqueue import PlayQueue
from src.rpiasync import EventListener
import threading

logger = logging.getLogger(__name__)


class Device:
    def __init__(self):
        self.connected_input = "optical2"
        device_addr = "192.168.3.33"
        device_port = 80
        self.session = requests.Session()
        self.base_url = f"http://{device_addr}:{device_port}/YamahaExtendedControl/v1"
        status = self._get_status()
        self.volume_limits = (0, status["max_volume"])
        self.timer = None

    def _on_paused_or_stopped(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

        self.timer = threading.Timer(60.0, self._self_power_off)
        self.timer.start()

    def _self_power_off(self):
        status = self._get_status()
        if status["input"] == self.connected_input:
            self.power_off()

    def _on_playing(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

        self.power_on()

    def _get_status(self):
        response = self._request_musiccast("/main/getStatus")
        if response["response_code"] != 0:
            logger.warn("MusicCast returned error code %d", response["response_code"])

        return response

    def _set_input(self):
        self._request_musiccast(f"/main/setInput?input={self.connected_input}")

    def _request_musiccast(self, endpoint):
        response = self.session.get(self.base_url + endpoint)
        if response.status_code != 200:
            raise Exception(f"MusicCast returned {response.status_code}")

        return response.json()

    def get_volume(self) -> float:
        status = self._get_status()
        if "volume" not in status:
            return 0

        return (status["volume"] - self.volume_limits[0]) / self.volume_limits[1]

    def set_volume(self, volume: float) -> None:
        response = self._request_musiccast(
            f"/main/setVolume?volume={int(self.volume_limits[0] + self.volume_limits[1] * volume)}"
        )
        logger.info(response)

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
