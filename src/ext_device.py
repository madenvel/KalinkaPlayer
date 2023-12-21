from abc import ABC, abstractmethod
from enum import Enum


class SupportedFunction(Enum):
    GET_VOLUME = "get_volume"
    SET_VOLUME = "set_volume"
    POWER_ON = "power_on"
    IS_POWER_ON = "is_power_on"
    POWER_OFF = "power_off"


class ExternalOutputDevice(ABC):
    @abstractmethod
    def get_volume(self) -> float:
        pass

    @abstractmethod
    def set_volume(self, volume: float) -> None:
        pass

    @abstractmethod
    def power_on(self) -> None:
        pass

    @abstractmethod
    def is_power_on(self) -> bool:
        pass

    @abstractmethod
    def power_off(self) -> None:
        pass

    @abstractmethod
    def supported_functions(self) -> list[SupportedFunction]:
        pass
