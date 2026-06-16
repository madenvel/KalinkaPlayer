"""Range-fetch MTG-Jamendo clips from the Jamendo public stream.

CLAP only reads the first ~10s, so we grab the leading ~440 KB (~37s @96kbps)
instead of full tracks. Skips existing, drops unavailable tracks, writes
manifest.fetched.jsonl with only successfully-fetched tracks.

Artifacts go to $MTG_BENCH_DIR (default <repo>/tmp/mtg_jamendo_eval).
"""
from __future__ import annotations
import json, os, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[4]
WORK = Path(os.environ.get("MTG_BENCH_DIR", REPO / "tmp" / "mtg_jamendo_eval"))
CLIPS = WORK / "clips"
RANGE = int(os.environ.get("CLIP_BYTES", "450000"))
LIMIT = int(os.environ.get("FETCH_LIMIT", "0"))  # 0 = all
URL = "https://mp3d.jamendo.com/?trackid={jid}&format=mp31"


def fetch(m):
    jid = m["jamendo_id"]
    dest = CLIPS / f"{jid}.mp3"
    if dest.exists() and dest.stat().st_size > 50000:
        return (m, True)
    try:
        r = requests.get(URL.format(jid=jid), headers={"Range": f"bytes=0-{RANGE}"},
                         timeout=60, stream=True)
        if r.status_code not in (200, 206):
            return (m, False)
        data = r.content
        if len(data) < 50000:
            return (m, False)
        dest.write_bytes(data)
        return (m, True)
    except Exception:
        return (m, False)


def main():
    CLIPS.mkdir(parents=True, exist_ok=True)
    manifest = [json.loads(l) for l in open(WORK / "manifest.jsonl")]
    if LIMIT:
        manifest = manifest[:LIMIT]
    ok, bad = [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, (m, good) in enumerate(ex.map(fetch, manifest), 1):
            (ok if good else bad).append(m)
            if i % 50 == 0:
                print(f"  {i}/{len(manifest)}  ok={len(ok)} bad={len(bad)}", file=sys.stderr)
    print(f"fetched {len(ok)}/{len(manifest)} (failed {len(bad)})")
    if not LIMIT:
        with open(WORK / "manifest.fetched.jsonl", "w") as f:
            for m in ok:
                f.write(json.dumps(m) + "\n")
        total = sum((CLIPS / f"{m['jamendo_id']}.mp3").stat().st_size for m in ok)
        print(f"  manifest.fetched.jsonl written; clips total {total/1e6:.0f} MB")


if __name__ == "__main__":
    main()
