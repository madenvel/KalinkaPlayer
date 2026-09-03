"""What bootstrap does with the wheels it is handed.

The wheel directory is not the bundle's alone: a plugin installed from outside
it — kalinka-plugin-qobuz has its own repo and release train, and nothing on
the device upgrades it — drops a wheel here too. An SDK major then leaves that
wheel pinned to an SDK the bundle no longer ships, and resolving the directory
in one pip transaction turns one unusable plugin into a server that never
starts. That is a real failure, seen on 4.3.0.
"""

import os
import subprocess
from pathlib import Path

import pytest

BOOTSTRAP = (
    Path(__file__).resolve().parents[1] / "scripts" / "bootstrap.sh"
)

BUNDLE = [
    "kalinka_plugin_dummydevice-4.3.0-py3-none-any.whl",
    "kalinka_plugin_localfiles-4.3.0-py3-none-any.whl",
    "kalinka_plugin_sdk-2.0.0-py3-none-any.whl",
    "kalinka_server-4.3.0-py3-none-any.whl",
]
STRAY = "kalinka_plugin_qobuz-2.3.0-py3-none-any.whl"


@pytest.fixture
def install_dir(tmp_path):
    """A fake /opt/kalinka whose pip records every call it is given."""
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (tmp_path / "wheels").mkdir()

    pip = venv_bin / "pip"
    # Fails whenever asked for more than one wheel at once (pip's
    # ResolutionImpossible), and refuses the stray wheel on its own.
    pip.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$PIP_LOG"\n'
        "wheels=(); for a in \"$@\"; do case \"$a\" in *.whl) wheels+=(\"$a\");; esac; done\n"
        f'[ "${{#wheels[@]}}" -gt 1 ] && exit 1\n'
        f'case "${{wheels[0]:-}}" in *{STRAY}) exit 1;; esac\n'
        "exit 0\n"
    )
    pip.chmod(0o755)
    return tmp_path


def _run(install_dir, wheels, tmp_path):
    for name in wheels:
        (install_dir / "wheels" / name).write_text("")
    log = tmp_path / "pip.log"
    env = {
        **os.environ,
        "KALINKA_INSTALL_DIR": str(install_dir),
        "KALINKA_CACHE_DIR": str(tmp_path / "cache"),
        "KALINKA_STATE_DIR": str(tmp_path / "state"),
        "PIP_LOG": str(log),
    }
    result = subprocess.run(
        ["bash", str(BOOTSTRAP)], env=env, capture_output=True, text=True
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_a_stray_wheel_no_longer_costs_the_server_its_startup(
    install_dir, tmp_path
):
    result, calls = _run(install_dir, BUNDLE + [STRAY], tmp_path)

    assert result.returncode == 0, result.stderr
    # Every bundle wheel was still placed, one at a time.
    for name in BUNDLE:
        assert any(name in call and call.count(".whl") == 1 for call in calls)
    assert f"skipped {STRAY}" in result.stderr


def test_the_sdk_is_installed_before_what_depends_on_it(install_dir, tmp_path):
    """No index carries the SDK, so a plugin placed ahead of it has nowhere to
    resolve it from and would be skipped along with the stray one."""
    _result, calls = _run(install_dir, BUNDLE + [STRAY], tmp_path)

    singles = [c for c in calls if c.count(".whl") == 1]
    sdk = next(i for i, c in enumerate(singles) if "kalinka_plugin_sdk-" in c)
    assert sdk == 0, singles


def test_a_resolvable_set_is_installed_in_one_go(install_dir, tmp_path):
    """The fallback is for the broken case only — one wheel, one call."""
    result, calls = _run(install_dir, ["kalinka_server-4.3.0-py3-none-any.whl"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert "installing them singly" not in result.stderr
