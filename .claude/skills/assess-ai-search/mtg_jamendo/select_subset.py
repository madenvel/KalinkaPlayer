"""Select a stratified MTG-Jamendo subset + emit manifest and query-tags.

Ground truth = official autotagging tags (genre/instrument/mood-theme). Picks a
set of target query-tags (top-N per category by corpus frequency) then samples
tracks so each target tag has a floor of relevant tracks in the subset.
Deterministic (fixed seed + sorted iteration).

Artifacts go to $MTG_BENCH_DIR (default <repo>/tmp/mtg_jamendo_eval).
"""
from __future__ import annotations
import collections, json, os, random, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
WORK = Path(os.environ.get("MTG_BENCH_DIR", REPO / "tmp" / "mtg_jamendo_eval"))
WORK.mkdir(parents=True, exist_ok=True)

B = "https://raw.githubusercontent.com/MTG/mtg-jamendo-dataset/master/data"
CATS = {"genre": "autotagging_genre.tsv",
        "instrument": "autotagging_instrument.tsv",
        "moodtheme": "autotagging_moodtheme.tsv"}
N_TARGET = int(os.environ.get("N_TRACKS", "600"))
TOP_PER_CAT = {"genre": 25, "instrument": 15, "moodtheme": 15}
PER_TAG = int(os.environ.get("PER_TAG", "40"))
SEED = 13


def ensure(fn):
    p = WORK / fn
    if not p.exists():
        print("  downloading", fn)
        urllib.request.urlretrieve(f"{B}/{fn}", p)
    return p


def main():
    random.seed(SEED)
    track_tags: dict[str, set[str]] = collections.defaultdict(set)
    track_path: dict[str, str] = {}
    cat_counts = {c: collections.Counter() for c in CATS}

    for cat, fn in CATS.items():
        with open(ensure(fn)) as f:
            next(f)
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 6:
                    continue
                tid, path, tags = p[0], p[3], [t for t in p[5:] if t]
                track_path[tid] = path
                for t in tags:
                    track_tags[tid].add(t)
                    cat_counts[cat][t] += 1

    query_tags = {c: [t for t, _ in cat_counts[c].most_common(TOP_PER_CAT[c])]
                  for c in CATS}
    targets = [t for c in CATS for t in query_tags[c]]

    by_tag = collections.defaultdict(list)
    for tid, tags in track_tags.items():
        for t in tags:
            if t in targets:
                by_tag[t].append(tid)
    selected: set[str] = set()
    for t in sorted(targets, key=lambda t: len(by_tag[t])):
        pool = sorted(by_tag[t])
        random.shuffle(pool)
        for tid in pool[:PER_TAG]:
            selected.add(tid)
            if len(selected) >= N_TARGET:
                break
        if len(selected) >= N_TARGET:
            break

    manifest = []
    for tid in sorted(selected):
        path = track_path[tid]
        jid = int(os.path.splitext(os.path.basename(path))[0])
        manifest.append({"track_id": tid, "jamendo_id": jid, "path": path,
                         "tags": sorted(track_tags[tid])})

    with open(WORK / "manifest.jsonl", "w") as f:
        for m in manifest:
            f.write(json.dumps(m) + "\n")
    with open(WORK / "query_tags.json", "w") as f:
        json.dump(query_tags, f, indent=2)

    in_subset = collections.Counter()
    for m in manifest:
        for t in m["tags"]:
            if t in targets:
                in_subset[t] += 1
    print(f"selected {len(manifest)} tracks; {len(targets)} target query-tags -> {WORK}")
    for c in CATS:
        cov = [in_subset[t] for t in query_tags[c]]
        print(f"  {c:11}: {len(query_tags[c])} tags, relevant-in-subset "
              f"min/median/max = {min(cov)}/{sorted(cov)[len(cov)//2]}/{max(cov)}")


if __name__ == "__main__":
    main()
