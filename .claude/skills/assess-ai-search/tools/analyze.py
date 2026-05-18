#!/usr/bin/env python3
"""Post-hoc analysis of AI-search results: lift-over-random + recall-at-ceiling.

Reads a results.json (from evaluate.py) or results.endpoint.json (from
evaluate_endpoint.py) and the matching ground_truth.jsonl, then prints:
  - per-category aggregates with random baseline + lift
  - real wins (high lift)
  - corpus-skew artifacts (high P, low lift)
  - actual losses (negative lift)
  - recall-at-ceiling per category (fraction of achievable recall captured)

No CLAP / no DB access required — pure arithmetic on the existing files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_results(path: str) -> tuple[dict, str]:
    """Return (results_doc, schema) where schema is 'raw' (evaluate.py) or 'endpoint'."""
    doc = json.loads(Path(path).read_text())
    sample_cat = next(iter(doc["categories"].values()))
    sample_q = sample_cat["per_query"][0]
    if "indexes" in sample_q:
        return doc, "raw"
    if "topk_relevant" in sample_q:
        return doc, "endpoint"
    raise ValueError(f"Unrecognised results schema in {path}")


def iter_query_records(doc: dict, schema: str, index_name: str = "audio"):
    """Yield (category, query, total_relevant, n_rel_in_topk, p_at_k) per query."""
    k = doc.get("k", 10)
    for cat_name, cat in doc["categories"].items():
        for entry in cat["per_query"]:
            q = entry["query"]
            if schema == "raw":
                payload = entry["indexes"][index_name]
                total_rel = payload["total_relevant"]
                n_rel = sum(1 for h in payload["topk"] if h["relevant"])
                p = payload["precision_at_k"]
            else:
                total_rel = entry["total_relevant"]
                n_rel = sum(entry["topk_relevant"])
                p = entry["precision_at_k"]
            yield cat_name, q, total_rel, n_rel, p, k


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results",     default="tmp/ai_search_eval/results.endpoint.json")
    ap.add_argument("--truth",       default="tmp/ai_search_eval/ground_truth.jsonl")
    ap.add_argument("--index",       default="audio",
                    help="Which index to analyse if --results is a raw evaluate.py file (audio|text)")
    ap.add_argument("--lift-real",   type=float, default=0.50,
                    help="Threshold for 'real lift' wins")
    ap.add_argument("--lift-skew",   type=float, default=0.10,
                    help="Threshold below which a high-P result counts as corpus skew")
    args = ap.parse_args()

    doc, schema = load_results(args.results)
    N = sum(1 for _ in Path(args.truth).read_text().splitlines() if _.strip())
    print(f"Results: {args.results}  schema={schema}  library_size={N}")
    if schema == "raw":
        print(f"Analysing index: {args.index}")
    print()

    # Collect per-query records
    records = list(iter_query_records(doc, schema, args.index))

    # Per-category aggregates
    print(f"{'category':12s} | {'n':3s} | {'P@K':6s} | {'rand@K':7s} | {'lift':7s} | {'norm_lift':10s} | {'ceiling_R':10s} | {'unc_R':6s}")
    print("-" * 100)
    cats: dict[str, list] = {}
    for cat, q, rel, n_rel, p, k in records:
        cats.setdefault(cat, []).append((q, rel, n_rel, p, k))

    for cat, rows in cats.items():
        n_q = len(rows)
        ps  = [r[3] for r in rows]
        rands = [r[1] / N for r in rows]
        lifts = [p - rand for p, rand in zip(ps, rands)]
        norm_lifts = [(p - rand) / (1 - rand) if rand < 1 else 0.0
                      for p, rand in zip(ps, rands)]
        unc_recalls = [n / rel if rel else 0.0 for _, rel, n, _, _ in rows]
        ceilings    = [min(1.0, k / rel) if rel else 1.0 for _, rel, _, _, k in rows]
        ceiling_recalls = [u / c if c else 0.0 for u, c in zip(unc_recalls, ceilings)]
        print(f"  {cat:10s} | {n_q:3d} | {sum(ps)/n_q:.3f}  | {sum(rands)/n_q:.3f}   | {sum(lifts)/n_q:+.3f}  | {sum(norm_lifts)/n_q:+.3f}     | {sum(ceiling_recalls)/n_q:.3f}      | {sum(unc_recalls)/n_q:.3f}")

    # Overall
    n_q = len(records)
    ps    = [r[4] for r in records]
    rands = [r[2] / N for r in records]
    lifts = [p - rand for p, rand in zip(ps, rands)]
    norm_lifts = [(p - rand) / (1 - rand) if rand < 1 else 0.0
                  for p, rand in zip(ps, rands)]
    unc_recalls = [n / rel if rel else 0.0 for _, _, rel, n, _, _ in records]
    ceilings    = [min(1.0, k / rel) if rel else 1.0 for _, _, rel, _, _, k in records]
    ceiling_recalls = [u / c if c else 0.0 for u, c in zip(unc_recalls, ceilings)]
    print("-" * 100)
    print(f"  {'OVERALL':10s} | {n_q:3d} | {sum(ps)/n_q:.3f}  | {sum(rands)/n_q:.3f}   | {sum(lifts)/n_q:+.3f}  | {sum(norm_lifts)/n_q:+.3f}     | {sum(ceiling_recalls)/n_q:.3f}      | {sum(unc_recalls)/n_q:.3f}")

    # Per-query: real wins, corpus-skew artifacts, actual losses
    per_q = []
    for cat, q, rel, n_rel, p, k in records:
        rand = rel / N
        lift = p - rand
        per_q.append((cat, q, rel, n_rel, p, rand, lift, k))

    print(f"\n=== Real wins (lift ≥ {args.lift_real:+.2f}) ===")
    for r in sorted(per_q, key=lambda x: -x[6])[:15]:
        cat, q, rel, n_rel, p, rand, lift, k = r
        if lift < args.lift_real: break
        print(f"  lift={lift:+.2f}  P={p:.2f}  rand={rand:.2f}  total_rel={rel:3d}  [{cat:10s}] {q!r}")

    print(f"\n=== Corpus-skew artifacts (P ≥ 0.5 but lift < {args.lift_skew:+.2f}) ===")
    skew_rows = [r for r in per_q if r[4] >= 0.5 and r[6] < args.lift_skew]
    for r in sorted(skew_rows, key=lambda x: -x[5])[:15]:
        cat, q, rel, n_rel, p, rand, lift, k = r
        print(f"  P={p:.2f}  rand={rand:.2f}  lift={lift:+.2f}  total_rel={rel:3d}  [{cat:10s}] {q!r}")

    print(f"\n=== Actual losses (lift < 0) — model worse than random ===")
    for r in sorted(per_q, key=lambda x: x[6])[:15]:
        cat, q, rel, n_rel, p, rand, lift, k = r
        if lift >= 0: break
        print(f"  lift={lift:+.2f}  P={p:.2f}  rand={rand:.2f}  total_rel={rel:3d}  [{cat:10s}] {q!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
