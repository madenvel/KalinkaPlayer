"""Canonical spans use Python string (Unicode code point) offsets."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

LABELS = ("ARTIST", "TITLE", "TRACK_NUMBER", "ALBUM", "YEAR", "EDITION", "DISC_NUMBER", "TECHNICAL")
SCHEMA_VERSION = 1
BRACKET_PAIRS = (("(", ")"), ("[", "]"), ("{", "}"))
SEPARATOR_CHARS = "-\u2013\u2014_,;:|/\\ \t\u3000"


def is_separator_run(run: str, at_component_edge: bool = True) -> bool:
    """Is this edge run punctuation that separates fields, not part of one?

    A run that carries whitespace, is written in underscores, or simply repeats
    is a filename separator in every layout this parser sees. A *lone* dash is
    the hard case, because it is load-bearing in real names: Japanese releases
    mark a subtitle as "千年前から来た少年-サンゾウ-", and "-M-" is a stage name.
    Those sit against the edge of their own path component, with nothing but the
    extension beyond; a dash with more text on the far side is dividing two
    fields. That is the line drawn here, and it is the same one the importer
    draws when it carves a bracketed qualifier off a title.
    """
    if not run or any(char not in SEPARATOR_CHARS for char in run):
        return False
    return len(run) > 1 or run.isspace() or run == "_" or not at_component_edge


def trim_separator_edges(text: str, start: int, end: int,
                         bounds: tuple[int, int] | None = None) -> tuple[int, int]:
    """Shrink [start, end) off leading and trailing separator runs.

    ``bounds`` gives the surrounding path component, so a lone dash can be told
    apart from one dividing fields. Without it the string is taken to be a whole
    component, which is what a bare metadata value is.
    """
    left, right = bounds if bounds else (0, len(text))
    while start < end:
        run = (end - start) - len(text[start:end].lstrip(SEPARATOR_CHARS))
        if not run or not is_separator_run(text[start:start + run], start == left):
            break
        start += run
    while start < end:
        stripped = text[start:end].rstrip(SEPARATOR_CHARS)
        run = (end - start) - len(stripped)
        if not run or not is_separator_run(text[end - run:end], end == right):
            break
        end -= run
    return start, end


def strip_separator_edges(value: str) -> str:
    """The same rule applied to a metadata string rather than a span."""
    start, end = trim_separator_edges(value, 0, len(value))
    return value[start:end]


def component_bounds(text: str, position: int) -> tuple[int, int]:
    """The path component containing ``position``, excluding its separators."""
    start = max(text.rfind("/", 0, position), text.rfind("\\", 0, position)) + 1
    ends = [i for i in (text.find("/", position), text.find("\\", position)) if i >= 0]
    return start, min(ends) if ends else len(text)


def repair_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Project a span onto the well-formedness rules the labels already obey.

    Only ever shrinks, so no text is invented and no field is rewritten: it
    removes separator edges, and removes an edge bracket left without a partner
    inside a component that is itself balanced. Data import rejects such a span
    as ``unbalanced_bracket_span``; the tagger has no way to honour that, since
    a bracket is just another token to it.
    """
    comp_start, comp_end = component_bounds(text, start)
    component = text[comp_start:comp_end]
    balanced = [pair for pair in BRACKET_PAIRS
                if component.count(pair[0]) == component.count(pair[1])]
    extension = re.search(r"\.[A-Za-z0-9]{1,8}$", component)
    if extension:
        comp_end = comp_start + extension.start()
    for _ in range(len(BRACKET_PAIRS) + 1):
        moved = trim_separator_edges(text, start, end, (comp_start, comp_end))
        changed = moved != (start, end)
        start, end = moved
        for opener, closer in balanced:
            if start >= end:
                break
            piece = text[start:end]
            if text[start] == closer and piece.count(closer) > piece.count(opener):
                start += 1
                changed = True
            elif text[end - 1] == opener and piece.count(opener) > piece.count(closer):
                end -= 1
                changed = True
        if not changed:
            break
    return start, end


def repair_spans(text: str, spans: list[dict]) -> list[dict]:
    """Repaired copies of ``spans``; a span shrunk to nothing is dropped."""
    out = []
    for span in spans:
        start, end = repair_span(text, span["start"], span["end"])
        if start >= end:
            continue
        out.append({**span, "start": start, "end": end, "text": text[start:end]})
    return out


def context_window(text: str) -> tuple[str, int]:
    """Keep at most three parent components; never normalize the source text."""
    parts = list(re.finditer(r"[^/\\]+", text))
    if not parts:
        return text, 0
    first = max(0, len(parts) - 4)
    for i, part in enumerate(parts[:-1]):
        if part.group().casefold() in {"music", "audio", "library"}:
            first = max(first, i + 1)
    offset = parts[first].start()
    return text[offset:], offset


def tokenize(text: str) -> list[tuple[str, int, int]]:
    """Unbounded Unicode words/digits and individual punctuation; keep offsets."""
    tokens = []
    start = 0
    kind = None
    for i, char in enumerate(text):
        category = unicodedata.category(char)
        current = "word" if category[0] in "LM" else "number" if category[0] == "N" else None
        if kind is not None and current == kind:
            continue
        if kind is not None:
            tokens.append((text[start:i], start, i))
        kind = current
        start = i
        if current is None and not char.isspace():
            tokens.append((char, i, i + 1))
    if kind is not None:
        tokens.append((text[start:], start, len(text)))
    return tokens


def validate_record(record: dict) -> None:
    text = record["input"]
    if not isinstance(text, str):
        raise ValueError("input must be a string")
    previous = 0
    for span in sorted(record["spans"], key=lambda x: (x["start"], x["end"])):
        a, b = span["start"], span["end"]
        if span["label"] not in LABELS or not isinstance(a, int) or not isinstance(b, int):
            raise ValueError("invalid span label or offset")
        if not 0 <= a < b <= len(text) or a < previous or text[a:b] != span["text"]:
            raise ValueError("invalid, overlapping, or inexact span")
        previous = b


def token_labels(text: str, spans: list[dict]) -> list[str]:
    """Reject an alignment which would split a model token."""
    tokens = tokenize(text)
    result = ["O"] * len(tokens)
    for span in spans:
        indices = [i for i, (_, a, b) in enumerate(tokens) if a < span["end"] and b > span["start"]]
        if not indices or tokens[indices[0]][1] != span["start"] or tokens[indices[-1]][2] != span["end"]:
            raise ValueError("span does not match token boundaries")
        for j, i in enumerate(indices):
            result[i] = ("B-" if j == 0 else "I-") + span["label"]
    return result


def read_jsonl(path: str | Path):
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def result_from_spans(text: str, spans: list[dict], min_confidence: float = 0.0) -> dict:
    accepted = [s for s in spans if s.get("score", 1.0) >= min_confidence]
    fields = {}
    for label in LABELS:
        candidates = [s for s in spans if s["label"] == label]
        good = [s for s in accepted if s["label"] == label]
        # Prefer a basename occurrence when directory and basename repeat a
        # field, because the basename is the most specific mention. YEAR is the
        # exception: a path that carries two years states the original release
        # first and the reissue or remaster second, so the earliest one is the
        # release year a caller wants.
        chooser = min if label == "YEAR" else max
        best = chooser(good, key=lambda s: s["start"], default=None)
        field = {"status": "detected" if best else "uncertain" if candidates else "not_detected"}
        if best:
            field.update({k: best[k] for k in ("text", "start", "end", "score") if k in best})
            if label in {"TRACK_NUMBER", "DISC_NUMBER", "YEAR"}:
                try:
                    field["value"] = int(best["text"])
                except ValueError:
                    field["value"] = None
        fields[label.lower()] = field
    extension = re.search(r"\.([a-zA-Z0-9]{1,8})$", text)
    return {"input": text, "spans": accepted, "fields": fields,
            "format": extension.group(1).lower() if extension else None,
            "abstained": [s for s in spans if s not in accepted]}
