#!/usr/bin/env python3
"""A music root that bounces (autofs idle expiry) and returns with the mount
identity the library was indexed from re-arms its watches without the full
rescan — however long it was gone, since remote changes are invisible to
inotify anyway and the periodic scan covers them. A changed identity (media
swap), a missing recorded identity, a deleted root, or startup deferral
still owes a rescan.
"""

from kalinka_plugin_localfiles.indexer.indexer import rearm_needs_rescan

NFS = "nfs4 192.168.1.5:/export/music"


def test_unmount_bounce_with_matching_identity_skips_rescan():
    assert rearm_needs_rescan(True, NFS, NFS) is False


def test_unconditional_rescan_without_a_seen_unmount():
    # Startup deferral / deleted root.
    assert rearm_needs_rescan(False, NFS, NFS) is True


def test_changed_identity_rescans():
    assert rearm_needs_rescan(True, "vfat /dev/sda1", NFS) is True


def test_missing_stored_signature_rescans():
    assert rearm_needs_rescan(True, NFS, None) is True
    assert rearm_needs_rescan(True, None, NFS) is True
