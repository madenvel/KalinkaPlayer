import asyncio
import importlib.util
import logging
import logging.handlers
import multiprocessing
import os
from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional

from kalinka_plugin_sdk import (
    DynamicFieldDecl,
    ModuleHealthState,
    ModuleState,
    OptionalPackageSpec,
)
from kalinka_plugin_sdk.plugin import InputPluginContext, InputModulePlugin
from kalinka_plugin_sdk.inputmodule import InputModule

from .config_model import LocalFilesConfig
from .db_schema import init_db
from .input_module_db import LocalFilesInputModuleDb
from .localfiles import LocalFilesInputModule
from .optional_packages import OPTIONAL_PACKAGES
from . import librarian
from . import embedder
from . import searcher


logger = logging.getLogger(__name__.split(".")[-1])


@dataclass
class _SubfeatureBookkeeping:
    """Internal-to-the-plugin record of one sub-feature's runtime state.

    Not exposed on any API; rolled up into ModuleState by get_state().
    """

    title: str
    required: bool
    state: ModuleHealthState = ModuleHealthState.READY
    message: str = ""
    # Optional-package keys this sub-feature would have needed but couldn't
    # find — surfaced in the status message and used to suggest installs.
    missing_packages: tuple[str, ...] = field(default_factory=tuple)


def _is_importable(import_name: str) -> bool:
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ImportError, ValueError):
        return False


def _format_subfeature_status(sf: "_SubfeatureBookkeeping") -> str:
    """Render a sub-feature's state as the markdown the UI displays."""
    if sf.state == ModuleHealthState.READY:
        return f"**Ready** — {sf.title.lower()} is running normally."
    if sf.state == ModuleHealthState.DISABLED:
        msg = sf.message or "Disabled in configuration."
        return f"**Disabled** — {msg}"
    if sf.state == ModuleHealthState.WARNING:
        return f"**Warning** — {sf.message or 'Degraded operation.'}"
    if sf.state == ModuleHealthState.ERROR:
        return f"**Not available** — {sf.message or 'Unknown error.'}"
    return sf.message or ""


class KalinkaPluginLocalFiles(InputModulePlugin):
    REQUIRES_SDK = ">=2,<3"
    PLUGIN_ID = "localfiles"
    CONFIG_MODEL = LocalFilesConfig
    OPTIONAL_PACKAGES: ClassVar[dict[str, OptionalPackageSpec]] = OPTIONAL_PACKAGES

    # Dynamic-field declarations consumed by the server's schema builder.
    # Paths here are the keys passed to resolve_dynamic_field(). Section
    # ids are relative to the module — the server prepends the module's
    # full path prefix when locating the target section in the schema.
    DYNAMIC_FIELDS: ClassVar[dict[str, DynamicFieldDecl]] = {
        "enricher.status_view": DynamicFieldDecl(
            section_id="enricher",
            label="Status",
            widget="rich_text",
            value_type="str",
        ),
        "ai_search.status_view": DynamicFieldDecl(
            section_id="ai_search",
            label="Status",
            widget="rich_text",
            value_type="str",
        ),
    }

    def __init__(self):
        # The indexer and enricher share one "librarian" process (single DB
        # writer); the searcher and embedder stay separate.
        self._librarian_proc = None
        self._searcher_proc = None
        self._embedder_proc = None
        self._searcher_nudge_queue = multiprocessing.Queue()
        self._embedder_nudge_queue = multiprocessing.Queue()
        self._logging_queue = multiprocessing.Queue()
        self._search_request_queue = multiprocessing.Queue()
        self._search_response_queue = multiprocessing.Queue()
        self._text_encode_request_queue = multiprocessing.Queue()
        self._text_encode_response_queue = multiprocessing.Queue()
        self._log_listener = None
        self._inputmodule = None

        # Context captured at setup() so methods called by the server
        # later (get_state, required_packages, resolve_dynamic_field)
        # can read the *current* in-memory config — which the server
        # mutates via PUT /server/config without re-invoking setup.
        self._context: Optional[InputPluginContext] = None

        # Per-sub-feature state used by get_state() and resolve_dynamic_field().
        # Populated during setup() based on actual subprocess start outcomes
        # and import probing for optional packages.
        self._subfeatures: dict[str, _SubfeatureBookkeeping] = {
            "indexer": _SubfeatureBookkeeping(
                # Not required — direct file playback works against the
                # existing DB even if the indexer is down. A dead indexer
                # surfaces as a module-level WARNING, not an ERROR.
                title="File indexer",
                required=False,
            ),
            "enricher": _SubfeatureBookkeeping(
                title="Metadata enricher", required=False
            ),
            "ai_search": _SubfeatureBookkeeping(
                title="AI search", required=False
            ),
        }

    def get_interface(self) -> Optional[InputModule]:
        return self._inputmodule

    async def setup(self, context: InputPluginContext) -> None:
        self._context = context
        config = LocalFilesConfig(**context.config.model_dump())
        logger.info("Setting up localfiles input module")

        input_module_db = LocalFilesInputModuleDb(config)

        # "Rebuild library on next restart" is a one-shot trigger: the server
        # has already reset it (persist-first) before this boot, leaving the
        # armed value here for us to act on exactly once. Purge before any
        # worker opens the DB so the indexer rebuilds it from scratch.
        if config.rebuild_library:
            logger.warning(
                "Rebuild requested — purging DB and artwork before scan."
            )
            input_module_db.purge_all()

        # The queues are the module's "is there a searcher?" test, so hand
        # them over only when one will actually run: a request nobody reads
        # blocks ai_search() for its full 30 s timeout.
        search_queues = (
            (self._search_request_queue, self._search_response_queue)
            if config.ai_search.enabled
            else (None, None)
        )

        # The LocalFilesInputModule will use its own specialized DB
        self._inputmodule = LocalFilesInputModule(
            config,
            input_module_db,
            *search_queues,
        )

        # Forward subprocess log records into the main logging pipeline so the
        # main kalinka-server handlers (and their levels/formatters) decide what
        # to emit. This avoids a parallel StreamHandler that would bypass the
        # server's log level filtering.
        class SubprocessForwardingHandler(logging.Handler):
            def emit(self, record):
                target = logging.getLogger(record.name)
                if target.isEnabledFor(record.levelno):
                    target.handle(record)

        handler = SubprocessForwardingHandler()

        # In Python 3.14, respect_handler_level defaults to True; we keep it
        # False for backward compatibility and to let the target logger handle
        # level filtering.
        self._log_listener = logging.handlers.QueueListener(
            self._logging_queue, handler, respect_handler_level=False
        )
        self._log_listener.start()

        # Centralised schema init — runs once in the main process before
        # any subprocess starts, so there is no lock contention.
        await init_db(config.db_path)

        # Indexer + enricher run in one process, wired by an in-process queue.
        # It nudges the searcher (not the embedder directly) when enrichment
        # completes; the enricher leg only runs if config.enricher.enabled.
        self._librarian_proc = multiprocessing.Process(
            target=librarian.main,
            args=(
                config,
                self._logging_queue,
                self._searcher_nudge_queue,
            ),
        )
        self._librarian_proc.start()

        # AI search is served by two processes: the searcher answers queries,
        # the embedder indexes the library and encodes query text for the
        # searcher over IPC, so only one process loads the ~600 MB CLAP model.
        # Neither is useful alone, so one config flag starts both.
        if config.ai_search.enabled:
            self._searcher_proc = multiprocessing.Process(
                target=searcher.main,
                args=(
                    config,
                    self._logging_queue,
                    self._search_request_queue,
                    self._search_response_queue,
                    self._searcher_nudge_queue,
                    self._embedder_nudge_queue,
                    self._text_encode_request_queue,
                    self._text_encode_response_queue,
                ),
            )
            self._searcher_proc.start()

            self._embedder_proc = multiprocessing.Process(
                target=embedder.main,
                args=(
                    config,
                    self._logging_queue,
                    self._embedder_nudge_queue,
                    self._text_encode_request_queue,
                    self._text_encode_response_queue,
                ),
            )
            self._embedder_proc.start()

        # Populate sub-feature state from what was (or wasn't) just started
        # and from import probing. This runs in the main process — it
        # doesn't catch later subprocess deaths, but covers the common
        # "package was never installed" failure path.
        await self._evaluate_subfeatures(config)

    # ------------------------------------------------------------------
    # Sub-feature bookkeeping (internal — never exposed directly)
    # ------------------------------------------------------------------

    async def _evaluate_subfeatures(self, config: LocalFilesConfig) -> None:
        """Decide initial state for each sub-feature based on config + imports.

        Best-effort: `is_alive()` immediately after `Process.start()` is
        racy on both edges (kernel may not have scheduled yet; or child
        may have already crashed during import). We yield briefly so the
        OS has a chance to schedule the children — a child that fails at
        import nearly always exits within the grace window, and a child
        that successfully imports settles into its main loop. Persistent
        crashes after this window are *not* caught here.
        """
        await asyncio.sleep(0.2)

        # Indexer: always starts (as the librarian process). Mark ERROR only
        # if that process failed to start.
        librarian_alive = (
            self._librarian_proc is not None and self._librarian_proc.is_alive()
        )
        idx = self._subfeatures["indexer"]
        if librarian_alive:
            idx.state = ModuleHealthState.READY
            idx.message = ""
        else:
            idx.state = ModuleHealthState.ERROR
            idx.message = "Librarian subprocess failed to start."

        # Music folders: a missing or unreadable folder doesn't crash the
        # indexer — it just finds nothing — which makes it the most silent
        # misconfiguration we have. Surface it as a WARNING naming the
        # offending paths. The check runs inside the service sandbox, so
        # it also catches paths hidden by systemd hardening.
        if idx.state == ModuleHealthState.READY:
            bad_folders = []
            for folder in config.music_folders:
                expanded = os.path.expanduser(folder)
                if not os.path.isdir(expanded) or not os.access(
                    expanded, os.R_OK | os.X_OK
                ):
                    bad_folders.append(folder)
            if bad_folders:
                idx.state = ModuleHealthState.WARNING
                idx.message = (
                    "Music folder(s) not accessible to the service: "
                    f"{', '.join(bad_folders)}. Check that the path exists "
                    "and is readable by the 'kalusr' user."
                )

        # Enricher: DISABLED if config.enricher.enabled is False; else READY
        # (hard dependencies are guaranteed by the plugin's deb). The one
        # optional leg is generated album art, which needs numpy — a
        # missing numpy there is a WARNING (art generation off), not an
        # ERROR: metadata enrichment itself is unaffected.
        enr = self._subfeatures["enricher"]
        if not config.enricher.enabled:
            enr.state = ModuleHealthState.DISABLED
            enr.message = "Disabled in configuration."
        elif not librarian_alive:
            enr.state = ModuleHealthState.ERROR
            enr.message = "Librarian subprocess failed to start."
        elif config.enricher.plugins.procedural_artwork.enabled and not _is_importable(
            "numpy"
        ):
            enr.state = ModuleHealthState.WARNING
            enr.message = (
                "Generated album art unavailable. Missing package: `numpy`. "
                "Use **Restart with install** in the modules page to fetch it."
            )
            enr.missing_packages = ("numpy",)
        else:
            enr.state = ModuleHealthState.READY
            enr.message = ""

        # AI search: the query and indexing halves report as one feature.
        # CLAP needs numpy + onnxruntime + soundfile + soxr + tokenizers
        # (soundfile/soxr replaced librosa).
        ai = self._subfeatures["ai_search"]
        if not config.ai_search.enabled:
            ai.state = ModuleHealthState.DISABLED
            ai.message = "Disabled in configuration."
        else:
            missing = [
                pkg
                for pkg, import_name in (
                    ("numpy", "numpy"),
                    ("onnxruntime", "onnxruntime"),
                    ("soundfile", "soundfile"),
                    ("soxr", "soxr"),
                    ("tokenizers", "tokenizers"),
                )
                if not _is_importable(import_name)
            ]
            dead = [
                name
                for name, proc in (
                    ("Searcher", self._searcher_proc),
                    ("Embedder", self._embedder_proc),
                )
                if proc is None or not proc.is_alive()
            ]
            if missing:
                ai.state = ModuleHealthState.ERROR
                ai.message = (
                    "AI search unavailable. Missing package(s): "
                    + ", ".join(f"`{m}`" for m in missing)
                    + ". Use **Restart with install** in the modules page to "
                    "fetch them."
                )
                ai.missing_packages = tuple(missing)
            elif dead:
                ai.state = ModuleHealthState.ERROR
                ai.message = f"Subprocess failed to start: {', '.join(dead)}."
            else:
                ai.state = ModuleHealthState.READY
                ai.message = ""

    # ------------------------------------------------------------------
    # SDK overrides
    # ------------------------------------------------------------------

    async def get_state(self) -> ModuleState:
        """Roll sub-feature states up into a single ModuleState."""
        # The plugin overall is ERROR if any *required* sub-feature is ERROR.
        # Otherwise WARNING if any non-required sub-feature is ERROR or any
        # sub-feature reports a WARNING (something the user might want to
        # fix). Otherwise READY.
        any_required_error = False
        any_optional_error = False
        broken_titles: list[str] = []
        degraded_titles: list[str] = []
        missing_packages: list[str] = []
        seen_packages: set[str] = set()
        for sf in self._subfeatures.values():
            if sf.state == ModuleHealthState.ERROR:
                if sf.required:
                    any_required_error = True
                    broken_titles.append(sf.title)
                else:
                    any_optional_error = True
                    broken_titles.append(sf.title)
            elif sf.state == ModuleHealthState.WARNING:
                degraded_titles.append(sf.title)
            # Aggregate missing-package keys across all sub-features
            # (regardless of state — a DISABLED sub-feature still tells
            # us what the user would need if they re-enable it).
            for pkg in sf.missing_packages:
                if pkg not in seen_packages:
                    seen_packages.add(pkg)
                    missing_packages.append(pkg)

        if any_required_error:
            state = ModuleHealthState.ERROR
        elif any_optional_error or degraded_titles:
            state = ModuleHealthState.WARNING
        else:
            state = ModuleHealthState.READY

        if state == ModuleHealthState.READY:
            return ModuleState(
                state=state, message=None, missing_packages=missing_packages
            )

        # One-line summary; the per-sub-feature detail lives in the
        # dynamic status_view fields on the relevant settings sections.
        parts = []
        if broken_titles:
            parts.append(f"{', '.join(broken_titles)} unavailable")
        if degraded_titles:
            parts.append(f"{', '.join(degraded_titles)} degraded")
        summary = (
            f"{'; '.join(parts)} — "
            "open the corresponding section below for details."
        )
        return ModuleState(
            state=state, message=summary, missing_packages=missing_packages
        )

    async def resolve_dynamic_field(self, path: str) -> Any:
        """Return rich-text status for each declared dynamic field."""
        # Strip the "<subfeature>." prefix from a "<subfeature>.status_view" path.
        if path.endswith(".status_view"):
            sf_id = path[: -len(".status_view")]
            sf = self._subfeatures.get(sf_id)
            if sf is None:
                raise KeyError(path)
            return _format_subfeature_status(sf)
        raise KeyError(path)

    async def required_packages(self) -> list[str]:
        """Optional-package keys this plugin would need given the *current*
        in-memory config.

        Reads context.config (which reflects any staged PUT /server/config
        changes), not the config captured at setup. The server calls this
        when handling /server/restart so an install is auto-queued for
        sub-features the user just enabled but whose deps aren't yet
        importable.
        """
        if self._context is None:
            return []
        cfg = self._context.config

        missing: list[str] = []
        seen: set[str] = set()

        def need(key: str, import_name: str) -> None:
            if key in seen:
                return
            if not _is_importable(import_name):
                missing.append(key)
                seen.add(key)

        # Enricher: numpy for the generated-album-art leg.
        if cfg.enricher.enabled and cfg.enricher.plugins.procedural_artwork.enabled:
            need("numpy", "numpy")

        # AI search: numpy for mood ranking, plus the CLAP-side deps
        # (soundfile + soxr handle audio decode/resample, replacing librosa).
        if cfg.ai_search.enabled:
            need("numpy", "numpy")
            need("onnxruntime", "onnxruntime")
            need("soundfile", "soundfile")
            need("soxr", "soxr")
            need("tokenizers", "tokenizers")

        return missing

    def _shutdown_process(self, proc):
        """Shutdown a process by sending a termination signal"""

        # A sub-feature that was never started has no process — routine
        # since AI search is off by default, so not worth a warning.
        if proc is None:
            return
        if not proc.is_alive():
            logger.warning("Process already shut down.")
            return

        # Send shutdown command over the process's socket
        try:
            sleeping_time = 5
            proc.terminate()
            proc.join(timeout=sleeping_time)
            if proc.is_alive():
                logger.warning(
                    f"Process {proc.pid} did not shut down gracefully, killing it."
                )
                proc.kill()
                proc.join(timeout=sleeping_time)
            else:
                logger.info(f"Process {proc.pid} shut down successfully.")
        except Exception as e:
            logger.error(f"Error shutting down process {proc.pid}: {e}")

    async def shutdown(self) -> None:
        logger.info("Shutting down localfiles input module")

        self._shutdown_process(self._librarian_proc)
        self._shutdown_process(self._searcher_proc)
        self._shutdown_process(self._embedder_proc)

        if self._log_listener is not None:
            self._log_listener.stop()

        # Ensure multiprocessing queues release their semaphores
        for q in (
            self._searcher_nudge_queue,
            self._embedder_nudge_queue,
            self._logging_queue,
            self._search_request_queue,
            self._search_response_queue,
            self._text_encode_request_queue,
            self._text_encode_response_queue,
        ):
            if q is not None:
                q.close()
                q.join_thread()
