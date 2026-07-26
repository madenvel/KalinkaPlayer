"""Tests for the update-check module behind GET /server/update."""

from __future__ import annotations

import asyncio

from kalinka_server import update_check
from kalinka_server.update_check import (
    UpdateChecker,
    is_newer,
    latest_release_version,
    upgrade_supported,
    validate_upgrade_request,
)


def _feed(*tags):
    """Minimal GitHub releases.atom document with the given tags, newest first."""
    entries = "".join(
        f"<entry><id>tag:github.com,2008:Repository/12345/{tag}</id>"
        f"<title>{tag}</title></entry>"
        for tag in tags
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<title>Release notes</title>{entries}</feed>"
    )


class TestLatestReleaseVersion:
    def test_picks_first_matching_release(self):
        feed = _feed("kalinka-v3.3.0", "kalinka-v3.2.0")
        assert latest_release_version(feed) == "3.3.0"

    def test_skips_foreign_tags(self):
        feed = _feed("jamendo-ai-v2", "kalinka-v3.3.0")
        assert latest_release_version(feed) == "3.3.0"

    def test_none_when_no_matching_release(self):
        assert latest_release_version(_feed("jamendo-ai-v1")) is None
        assert latest_release_version(_feed()) is None

    def test_none_on_malformed_feed(self):
        assert latest_release_version("<html>not a feed") is None


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


class TestValidateUpgradeRequest:
    def test_accepts_matching_available_update(self):
        assert validate_upgrade_request("3.3.0", "3.3.0", "3.2.0") is None

    def test_rejects_when_no_update_known(self):
        assert validate_upgrade_request("3.3.0", None, "3.2.0") is not None

    def test_rejects_after_upgrade_already_happened(self):
        # Server upgraded to 3.3.0; a retried request for 3.3.0 must not
        # fire the installer again.
        assert validate_upgrade_request("3.3.0", "3.3.0", "3.3.0") is not None

    def test_rejects_stale_target_version(self):
        # A newer release was published since the client saw the banner.
        assert validate_upgrade_request("3.3.0", "3.4.0", "3.2.0") is not None


class TestUpdateChecker:
    def _checker_with_fetches(self, results):
        checker = UpdateChecker()
        fetches = iter(results)

        async def fake_fetch():
            return next(fetches)

        checker._fetch = fake_fetch
        return checker

    def test_starts_with_no_known_release(self):
        assert UpdateChecker().latest is None

    def test_check_updates_latest(self):
        checker = self._checker_with_fetches(["3.3.0"])
        assert asyncio.run(checker.check_now()) == "3.3.0"
        assert checker.latest == "3.3.0"

    def test_failed_check_keeps_last_known_result(self):
        # An available update must not vanish on a network blip.
        checker = self._checker_with_fetches(["3.3.0", None])

        async def run():
            await checker.check_now()
            assert await checker.check_now() is None

        asyncio.run(run())
        assert checker.latest == "3.3.0"
