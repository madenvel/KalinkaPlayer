import logging
from typing import Optional

from kalinka_plugin_sdk import ModuleHealthState, ModuleState
from kalinka_plugin_sdk.plugin import InputModulePlugin, InputPluginContext
from kalinka_plugin_sdk.inputmodule import InputModule

from .config_model import JamendoConfig
from .jamendo import JamendoInputModule, get_client
from .mood_search import JamendoMoodIndex

logger = logging.getLogger(__name__.split(".")[-1])


class KalinkaPluginJamendo(InputModulePlugin):
    REQUIRES_SDK = ">=1,<2"
    PLUGIN_ID = "jamendo"
    CONFIG_MODEL = JamendoConfig

    def __init__(self):
        self.interface: Optional[InputModule] = None
        self._client = None
        # Captured at setup so get_state() can read the current config.
        self._context: Optional[InputPluginContext] = None

    def module_name(self) -> str:
        return "Jamendo Input Module"

    def get_interface(self) -> Optional[InputModule]:
        return self.interface

    async def setup(self, context: InputPluginContext) -> None:
        self._context = context
        config = JamendoConfig(**context.config.model_dump())
        logger.info("Setting up Jamendo input module")
        self._client = await get_client(config)
        mood_index = None
        if config.ai_search_enabled:
            # Cheap to construct; assets are fetched and the model loaded
            # lazily on the first ai_search.
            mood_index = JamendoMoodIndex(
                config.ai_index_path,
                config.ai_index_url or None,
                config.ai_model_dir,
                config.ai_model_url or None,
            )
        self.interface = JamendoInputModule(config, self._client, mood_index)

    async def get_state(self) -> ModuleState:
        """Surface the "no client_id configured" case as an ERROR.

        Without a client_id every Jamendo request returns empty, which is
        indistinguishable from "no results" in the UI. Reporting it here lets
        the modules page tell the user to set their key instead of silently
        showing an empty catalog.
        """
        client_id = ""
        if self._context is not None:
            client_id = getattr(self._context.config, "client_id", "") or ""

        if not client_id:
            return ModuleState(
                state=ModuleHealthState.ERROR,
                message=(
                    "No Jamendo client_id configured. Create a free application "
                    "at https://devportal.jamendo.com and paste its Client ID "
                    "into this module's settings."
                ),
            )
        return ModuleState(state=ModuleHealthState.READY)

    async def shutdown(self) -> None:
        logger.info("Shutting down Jamendo input module")
        if self._client is not None:
            await self._client.aclose()
