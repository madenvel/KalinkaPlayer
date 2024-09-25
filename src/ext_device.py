from abc import ABC, abstractmethod
from enum import Enum
from pydantic import BaseModel


class SupportedFunction(Enum):
    GET_VOLUME = "get_volume"
    SET_VOLUME = "set_volume"
    POWER_ON = "power_on"
    IS_POWER_ON = "is_power_on"
    POWER_OFF = "power_off"


class Volume(BaseModel):
    max_volume: int
    current_volume: int
    replay_gain: float = 0.0


class ExternalOutputDevice(ABC):
    @abstractmethod
    def get_volume(self) -> Volume:
        pass

    @abstractmethod
    def set_volume(self, volume: int) -> None:
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
