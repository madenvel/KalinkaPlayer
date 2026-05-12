"""Tests for the bootstrap-side install_pending helper.

The helper has a subtle non-local invariant: the canonical
``pending_installs.json`` must be moved aside *before* pip runs so a
long source-build install can be interrupted (systemd timeout, power
loss, apt upgrade) without re-attempting the same install on the next
boot. These tests pin that contract down.
"""

from __future__ import annotations

import json
import time
import types
from pathlib import Path

import pytest

from kalinka_server import install_pending


def _seed_manifest(manifests_dir: Path) -> None:
    manifests_dir.mkdir(parents=True, exist_ok=True)
    (manifests_dir / "lf.json").write_text(
        json.dumps(
            {
                "plugin": "lf",
                "schema": 1,
                "packages": {
                    "numpy": {
                        "pip_spec": "numpy==1.26.4",
                        "description": "x",
                    },
                    "tokenizers": {
                        "pip_spec": "tokenizers==0.22.2",
                        "description": "x",
                    },
                },
            }
        )
    )


def _write_pending(pending_path: Path, keys: list[str]) -> None:
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(
        json.dumps(
            {"schema": 1, "requested": keys, "requested_at": time.time()}
        )
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """Prepare manifest + pending file; stub pip to record invocations.

    Yields a dict with paths and a list that records pip invocations.
    """
    manifests = tmp_path / "manifests"
    _seed_manifest(manifests)
    pending = tmp_path / "pending.json"
    last_install = tmp_path / "last.json"

    invocations: list[tuple[str, bool]] = []

    def fake_pip(spec: str):
        # Capture whether the *canonical* pending file is still on disk
        # at the moment pip runs. The contract is that it should already
        # have been reserved (moved aside) before this point.
        invocations.append((spec, pending.exists()))
        r = types.SimpleNamespace()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    monkeypatch.setattr(install_pending, "_pip_install", fake_pip)

    yield {
        "tmp": tmp_path,
        "manifests": manifests,
        "pending": pending,
        "last_install": last_install,
        "invocations": invocations,
    }


def _run(env: dict) -> int:
    return install_pending.main(
        [
            "install_pending",
            str(env["pending"]),
            str(env["manifests"]),
            str(env["last_install"]),
        ]
    )


def test_pending_file_is_reserved_before_pip_runs(env):
    """Contract: by the time pip is invoked, the canonical pending file
    is already gone — so a kill mid-pip won't trigger a retry on the
    next boot."""
    _write_pending(env["pending"], ["numpy"])
    rc = _run(env)
    assert rc == 0
    assert len(env["invocations"]) == 1
    pip_spec, pending_visible_during_pip = env["invocations"][0]
    assert pip_spec == "numpy==1.26.4"
    assert pending_visible_during_pip is False, (
        "pending_installs.json should be moved aside before pip runs"
    )
    assert not env["pending"].exists(), "pending file should be gone at end"


def test_success_renames_to_done(env):
    _write_pending(env["pending"], ["numpy"])
    _run(env)
    archives = sorted(env["tmp"].glob("pending.done.*"))
    assert len(archives) == 1, f"expected exactly one .done archive, got {archives}"
    assert env["last_install"].exists()


def test_failure_renames_to_failed(env, monkeypatch):
    def failing_pip(spec):
        r = types.SimpleNamespace()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "ERROR: simulated failure"
        return r

    monkeypatch.setattr(install_pending, "_pip_install", failing_pip)
    _write_pending(env["pending"], ["numpy"])
    rc = _run(env)
    # Helper exits 0 even on pip failure (audit recorded, no retry).
    assert rc == 0
    assert sorted(env["tmp"].glob("pending.failed.*"))
    audit = json.loads(env["last_install"].read_text())
    assert audit["results"][0]["status"] == "failed"


def test_killed_mid_install_does_not_retry_on_next_boot(env, monkeypatch):
    """Simulate the case the user just hit: install_pending starts, pip
    runs, the process is killed (or the host loses power) before audit
    is written. The next boot must find no pending file and must NOT
    re-attempt the install."""

    # First run: pip "starts" but we simulate a kill by raising before
    # the audit is written. The pending file should already be moved
    # aside at that point.
    class _Killed(SystemExit):
        pass

    def killing_pip(spec):
        # Pending must be gone before we "die".
        assert not env["pending"].exists()
        raise _Killed("simulated kill")

    monkeypatch.setattr(install_pending, "_pip_install", killing_pip)
    _write_pending(env["pending"], ["numpy"])
    with pytest.raises(_Killed):
        _run(env)

    # Pending file is gone; an .in_progress.<ts> archive is left.
    assert not env["pending"].exists()
    assert sorted(env["tmp"].glob("pending.in_progress.*"))

    # Second run: bootstrap re-invokes install_pending. With no canonical
    # pending file, it must be a no-op — no pip call recorded.
    invocations_before = len(env["invocations"])
    monkeypatch.setattr(
        install_pending,
        "_pip_install",
        lambda spec: env["invocations"].append((spec, True)) or types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    rc = _run(env)
    assert rc == 0
    assert len(env["invocations"]) == invocations_before, (
        "no pip invocation should happen on the second boot — the previous "
        "attempt already claimed the pending file"
    )


def test_no_pending_file_is_a_noop(env):
    """Helper must not error when there's nothing to install."""
    rc = _run(env)
    assert rc == 0
    assert env["invocations"] == []
    assert not env["last_install"].exists()


def test_malformed_pending_is_moved_aside(env):
    """A pending file without a `requested` list is moved aside so the
    same parse error doesn't loop."""
    env["pending"].write_text(json.dumps({"schema": 1, "not_requested": 42}))
    rc = install_pending.main(
        [
            "install_pending",
            str(env["pending"]),
            str(env["manifests"]),
            str(env["last_install"]),
        ]
    )
    assert rc == 1
    assert not env["pending"].exists()
    assert sorted(env["tmp"].glob("pending.malformed.*"))
