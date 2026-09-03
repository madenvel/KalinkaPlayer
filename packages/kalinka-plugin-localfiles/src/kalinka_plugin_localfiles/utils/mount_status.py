"""Mount awareness for the configured music folders.

A music folder is often a mounted network share (NFS/CIFS, possibly managed
by autofs), and an unmounted share is indistinguishable from a deleted
library by plain ``os.path`` checks. This module reads
``/proc/self/mountinfo`` to tell the two apart, and offers probes that give
a pending automount a bounded chance to complete — the stat inside a probe
is itself what triggers an autofs mount.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Iterable, Optional

_NETWORK_FS = {
    "cifs",
    "smb3",
    "smbfs",
    "sshfs",
    "fuse.sshfs",
    "davfs",
    "fuse.davfs2",
    "9p",
    "ceph",
    "fuse.ceph",
    "glusterfs",
    "fuse.glusterfs",
    "afpfs",
}

_PROBE_TIMEOUT_S = 5.0
_RETRY_DELAYS_S = (0.5, 1.0, 2.0)


@dataclass(frozen=True)
class Mount:
    """One mountinfo row: where a filesystem is mounted, its type and source."""

    mount_point: str
    fs_type: str
    source: str


@dataclass(frozen=True)
class RootStatus:
    """Availability verdict for one configured music folder.

    ``available`` means the folder exists, is readable, and is not hidden
    behind a pending automount. ``empty`` is reported separately because an
    empty folder that the library expects files under may be a mountpoint
    with nothing mounted on it — whether that blocks purging is the
    caller's call, weighed against the recorded mount identity. ``reason``
    is a user-presentable phrase, non-empty when unavailable.

    ``identity`` names the mounted filesystem the folder currently lives on
    ("nfs4 192.168.1.5:/export", "ext4 /dev/sda1"). Stable across reboots,
    it lets callers detect that a static mount silently gave way to the
    local directory underneath it — which availability alone cannot see.
    """

    root: str
    available: bool
    empty: bool
    reason: str
    fs_type: Optional[str]
    is_network: bool
    is_autofs: bool
    identity: Optional[str] = None


def _unescape(field: str) -> str:
    if "\\" not in field:
        return field
    out = []
    i = 0
    while i < len(field):
        if field[i] == "\\" and i + 4 <= len(field) and field[i + 1 : i + 4].isdigit():
            out.append(chr(int(field[i + 1 : i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def list_mounts(text: Optional[str] = None) -> list[Mount]:
    """Parse mountinfo rows, in mount order (later rows stack on earlier)."""
    if text is None:
        try:
            with open("/proc/self/mountinfo", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return []
    mounts: list[Mount] = []
    for line in text.splitlines():
        head, sep, tail = line.partition(" - ")
        if not sep:
            continue
        head_fields = head.split(" ")
        tail_fields = tail.split(" ")
        if len(head_fields) < 5 or len(tail_fields) < 2:
            continue
        mounts.append(
            Mount(
                mount_point=_unescape(head_fields[4]),
                fs_type=tail_fields[0],
                source=_unescape(tail_fields[1]),
            )
        )
    return mounts


def _covers(mount_point: str, path: str) -> bool:
    return (
        mount_point == "/"
        or path == mount_point
        or path.startswith(mount_point + os.sep)
    )


def covering_mount(path: str, mounts: Optional[list[Mount]] = None) -> Optional[Mount]:
    """The mount `path` currently lives on: longest matching mount point,
    later rows winning a tie (they are stacked on top)."""
    if mounts is None:
        mounts = list_mounts()
    best: Optional[Mount] = None
    for m in mounts:
        if _covers(m.mount_point, path) and (
            best is None or len(m.mount_point) >= len(best.mount_point)
        ):
            best = m
    return best


def _is_network_fs(fs_type: str) -> bool:
    return fs_type.startswith("nfs") or fs_type in _NETWORK_FS


def autofs_pending(root: str, mounts: Optional[list[Mount]] = None) -> bool:
    """True while an autofs mount is what answers for ``root`` — the real
    filesystem is not mounted. Reads mountinfo only, so it never triggers
    the automount itself."""
    cov = covering_mount(root, mounts)
    return cov is not None and cov.fs_type == "autofs"


def root_of(path: str, roots: Iterable[str]) -> Optional[str]:
    """The configured root ``path`` lies under, matched textually — indexed
    paths are derived from these canonical roots, so no stat is needed."""
    for root in roots:
        if root and (path == root or path.startswith(root + os.sep)):
            return root
    return None


def probe_root(root: str, mounts: Optional[list[Mount]] = None) -> RootStatus:
    """Blocking availability probe. The stat deliberately touches ``root`` so
    a pending automount is triggered; run it off the event loop — over a hung
    network mount it can block for a long time."""
    try:
        accessible = os.path.isdir(root) and os.access(root, os.R_OK | os.X_OK)
    except OSError:
        accessible = False
    if mounts is None:
        mounts = list_mounts()
    cov = covering_mount(root, mounts)
    fs_type = cov.fs_type if cov else None
    identity = f"{cov.fs_type} {cov.source}" if cov else None
    is_autofs = any(m.fs_type == "autofs" and _covers(m.mount_point, root) for m in mounts)
    is_network = fs_type is not None and _is_network_fs(fs_type)
    if fs_type == "autofs":
        return RootStatus(
            root=root,
            available=False,
            empty=True,
            reason="the automounter has not mounted it",
            fs_type=fs_type,
            is_network=is_network,
            is_autofs=True,
            identity=identity,
        )
    if not accessible:
        return RootStatus(
            root=root,
            available=False,
            empty=True,
            reason="the folder is missing or not readable",
            fs_type=fs_type,
            is_network=is_network,
            is_autofs=is_autofs,
            identity=identity,
        )
    try:
        with os.scandir(root) as entries:
            empty = next(iter(entries), None) is None
    except OSError:
        return RootStatus(
            root=root,
            available=False,
            empty=True,
            reason="the folder could not be listed",
            fs_type=fs_type,
            is_network=is_network,
            is_autofs=is_autofs,
            identity=identity,
        )
    return RootStatus(
        root=root,
        available=True,
        empty=empty,
        reason="",
        fs_type=fs_type,
        is_network=is_network,
        is_autofs=is_autofs,
        identity=identity,
    )


# One in-flight probe per root (per event loop): repeated status or content
# requests against a hung mount must pile onto the same blocked worker
# thread, not claim a fresh one each and exhaust the executor.
_inflight_probes: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}


async def probe_root_async(root: str, timeout: float = _PROBE_TIMEOUT_S) -> RootStatus:
    """`probe_root` off the event loop, bounded. On timeout the shared worker
    thread is left to its blocked stat — later probes of the same root reuse
    it — and the root is reported unavailable."""
    loop = asyncio.get_running_loop()
    entry = _inflight_probes.get(root)
    if entry is None or entry[0] is not loop or entry[1].done():
        task = loop.create_task(asyncio.to_thread(probe_root, root))
        _inflight_probes[root] = (loop, task)
    else:
        task = entry[1]
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout)
    except (asyncio.TimeoutError, TimeoutError):
        return RootStatus(
            root=root,
            available=False,
            empty=True,
            reason="it did not respond (hung network mount?)",
            fs_type=None,
            is_network=False,
            is_autofs=False,
        )


async def await_root_available(root: str, deadline_s: float = 6.0) -> RootStatus:
    """Probe until ``root`` is available or the deadline passes. The probes
    themselves trigger a pending automount; the retries are what give a slow
    mount its chance."""
    start = time.monotonic()
    status = await probe_root_async(root, timeout=deadline_s)
    for delay in _RETRY_DELAYS_S:
        if status.available:
            break
        remaining = deadline_s - (time.monotonic() - start)
        if remaining <= delay:
            break
        await asyncio.sleep(delay)
        remaining = deadline_s - (time.monotonic() - start)
        status = await probe_root_async(root, timeout=max(0.5, remaining))
    return status
