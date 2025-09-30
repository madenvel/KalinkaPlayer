**PROMPT START**

Create a project scaffold for a **Kalinka backend plugin** with the following features:

## Variables (ask me for these at generation time)

* `plugin_name` (human title, e.g., “Cool Recommender”)
* `package_name` (PEP-8 import name, e.g., `kalinka_cool_plugin`)
* `plugin_slug` (kebab for filenames, e.g., `kalinka-cool-plugin`)
* `author_name`
* `author_email`
* `version` (default `0.1.0`)
* `license` (default `MIT`)
* `requires_python` (default `>=3.10`)
* `sdk_version_range` (default `>=1.0,<2`)
* `kalinka_dep_version` (default `>=1.0`)
* `entry_point_group` (default `kalinka.plugins`)
* `venv_path` (default `/opt/kalinka/venv`)

## Directory layout

```
{{plugin_slug}}/
  pyproject.toml
  README.md
  LICENSE
  .gitignore
  src/
    {{package_name}}/
      __init__.py
      module_setup.py
      config_model.py
      manifest.toml        # optional: declares capabilities/permissions
      _version.py
  tests/
    test_smoke.py
  scripts/
    build_wheel.sh
    build_deb.sh
  debian/
    control
    changelog
    compat-or-not          # (use debhelper-compat via control)
    rules
    postinst
    prerm
    plugin.install         # file list mapping
```

## Content details

### 1) `pyproject.toml`

Use PEP 621 with setuptools.

* Project metadata with `name = "{{plugin_slug}}"`, `version = "{{version}}"`, `requires-python = "{{requires_python}}"`.
* Runtime deps: `kalinka-plugin-sdk {{sdk_version_range}}`.
* Optional test extras: `kalinka-plugin-dev {{sdk_version_range}}`, `pytest`.
* Entry point:

  ```toml
  [project.entry-points."{{entry_point_group}}"]
  {{package_name}} = "{{package_name}}.module_setup:setup"
  ```
* Build system:

  ```toml
  [build-system]
  requires = ["setuptools>=68", "wheel", "build"]
  build-backend = "setuptools.build_meta"
  ```
* Use:

  ```
  [tool.setuptools.packages.find]
  where = ["src"]
  ```

### 2) `src/{{package_name}}/__init__.py`

```python
from ._version import __version__
```

### 3) `src/{{package_name}}/_version.py`

```python
__version__ = "{{version}}"
```

### 4) `src/{{package_name}}/module_setup.py`

Provide the minimal plugin API:

```python
from typing import Any
from kalinka.sdk.api import PluginContext  # runtime Protocols
from .config_model import {{package_name|capitalize}}Config
# from kalinka.sdk.events import NowPlayingChanged  # example

REQUIRES_SDK = "{{sdk_version_range}}"
PLUGIN_ID = "{{package_name}}"
PLUGIN_TYPE "input_module" # or "device"

Config = {{package_name|capitalize}}Config

def setup(cfg: {{package_name|capitalize}}Config, ctx: "PluginContext") -> None:
    """
    Entry point used by Kalinka. Register subscriptions, timers, etc.
    This function must not block.
    """
    ctx.log.info("plugin_setup", plugin=PLUGIN_ID, version=ctx.sdk_version)

    # Example subscription (replace with your real topics)
    # async handlers should be registered via ctx.listener.stream(...) in your app’s wrapper
    def _on_event(msg: dict[str, Any]) -> None:
        # lightweight, non-blocking logic or enqueue work to your own thread/task
        pass

    # If your SDK exposes a callback adapter:
    # ctx.listener.subscribe("system.now_playing", _on_event)

    # Or if your SDK exposes an async stream helper, you may spawn a task from ctx if allowed.
```

### 5) `src/{{package_name}}/config_model.py`

Provide minimal module config model:

```python
from pydantic import Field
from kalinka.sdk.module_config import ModuleConfig


class DummyDeviceConfig(ModuleConfig):
    name: str = Field(default="{{package_name}}", title="{{plugin_name}}", frozen=True, exclude=True)
    enabled: bool = Field(default=False, title="Module Enabled")

```

### 6) `README.md`

Include:

* What it does, how to install via your CLI:

  ```
  sudo /opt/kalinka/bin/kalinka-plugin install {{plugin_slug}}=={{version}}
  ```
* Manual install from local wheel:

  ```
  sudo /opt/kalinka/bin/kalinka-plugin install dist/{{plugin_slug}}-{{version}}-py3-none-any.whl
  ```

### 7) `LICENSE`

Insert the chosen license.

### 8) `tests/test_smoke.py`

```python
from importlib.metadata import entry_points

def test_entry_point_visible():
    eps = entry_points(group="{{entry_point_group}}")
    # In editable mode, ensure it's discoverable after `pip install -e .`
    assert any(ep.name == "{{package_name}}" for ep in eps)
```

### 9) `scripts/build_wheel.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
python3 -m pip install --upgrade build
python3 -m build --wheel
ls -l dist/
```

Make executable.

### 10) `scripts/build_deb.sh`

Use `dpkg-deb` (no external fpm). Build a .deb that:

* installs the plugin wheel under `/usr/share/kalinka/plugins/`,
* installs maintainer scripts that pip-install the wheel into `{{venv_path}}`.

Script should:

* read version from `src/{{package_name}}/_version.py`,
* render `debian/` with correct fields,
* stage files under `pkgroot/`:

  ```
  pkgroot/usr/share/kalinka/plugins/{{plugin_slug}}-{{version}}.whl
  pkgroot/DEBIAN/{control,postinst,prerm}
  ```
* `dpkg-deb --build pkgroot {{plugin_slug}}_{{version}}_all.deb`

### 11) `debian/control`

Use debhelper-compat v13 via `Build-Depends`. Minimal fields:

```
Package: {{plugin_slug}}
Version: {{version}}
Section: python
Priority: optional
Architecture: all
Depends: kalinka ({{kalinka_dep_version}}), python3, ca-certificates
Maintainer: {{author_name}} <{{author_email}}>
Description: {{plugin_name}} – Kalinka plugin
 Installs the plugin wheel into Kalinka's venv and exposes entry points.
```

### 12) `debian/rules`

Simple passthrough (we’re manually building). Or omit and build via `scripts/build_deb.sh`.

### 13) `debian/postinst`

Install the wheel into Kalinka’s venv; be idempotent.

```bash
#!/bin/sh
set -e
VENVDIR="{{venv_path}}"
WHEEL="/usr/share/kalinka/plugins/{{plugin_slug}}-{{version}}-py3-none-any.whl"

if [ ! -x "$VENVDIR/bin/pip" ]; then
  echo "Kalinka venv not found at $VENVDIR" >&2
  exit 1
fi

"$VENVDIR/bin/pip" install --no-deps --upgrade "$WHEEL"

# Optionally: validate entry point visibility
"$VENVDIR/bin/python" - <<'PY'
from importlib.metadata import entry_points
eps = entry_points(group="{{entry_point_group}}")
print("Installed EPs:", [e.name for e in eps])
PY

# Restart Kalinka if service exists (comment out if you prefer manual)
if command -v systemctl >/dev/null 2>&1; then
  systemctl try-restart kalinka.service || true
fi

exit 0
```

Make executable.

### 14) `debian/prerm`

Uninstall on remove (optional; safe if plugin manager handles it).

```bash
#!/bin/sh
set -e
case "$1" in
  remove|deconfigure)
    VENVDIR="{{venv_path}}"
    PKG="{{plugin_slug}}"
    if [ -x "$VENVDIR/bin/pip" ]; then
      "$VENVDIR/bin/pip" uninstall -y "$PKG" || true
    fi
    ;;
esac
exit 0
```

### 15) `debian/changelog`

Minimal placeholder; or generate from git tags.

### 16) `debian/plugin.install`

(Optional) Path mapping doc; not strictly needed when using the custom script.

### 17) `.gitignore`

Typical Python + build ignores:

```
__pycache__/
*.pyc
dist/
build/
*.egg-info/
pkgroot/
*.deb
.venv/
```

## Developer instructions (include in README)

**Editable install for dev:**

```bash
pip install -e .
pytest -q
```

**Build wheel:**

```bash
./scripts/build_wheel.sh
```

**Build .deb:**

```bash
./scripts/build_deb.sh
```

**Install .deb (system):**

```bash
sudo dpkg -i {{plugin_slug}}_{{version}}_all.deb
```

## Requirements/assumptions

* Kalinka main app .deb creates venv at `{{venv_path}}` and runs service with that interpreter.
* Your plugin is pure-Python; if you add native code, extend pyproject build deps accordingly and ensure manylinux wheels if you plan PyPI distribution.

**Deliverables:** generate all files with correct variable substitution, executable bits on shell scripts, and ready to run the three core flows: `pip install -e .`, build wheel, build deb (which installs the wheel into Kalinka’s venv and restarts the service).

**PROMPT END**
