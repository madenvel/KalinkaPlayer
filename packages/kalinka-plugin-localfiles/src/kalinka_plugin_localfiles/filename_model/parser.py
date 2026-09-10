"""Process-wide owner of the CRF weights."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Dict, Optional, Tuple

from ._vendor.filename_parser import features

logger = logging.getLogger(__name__)

WEIGHTS_PACKAGE = "kalinka_plugin_localfiles.filename_model.weights"
MODEL_FILENAME = "model.crfsuite"
MANIFEST_FILENAME = "model.training.json"
MODEL_ENV_VAR = "KALINKA_FILENAME_MODEL"


@dataclass(frozen=True)
class ModelIdentity:
    """What the enrichment fingerprint records about the loaded model.

    ``sha256`` is truncated: the fingerprint only needs to change when the
    weights do, and the full digest lives in the manifest. ``available`` is
    part of the identity on purpose — a degraded model keeps the signature
    stable while it is broken, and flips it back on recovery so the rows it
    could not enrich re-open by themselves.
    """

    sha256: Optional[str]
    feature_version: int
    source: str
    available: bool
    reason: Optional[str]


def resolve_model_path() -> Tuple[Path, str]:
    """The weights to load, and whether they came from the env override."""
    override = os.environ.get(MODEL_ENV_VAR)
    if override:
        return Path(override), "env"
    with resources.as_file(
        resources.files(WEIGHTS_PACKAGE).joinpath(MODEL_FILENAME)
    ) as path:
        return path, "bundled"


class FilenameModel:
    """The CRF tagger plus the weights it was loaded from.

    Construction never raises and never loads: the first :meth:`parse` opens
    the model, and any failure — ``pycrfsuite`` missing, weights absent or
    truncated, a feature-version mismatch — is caught, logged once and turned
    into ``available = False``. Callers get ``None`` from :meth:`parse` and
    degrade to whatever they do when a path yields nothing, so a broken model
    costs the filename fallback and nothing else.

    One instance is meant to be shared; see :func:`get_parser`.
    """

    def __init__(
        self, model_path: Optional[Path] = None, source: str = "bundled"
    ) -> None:
        self._explicit_path = model_path
        self._source = source
        self._parser = None
        self._sha256: Optional[str] = None
        self._reason: Optional[str] = None
        self._loaded = False
        self._parse_failed = False

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._parser is not None

    def identity(self) -> Dict:
        self._ensure_loaded()
        return asdict(
            ModelIdentity(
                sha256=self._sha256[:16] if self._sha256 else None,
                feature_version=features.FEATURE_VERSION,
                source=self._source,
                available=self._parser is not None,
                reason=self._reason,
            )
        )

    def parse(self, view: str) -> Optional[Dict]:
        """Tag ``view``, or ``None`` when the model is unavailable or threw."""
        self._ensure_loaded()
        if self._parser is None:
            return None
        try:
            return self._parser.parse(view)
        except Exception as exc:
            # One bad filename must not disable the model for the library, so
            # this never latches the way a load failure does.
            level = logging.DEBUG if self._parse_failed else logging.WARNING
            logger.log(level, "Filename parse failed for %r: %s", view, exc)
            self._parse_failed = True
            return None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            self._load()
        except Exception as exc:
            self._parser = None
            self._reason = f"{type(exc).__name__}: {exc}"
            logger.error(
                "Filename model unavailable, falling back to no parse: %s",
                self._reason,
            )

    def _load(self) -> None:
        if self._explicit_path is not None:
            path, source = self._explicit_path, self._source
        else:
            path, source = resolve_model_path()
        self._source = source
        self._sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        self._check_feature_version(path)
        # Imported here, not at module scope, so importing this package never
        # pulls in the C extension and the ImportError has one catch site.
        from ._vendor.filename_parser.model import FilenameParser

        self._parser = FilenameParser(path)
        logger.info(
            "Filename model loaded (%s, sha256 %s, feature version %d)",
            source, self._sha256[:16], features.FEATURE_VERSION,
        )

    def _check_feature_version(self, path: Path) -> None:
        """Weights and feature extractor must version together.

        A model built against a different feature version does not raise on
        its own — it silently produces worse spans — so a half-finished
        re-vendor has to be turned into a refusal here.
        """
        manifest_path = (
            path.with_suffix(".training.json")
            if self._source == "env"
            else path.with_name(MANIFEST_FILENAME)
        )
        if not manifest_path.is_file():
            if self._source == "env":
                logger.info(
                    "No manifest beside %s; skipping the feature-version check", path
                )
                return
            raise FileNotFoundError(f"model manifest missing: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recorded = manifest.get("feature_version")
        if recorded != features.FEATURE_VERSION:
            raise ValueError(
                f"feature version {recorded!r} in {manifest_path.name} does not match "
                f"the vendored extractor's {features.FEATURE_VERSION}"
            )


@lru_cache(maxsize=1)
def get_parser() -> FilenameModel:
    """The shared model. Loaded once per process, on first use."""
    return FilenameModel()
