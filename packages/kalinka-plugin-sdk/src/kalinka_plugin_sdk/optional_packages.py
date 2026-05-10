"""Declarations for optional pip packages a plugin may install on demand.

A plugin declares a static allow-list as a class attribute:

    class MyPlugin(InputModulePlugin):
        OPTIONAL_PACKAGES: ClassVar[dict[str, OptionalPackageSpec]] = {
            "numpy": OptionalPackageSpec(
                pip_spec="numpy==1.26.4",
                description="Used by AI search",
                triggered_by=("input_modules.mine.searcher.enabled",),
            ),
        }

At deb build time the plugin's allow-list is exported to a JSON manifest
shipped under /opt/kalinka/allowed_packages/<plugin_id>.json (root-owned).

At runtime the server exposes the catalog and accepts install requests
keyed by allow-list key (never by pip spec). Requested keys are validated
against the in-memory registry and written to a pending-installs file;
bootstrap re-validates against the manifest before invoking pip.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OptionalPackageSpec(BaseModel):
    """Static declaration of an installable pip package.

    Pinned versions (or version ranges) live here, not in the install
    request — what gets installed is decided by the deb-shipped manifest,
    not by the API caller.
    """

    model_config = ConfigDict(frozen=True)

    pip_spec: str = Field(
        ...,
        description="Pip requirement specifier, e.g. 'numpy==1.26.4'.",
    )
    description: str = Field(
        ...,
        description="Human-readable description shown alongside the package "
        "in the settings UI.",
    )
    import_name: str = Field(
        default="",
        description="Python import name used to probe whether the package is "
        "already installed. Defaults to the registry key when empty. "
        "Set explicitly when the pip distribution name differs from the "
        "import name (e.g. 'essentia-tensorflow' -> 'essentia').",
    )
    triggered_by: tuple[str, ...] = Field(
        default=(),
        description="Dotted config paths whose enablement makes this package "
        "required. Informational; the server uses this to surface "
        "'package needed but not installed' hints. Not currently used "
        "to auto-queue installs.",
    )
