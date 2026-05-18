#!/usr/bin/env python3
"""Evaluate the live /ai_search endpoint (full pipeline: FTS + KNN + re-rank).

Mirrors evaluate.py's metrics but hits the running server instead of querying
vec tables directly. Use this to measure what the user actually sees, after
the re-ranker weights have been applied.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Reuse the relevance predicate + metric helpers from evaluate.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import is_relevant, aggregate  # type: ignore


def fetch_tracks(endpoint: str, query: str, limit: int, timeout: float = 60.0) -> list[str]:
    """Return track IDs from the Tracks section of /ai_search, in order."""
    url = endpoint + "?" + urllib.parse.urlencode({"query": query, "limit": limit})
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.load(r)

    for section in data.get("items", []):
        if section.get("name") != "Tracks":
            continue
        out: list[str] = []
        for s in section.get("sections", []):
            tid = s.get("id", "")
            # IDs look like "kalinka:localfiles:track:track_xxxx" — strip the prefix
            if ":track:" in tid:
                tid = tid.split(":track:", 1)[1]
            out.append(tid)
        return out
    return []


def evaluate_query(query_obj: dict, truth_by_id: dict[str, dict], hit_ids: list[str], k: int) -> dict:
    rule = query_obj["rule"]
    total_relevant = sum(1 for t in truth_by_id.values() if is_relevant(t, rule))

    flags: list[bool] = []
    for tid in hit_ids[:k]:
        t = truth_by_id.get(tid)
        flags.append(bool(t and is_relevant(t, rule)))

    n_rel = sum(flags)
    precision = n_rel / k if k else 0.0
    recall_denom = min(k, total_relevant) if total_relevant else 0
    recall = (n_rel / recall_denom) if recall_denom else 0.0

    mrr = 0.0
    for i, f in enumerate(flags, start=1):
        if f:
            mrr = 1.0 / i
            break

    return {
        "query": query_obj["q"],
        "total_relevant": total_relevant,
        "n_returned": len(hit_ids),
        "n_topk": len(hit_ids[:k]),
        "topk_ids": hit_ids[:k],
        "topk_relevant": flags,
        "precision_at_k": precision,
        "recall_at_k": recall,
        "mrr": mrr,
        "hit": bool(n_rel),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://localhost:8000/ai_search")
    ap.add_argument("--queries",  default=str(Path(__file__).resolve().parent.parent / "queries.json"))
    ap.add_argument("--truth",    default="tmp/ai_search_eval/ground_truth.jsonl")
    ap.add_argument("--out",      default="tmp/ai_search_eval/results.endpoint.json")
    ap.add_argument("--k",        type=int, default=10)
    args = ap.parse_args()

    queries_doc = json.loads(Path(args.queries).read_text())
    truth_by_id = {json.loads(l)["track_id"]: json.loads(l)
                   for l in Path(args.truth).read_text().splitlines() if l.strip()}
    sys.stderr.write(f"Loaded {len(truth_by_id)} ground-truth tracks\n")

    out: dict = {"endpoint": args.endpoint, "k": args.k, "categories": {}}

    t_total = time.monotonic()
    for cat in queries_doc["categories"]:
        cat_name = cat["name"]
        out["categories"][cat_name] = {"per_query": [], "aggregates": {}}
        for q in cat["queries"]:
            t0 = time.monotonic()
            hits = fetch_tracks(args.endpoint, q["q"], args.k)
            dt = time.monotonic() - t0
            entry = evaluate_query(q, truth_by_id, hits, args.k)
            entry["latency_s"] = dt
            entry["rule"] = q["rule"]
            out["categories"][cat_name]["per_query"].append(entry)
        out["categories"][cat_name]["aggregates"] = aggregate(
            out["categories"][cat_name]["per_query"]
        )

    all_per = [e for cat in out["categories"].values() for e in cat["per_query"]]
    out["overall"] = aggregate(all_per)
    out["overall"]["mean_latency_s"] = sum(e["latency_s"] for e in all_per) / len(all_per)
    out["overall"]["mean_n_returned"] = sum(e["n_returned"] for e in all_per) / len(all_per)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
    sys.stderr.write(f"Wrote {args.out} in {time.monotonic() - t_total:.1f}s\n")

    ov = out["overall"]
    sys.stderr.write(
        f"[endpoint] P@{args.k}={ov['mean_p_at_k']:.3f}  "
        f"R@{args.k}={ov['mean_r_at_k']:.3f}  "
        f"MRR={ov['mean_mrr']:.3f}  "
        f"Hit@{args.k}={ov['hit_rate']:.3f}  "
        f"avg_latency={ov['mean_latency_s']*1000:.0f}ms  "
        f"avg_returned={ov['mean_n_returned']:.1f}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
