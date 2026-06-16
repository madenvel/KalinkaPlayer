"""Generate the canonical query set from the MTG-Jamendo tag taxonomy.

One natural-language query per target tag; relevance = corpus track carries the
tag. Grouped by category. Reads query_tags.json, writes queries.json.

Artifacts go to $MTG_BENCH_DIR (default <repo>/tmp/mtg_jamendo_eval).
"""
from __future__ import annotations
import json, os
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
WORK = Path(os.environ.get("MTG_BENCH_DIR", REPO / "tmp" / "mtg_jamendo_eval"))

INSTR_FIX = {
    "electricguitar": "electric guitar", "acousticguitar": "acoustic guitar",
    "classicalguitar": "classical guitar", "electricpiano": "electric piano",
    "drummachine": "drum machine", "doublebass": "double bass",
    "pipeorgan": "pipe organ", "computer": "computer / electronic production",
}
GENRE_FIX = {"easylistening": "easy listening", "hiphop": "hip hop",
             "drumnbass": "drum and bass", "popfolk": "folk pop",
             "rnb": "r&b", "newage": "new age"}


def phrase(cat, tag):
    name = tag.split("---")[-1]
    if cat == "genre":
        return f"{GENRE_FIX.get(name, name)} music"
    if cat == "instrument":
        return f"music with {INSTR_FIX.get(name, name)}"
    return f"{name} music"  # moodtheme


def main():
    qt = json.load(open(WORK / "query_tags.json"))
    out = {"categories": {}}
    for cat, tags in qt.items():
        out["categories"][cat] = [{"q": phrase(cat, t), "tag": t} for t in tags]
    with open(WORK / "queries.json", "w") as f:
        json.dump(out, f, indent=2)
    total = sum(len(v) for v in out["categories"].values())
    print(f"wrote {total} queries across {len(out['categories'])} categories -> {WORK/'queries.json'}")
    for cat, qs in out["categories"].items():
        print(f"  {cat:11}: " + ", ".join(q["q"] for q in qs[:5]) + " ...")


if __name__ == "__main__":
    main()
