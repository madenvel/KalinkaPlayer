"""Bounded lexical hashing plus structural features, without a word vocabulary."""
from __future__ import annotations

import re
import unicodedata
import zlib
from .common import BRACKET_PAIRS, tokenize

FEATURE_VERSION = 2
HASH_BUCKETS = 8192
EDITION_WORDS = frozenset("remaster remastered remix remixed live deluxe expanded edition version mix mono stereo bonus acoustic instrumental extended original anniversary radio edit demo session sessions unreleased japan japanese limited explicit clean disc cd disk диджипак ремастер концерт".split())
TECH_WORDS = frozenset("flac mp3 wav aiff alac ape ogg opus m4a wma dsf dff khz hz bit bits kbps kb b web vinyl lossless hifi hires rip cdr sacd dsd pcm".split())


def bucket(word: str) -> str:
    return str(zlib.crc32(word.casefold().encode("utf-8")) % HASH_BUCKETS)


def shape(word: str) -> str:
    out = "".join("d" if c.isdigit() else "A" if c.isupper() else "a" if c.isalpha() else c for c in word)
    return re.sub(r"(.)\1+", r"\1", out)[:12]


def unmatched_brackets(text: str, components: list[tuple[int, int]]) -> set[int]:
    """Offsets of brackets with no partner inside their own path component.

    Depth alone cannot express this: a ")" that closes a group and a ")" left
    over from a group opened in another field sit at the same depth once the
    counter has been clamped at zero. A field never opens on a leftover bracket,
    so naming them lets the tagger learn that boundary rather than infer it.

    Classifying what each *matched* group holds was tried alongside this and
    rejected on dev: a coarse word-list verdict overrode the model's own lexical
    evidence and cost real editions, taking "12 Death Row (Reprise)" from an
    EDITION to part of the title (EDITION F1 100.00 -> 98.57).
    """
    opener_of = {closer: opener for opener, closer in BRACKET_PAIRS}
    orphans: set[int] = set()
    for comp_start, comp_end in components:
        open_positions: dict[str, list[int]] = {opener: [] for opener, _ in BRACKET_PAIRS}
        for index in range(comp_start, comp_end):
            char = text[index]
            if char in open_positions:
                open_positions[char].append(index)
            elif char in opener_of:
                stack = open_positions[opener_of[char]]
                if stack:
                    stack.pop()
                else:
                    orphans.add(index)
        for stack in open_positions.values():
            orphans.update(stack)
    return orphans


def sequence_features(text: str) -> list[dict]:
    tokens = tokenize(text)
    if not tokens:
        return []
    components = [m.span() for m in re.finditer(r"[^/\\]+", text)]
    if not components:
        # Separator-only input: treat the whole string as one component.
        components = [(0, len(text))]
    descriptors = []
    for ci, (comp_start, comp_end) in enumerate(components):
        content = text[comp_start:comp_end]
        dashes = [m.start() + comp_start for m in re.finditer(r"(?:\s[-–—]\s|_-_|\s[-–—]_)", content)]
        descriptors.append((comp_start, comp_end, min(3, len(components)-ci-1), dashes))
    orphans = unmatched_brackets(text, components)
    base = []
    ci = 0
    bracket_depth = 0
    for i, (word, a, b) in enumerate(tokens):
        while ci + 1 < len(descriptors) and a >= descriptors[ci][1]:
            ci += 1
            bracket_depth = 0
        ca, cb, depth, dashes = descriptors[ci]
        low = word.casefold()
        digit = word.isdecimal()
        number = int(word) if digit and len(word) < 6 else -1
        if word in "([{":
            bracket_depth += 1
        script = unicodedata.name(word[0], "UNKNOWN").split()[0]
        base.append({"w": bucket(word), "shape": shape(word), "script": script,
                     "punct": word if len(word) == 1 and not word.isalnum() else "",
                     "len": str(min(len(word), 12)), "digit": digit,
                     "year": 1900 <= number <= 2099,
                     "small_number": 0 <= number <= 99, "zero_pad": digit and word.startswith("0"),
                     "edition": low in EDITION_WORDS, "technical": low in TECH_WORDS,
                     "depth": str(depth), "comp_start": a == ca,
                     "comp_end": b == cb, "bracket": str(min(bracket_depth, 2)),
                     "orphan_bracket": a in orphans,
                     "dash_count": str(min(3, len(dashes))),
                     "dash_segment": str(min(3, sum(x < a for x in dashes))),
                     "position": str(min(9, max(0, (a - ca) * 10 // max(1, cb - ca)))),
                     "near_start": str(min(10, max(0, a-ca))),
                     "near_end": str(min(10, max(0, cb-b))),
                     "extension": bool(re.fullmatch(r"\.[A-Za-z0-9]{1,8}", text[a-1:])) if a else False})
        if word in ")]}":
            bracket_depth = max(0, bracket_depth - 1)
    features = []
    for i, descriptor in enumerate(base):
        row = {"bias": 1.0, **descriptor}
        row["depth_segment"] = descriptor["depth"] + ":" + descriptor["dash_segment"]
        row["depth_position"] = descriptor["depth"] + ":" + descriptor["position"]
        for delta in (-2, -1, 1, 2):
            j = i + delta
            if 0 <= j < len(base):
                for key in ("w", "shape", "punct", "digit", "edition", "technical", "year",
                            "comp_start", "comp_end", "orphan_bracket"):
                    row[f"{delta}:{key}"] = base[j][key]
            else:
                row[f"boundary{delta}"] = True
        features.append(row)
    return features
