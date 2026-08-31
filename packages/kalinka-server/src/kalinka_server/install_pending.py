"""Privileged install helper invoked by bootstrap.sh.

Reads a pending-installs request file written by the kalusr-side server,
intersects it with deb-shipped manifests (the static allow-list), and
calls `pip install` for each allowed key. Writes a per-run audit record
to last_install.json.

Runs as root from /opt/kalinka/venv/bin/python (the same venv the server
runs in). Uses stdlib only to keep the bootstrap path minimal.

Usage:
    python -m kalinka_server.install_pending \\
        <pending.json> <manifests_dir> <last_install.json>

Exit codes:
    0 — finished (even if some installs failed; partial is fine)
    1 — bad arguments, unreadable inputs, or write of audit log failed

The helper never blocks the system: if pip itself fails, the failure is
logged to last_install.json and the helper exits 0 so kalinka.service
still starts. The server can read last_install.json to surface results.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .logging_setup import make_handler


logger = logging.getLogger("install_pending")
logging.basicConfig(level=logging.INFO, handlers=[make_handler()])


def _load_manifests(manifests_dir: Path) -> dict[str, dict]:
    """Build {key: spec_dict} registry from every <plugin>.json in the dir."""
    registry: dict[str, dict] = {}
    if not manifests_dir.is_dir():
        logger.info("No manifests directory at %s; nothing to install", manifests_dir)
        return registry

    for manifest_path in sorted(manifests_dir.glob("*.json")):
        try:
            data = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Skipping malformed manifest %s: %s", manifest_path, e)
            continue
        packages = data.get("packages") or {}
        if not isinstance(packages, dict):
            logger.warning("Manifest %s has no 'packages' object", manifest_path)
            continue
        for key, spec in packages.items():
            if not isinstance(spec, dict) or "pip_spec" not in spec:
                logger.warning(
                    "Manifest %s key %r missing pip_spec; skipping",
                    manifest_path,
                    key,
                )
                continue
            if key in registry and registry[key]["pip_spec"] != spec["pip_spec"]:
                logger.warning(
                    "Key %r declared by multiple plugins with different pip_spec "
                    "(%r vs %r); using first seen",
                    key,
                    registry[key]["pip_spec"],
                    spec["pip_spec"],
                )
                continue
            registry.setdefault(key, spec)
    return registry


def _pip_install(pip_spec: str) -> subprocess.CompletedProcess:
    """Invoke pip install for a single package spec.

    Transitive deps are accepted by design — pinning every transitive of
    e.g. essentia-tensorflow is impractical, and the static allow-list +
    version pin on the top-level spec is the security boundary we care
    about.
    """
    cmd = [sys.executable, "-m", "pip", "install", "--no-input", pip_spec]
    logger.info("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        sys.stderr.write(
            "usage: install_pending <pending.json> <manifests_dir> "
            "<last_install.json>\n"
        )
        return 1

    pending_path = Path(argv[1])
    manifests_dir = Path(argv[2])
    last_install_path = Path(argv[3])

    if not pending_path.is_file():
        logger.info("No pending file at %s; nothing to do", pending_path)
        return 0

    try:
        pending = json.loads(pending_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Cannot read pending file %s: %s", pending_path, e)
        return 1

    requested = pending.get("requested")
    if not isinstance(requested, list):
        logger.error("Pending file missing 'requested' list; got %r", pending)
        # Move it aside so we don't keep hitting the same error.
        try:
            pending_path.rename(
                pending_path.with_suffix(f".malformed.{int(time.time())}")
            )
        except OSError:
            pass
        return 1

    # Move the pending file aside *before* we touch pip. A long source-
    # build install can outlast systemd's TimeoutStartSec, an apt deb
    # upgrade, or a hand-pulled power plug — and on any of those the
    # next bootstrap would otherwise re-find the same pending file and
    # restart the install from scratch, infinite loop. The
    # .in_progress.<ts> name preserves an audit trail of what was
    # attempted; the canonical pending_installs.json is gone, so the
    # next boot finds nothing to do. Retry path is via re-toggling the
    # subfeature in the UI — that writes a fresh pending file.
    started_at = time.time()
    in_progress_path = pending_path.with_suffix(
        f".in_progress.{int(started_at)}"
    )
    try:
        pending_path.rename(in_progress_path)
    except OSError as e:
        logger.error(
            "Cannot reserve pending file %s as %s: %s",
            pending_path,
            in_progress_path,
            e,
        )
        return 1
    logger.info("Reserved pending request as %s", in_progress_path)

    registry = _load_manifests(manifests_dir)
    logger.info(
        "Allow-list keys available: %s",
        sorted(registry.keys()) or "(none)",
    )
    results: list[dict] = []
    for raw_key in requested:
        key = str(raw_key)
        if key not in registry:
            logger.warning("Refusing %r: not in allow-list", key)
            results.append({"key": key, "status": "rejected_unknown_key"})
            continue
        spec = registry[key]
        pip_spec = spec["pip_spec"]
        completed = _pip_install(pip_spec)
        entry: dict = {
            "key": key,
            "pip_spec": pip_spec,
            "returncode": completed.returncode,
        }
        if completed.returncode == 0:
            entry["status"] = "ok"
            logger.info("Installed %s (%s)", key, pip_spec)
        else:
            entry["status"] = "failed"
            entry["stderr"] = (completed.stderr or "").strip()[-4000:]
            logger.error(
                "pip install %s failed (rc=%d): %s",
                pip_spec,
                completed.returncode,
                entry["stderr"],
            )
        results.append(entry)

    audit = {
        "schema": 1,
        "started_at": started_at,
        "finished_at": time.time(),
        "requested": list(requested),
        "results": results,
    }

    try:
        last_install_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = last_install_path.with_suffix(last_install_path.suffix + ".tmp")
        tmp.write_text(json.dumps(audit, indent=2))
        os.replace(tmp, last_install_path)
    except OSError as e:
        logger.error("Failed to write audit log %s: %s", last_install_path, e)
        # Still try to move the pending file out of the way below.

    # Rename the in-progress audit trail to .done or .failed depending
    # on the outcome. If this rename fails the file simply stays as
    # .in_progress.<ts> — still a valid record, just less informative.
    any_failed = any(r.get("status") == "failed" for r in results)
    any_rejected = any(
        r.get("status") == "rejected_unknown_key" for r in results
    )
    suffix = ".failed" if (any_failed or any_rejected) else ".done"
    archive = pending_path.with_suffix(f"{suffix}.{int(started_at)}")
    try:
        shutil.move(str(in_progress_path), str(archive))
    except OSError as e:
        logger.error(
            "Failed to rename %s to %s: %s",
            in_progress_path,
            archive,
            e,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
