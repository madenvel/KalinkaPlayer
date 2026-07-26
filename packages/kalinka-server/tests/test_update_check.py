"""Tests for the update-check module behind GET /server/update."""

from __future__ import annotations

import asyncio

import pytest

from kalinka_server import update_check
from kalinka_server.update_check import (
    UpdateChecker,
    is_newer,
    latest_release_version,
    upgrade_supported,
)


def _release(tag, draft=False, prerelease=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease}


class TestLatestReleaseVersion:
    def test_picks_first_matching_release(self):
        releases = [_release("kalinka-v3.3.0"), _release("kalinka-v3.2.0")]
        assert latest_release_version(releases) == "3.3.0"

    def test_skips_drafts_and_prereleases(self):
        releases = [
            _release("kalinka-v4.0.0", draft=True),
            _release("kalinka-v3.9.0", prerelease=True),
            _release("kalinka-v3.3.0"),
        ]
        assert latest_release_version(releases) == "3.3.0"

    def test_skips_foreign_tags(self):
        releases = [_release("jamendo-ai-v1"), _release("kalinka-v3.3.0")]
        assert latest_release_version(releases) == "3.3.0"

    def test_none_when_no_matching_release(self):
        assert latest_release_version([_release("jamendo-ai-v1")]) is None
        assert latest_release_version([]) is None

    def test_tolerates_malformed_entries(self):
        releases = ["garbage", {"no_tag": True}, _release("kalinka-v1.0.0")]
        assert latest_release_version(releases) == "1.0.0"


class TestIsNewer:
    def test_newer(self):
        assert is_newer("3.3.0", "3.2.0")

    def test_equal_and_older(self):
        assert not is_newer("3.2.0", "3.2.0")
        assert not is_newer("3.1.0", "3.2.0")

    def test_release_beats_dev_build_of_same_version(self):
        assert is_newer("3.3.0", "3.3.0.dev5+g1234567")

    def test_unbuilt_source_tree_reads_as_older(self):
        assert is_newer("3.2.0", "0.0.0")

    def test_invalid_versions_are_not_newer(self):
        assert not is_newer("not-a-version", "3.2.0")
        assert not is_newer("3.3.0", "not-a-version")


class TestUpgradeSupported:
    def test_supported_when_root_side_files_exist(self, tmp_path, monkeypatch):
        script = tmp_path / "install-release.sh"
        unit = tmp_path / "kalinka-upgrade.path"
        script.touch()
        unit.touch()
        monkeypatch.delenv("KALINKA_PREFIX", raising=False)
        monkeypatch.setattr(update_check, "_UPGRADE_SCRIPT", str(script))
        monkeypatch.setattr(update_check, "_UPGRADE_PATH_UNIT", str(unit))
        assert upgrade_supported()

    def test_unsupported_when_files_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KALINKA_PREFIX", raising=False)
        monkeypatch.setattr(
            update_check, "_UPGRADE_SCRIPT", str(tmp_path / "missing.sh")
        )
        monkeypatch.setattr(
            update_check, "_UPGRADE_PATH_UNIT", str(tmp_path / "missing.path")
        )
        assert not upgrade_supported()

    def test_unsupported_under_dev_prefix(self, tmp_path, monkeypatch):
        """A dev fakeroot has no systemd watching its run dir, so upgrade
        must read as unsupported even if a deb install exists on the box."""
        script = tmp_path / "install-release.sh"
        unit = tmp_path / "kalinka-upgrade.path"
        script.touch()
        unit.touch()
        monkeypatch.setenv("KALINKA_PREFIX", str(tmp_path))
        monkeypatch.setattr(update_check, "_UPGRADE_SCRIPT", str(script))
        monkeypatch.setattr(update_check, "_UPGRADE_PATH_UNIT", str(unit))
        assert not upgrade_supported()


class TestUpdateChecker:
    def _checker_with_fetches(self, results):
        checker = UpdateChecker()
        calls = []

        async def fake_fetch():
            calls.append(1)
            return results[min(len(calls), len(results)) - 1]

        checker._fetch = fake_fetch
        return checker, calls

    def test_success_is_cached(self):
        checker, calls = self._checker_with_fetches(["3.3.0"])

        async def run():
            assert await checker.latest_version() == "3.3.0"
            assert await checker.latest_version() == "3.3.0"

        asyncio.run(run())
        assert len(calls) == 1

    def test_failure_is_cached_then_retried_after_expiry(self):
        checker, calls = self._checker_with_fetches([None, "3.3.0"])

        async def run():
            assert await checker.latest_version() is None
            # Within the failure TTL the None is served from cache.
            assert await checker.latest_version() is None
            checker._expires = 0.0
            assert await checker.latest_version() == "3.3.0"

        asyncio.run(run())
        assert len(calls) == 2
