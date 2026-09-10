"""The bundled CRF is pinned: weights, manifest and vendored code together.

A model built for a different feature version does not raise — it quietly
produces worse spans — so the pairing has to be asserted rather than assumed.
The weights digest is hard-coded on purpose: a deliberate retrain must edit
this file, which is what records that it happened.
"""

import hashlib
import json
from importlib import resources

from kalinka_plugin_localfiles.filename_model import (
    FilenameModel,
    get_parser,
    parse_music_path,
)
from kalinka_plugin_localfiles.filename_model._vendor.filename_parser import features
from kalinka_plugin_localfiles.filename_model.parser import (
    MANIFEST_FILENAME,
    MODEL_FILENAME,
    WEIGHTS_PACKAGE,
)

EXPECTED_MODEL_SHA256 = (
    "04377d6f69f73901ca246fa4651072e25fa6dd2c4573d807a71e55d9a12c0f80"
)
VENDOR_PACKAGE = "kalinka_plugin_localfiles.filename_model._vendor"


def _weights() -> bytes:
    return resources.files(WEIGHTS_PACKAGE).joinpath(MODEL_FILENAME).read_bytes()


def _manifest() -> dict:
    text = resources.files(WEIGHTS_PACKAGE).joinpath(MANIFEST_FILENAME).read_text()
    return json.loads(text)


def test_bundled_weights_are_the_expected_model():
    assert hashlib.sha256(_weights()).hexdigest() == EXPECTED_MODEL_SHA256


def test_weights_and_manifest_ship_as_a_pair():
    manifest = _manifest()
    assert hashlib.sha256(_weights()).hexdigest() == manifest["model_sha256"]
    assert len(_weights()) == manifest["model_bytes"]


def test_feature_version_matches_the_vendored_extractor():
    assert _manifest()["feature_version"] == features.FEATURE_VERSION


def test_vendored_runtime_is_unmodified():
    """Local behaviour belongs in assembler.py, never in the copied parser."""
    listing = (
        resources.files(VENDOR_PACKAGE).joinpath("filename_parser.sha256").read_text()
    )
    expected = dict(
        reversed(line.split(maxsplit=1))
        for line in listing.splitlines()
        if line.strip()
    )
    for name, digest in expected.items():
        data = (
            resources.files(f"{VENDOR_PACKAGE}.filename_parser")
            .joinpath(name.strip())
            .read_bytes()
        )
        assert hashlib.sha256(data).hexdigest() == digest, f"{name} was edited"


def test_model_loads_and_reports_its_identity():
    identity = get_parser().identity()
    assert identity["available"] is True
    assert identity["reason"] is None
    assert identity["sha256"] == EXPECTED_MODEL_SHA256[:16]
    assert identity["feature_version"] == features.FEATURE_VERSION


class TestDegradedModel:
    """A broken model must cost the filename fallback and nothing else.

    The enricher installs every wheel in one go and constructs its plugins
    eagerly, so a hard failure here would take MusicBrainz and AcoustID down
    with it.
    """

    def test_missing_weights_disable_the_model_without_raising(self, tmp_path):
        model = FilenameModel(tmp_path / "absent.crfsuite", source="env")
        assert model.available is False
        assert model.parse("Nick Cave/Murder Ballads/Stagger Lee.mp3") is None
        assert "FileNotFoundError" in model.identity()["reason"]

    def test_a_feature_version_mismatch_refuses_to_load(self, tmp_path):
        """Mismatched weights do not raise on their own; they get quietly
        worse, so a half-finished re-vendor has to be refused here."""
        weights = tmp_path / "model.crfsuite"
        weights.write_bytes(_weights())
        (tmp_path / "model.training.json").write_text(
            json.dumps({**_manifest(), "feature_version": features.FEATURE_VERSION + 1})
        )
        model = FilenameModel(weights, source="env")
        assert model.available is False
        assert "feature version" in model.identity()["reason"]

    def test_a_path_outside_every_root_yields_nothing(self):
        assert parse_music_path("/etc/passwd.mp3", None) is None
        assert parse_music_path("", "/home/user/Music") is None
