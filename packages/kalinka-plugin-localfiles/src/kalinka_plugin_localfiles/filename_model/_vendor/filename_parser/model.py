"""Small native CRF runtime; no numerical stack, GPU, or network required."""
from __future__ import annotations

import math
import os
import threading
from functools import lru_cache
from pathlib import Path

import pycrfsuite
from .common import context_window, repair_spans, result_from_spans, tokenize
from .features import sequence_features

DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "artifacts" / "filename" / "model.crfsuite"


class FilenameParser:
    def __init__(self, model_path: str | Path = DEFAULT_MODEL):
        self.model_path = Path(model_path)
        self.tagger = pycrfsuite.Tagger()
        self.tagger.open(str(self.model_path))
        self._lock = threading.Lock()

    def parse(self, text: str, min_confidence: float = 0.0) -> dict:
        if not isinstance(text, str):
            raise TypeError("filename/path must be a string")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must lie in [0, 1]")
        view, offset = context_window(text)
        tokens = tokenize(view)
        if not tokens:
            return result_from_spans(text, [], min_confidence)
        with self._lock:
            labels = self.tagger.tag(sequence_features(view))
            scores = [self.tagger.marginal(y, i) for i, y in enumerate(labels)]
        spans = []
        start = None
        field = None

        def finish(end):
            if start is not None:
                a, b = tokens[start][1] + offset, tokens[end - 1][2] + offset
                # Minimum token marginal is a conservative raw model score,
                # not a calibrated probability of exact span correctness.
                spans.append({"label": field, "start": a, "end": b,
                              "text": text[a:b], "score": min(scores[start:end])})

        for i, label in enumerate(labels + ["O"]):
            new_field = label[2:] if label != "O" else None
            if label.startswith("B-") or new_field != field:
                finish(i)
                start = i if new_field else None
                field = new_field
        # The tagger labels tokens, so nothing stops it opening a span on a
        # stray ")" or a separator. Repair shrinks such a span onto the same
        # well-formedness rules the training labels satisfy; it never widens
        # one, so it cannot invent text the path does not contain.
        return result_from_spans(text, repair_spans(text, spans), min_confidence)

    def parse_batch(self, texts, min_confidence: float = 0.0) -> list[dict]:
        return [self.parse(text, min_confidence=min_confidence) for text in texts]


@lru_cache(maxsize=1)
def _default_parser():
    return FilenameParser(os.environ.get("KALINKA_FILENAME_MODEL", DEFAULT_MODEL))


def parse_filename(name: str, min_confidence: float = 0.0) -> dict:
    return _default_parser().parse(name, min_confidence=min_confidence)


parse_path = parse_filename
