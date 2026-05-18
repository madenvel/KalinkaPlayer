#!/usr/bin/env python3
"""Evaluate Kalinka AI search precision / recall / MRR / hit-rate.

For each query in queries.json, encode it with CLAP's text encoder, run KNN
against `vec_tracks_clap` (audio side) and/or `vec_tracks_clap_text` (text
side), then score the top-K results against the ground-truth rule.

Writes a JSON result file. The skill consumes it to produce REPORT.md.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# CLAP text encoder loader
# ---------------------------------------------------------------------------

def load_clap_encoder() -> Callable[[str], bytes]:
    """Return a callable str -> 512-float L2-normalised bytes blob.

    Prefers laion_clap (matches test_ai_search.py). Falls back to the project's
    ONNX wrapper if laion_clap is missing.
    """
    import numpy as np

    try:
        import laion_clap  # type: ignore
        sys.stderr.write("Loading CLAP via laion_clap (laion/clap-htsat-unfused)...\n")
        t0 = time.monotonic()
        model = laion_clap.CLAP_Module(enable_fusion=False)
        model.load_ckpt()
        sys.stderr.write(f"  loaded in {time.monotonic() - t0:.1f}s\n")

        def encode(text: str) -> bytes:
            vec = model.get_text_embedding([text], use_tensor=False)[0]
            v = np.asarray(vec, dtype=np.float32)
            n = float(np.linalg.norm(v))
            if n > 0:
                v = v / n
            return v.tobytes()

        return encode
    except ImportError:
        pass

    # ONNX fallback
    sys.stderr.write("laion_clap not available — falling back to ONNX wrapper\n")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]
                           / "packages" / "kalinka-plugin-localfiles" / "src"))
    from kalinka_plugin_localfiles.embedder.clap_onnx import ClapOnnxModel  # type: ignore

    model_dir = os.environ.get("KALINKA_MODEL_DIR", "/var/lib/kalinka/models")
    model = ClapOnnxModel(model_dir=model_dir)
    model.load()

    def encode(text: str) -> bytes:
        vec = model.get_text_embedding([text], use_tensor=False)[0]
        v = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(v))
        if n > 0:
            v = v / n
        return v.tobytes()

    return encode


# ---------------------------------------------------------------------------
# sqlite-vec loader + KNN
# ---------------------------------------------------------------------------

def open_db(path: str) -> sqlite3.Connection:
    import sqlite_vec  # type: ignore
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)
    return conn


def knn(conn: sqlite3.Connection, table: str, blob: bytes, k: int) -> list[tuple[str, float]]:
    rows = conn.execute(
        f"SELECT track_id, distance FROM {table}"
        " WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (blob, k),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


# ---------------------------------------------------------------------------
# Relevance rule
# ---------------------------------------------------------------------------

def is_relevant(track: dict, rule: dict) -> bool:
    combine = rule.get("_combine", "OR").upper()

    checks: list[bool] = []

    if "pred_top_genre_in" in rule:
        wanted = {x.lower() for x in rule["pred_top_genre_in"]}
        actual = {x.lower() for x in track.get("predicted_top_genres") or []}
        checks.append(bool(wanted & actual))

    if "pred_subgenre_in" in rule:
        wanted = [x.lower() for x in rule["pred_subgenre_in"]]
        actual = " | ".join(track.get("predicted_subgenres") or []).lower()
        checks.append(any(w in actual for w in wanted))

    if "album_genre_contains" in rule:
        wanted = [x.lower() for x in rule["album_genre_contains"]]
        actual = (track.get("album_genre") or "").lower()
        checks.append(any(w in actual for w in wanted))

    if "artist_label_in" in rule:
        wanted = {x.lower() for x in rule["artist_label_in"]}
        actual = {x.lower() for x in track.get("artist_labels") or []}
        checks.append(bool(wanted & actual))

    if "artist_in" in rule:
        wanted = {x.lower() for x in rule["artist_in"]}
        actual = (track.get("artist") or "").lower()
        checks.append(actual in wanted)

    if "mood_cluster_in" in rule:
        wanted = set(rule["mood_cluster_in"])
        actual = track.get("mood_cluster")
        checks.append(actual is not None and actual in wanted)

    if "danceability_min" in rule:
        d = track.get("danceability")
        checks.append(d is not None and d >= rule["danceability_min"])

    if "danceability_max" in rule:
        d = track.get("danceability")
        checks.append(d is not None and d <= rule["danceability_max"])

    if "year_min" in rule:
        y = track.get("year")
        checks.append(y is not None and y >= rule["year_min"])

    if "year_max" in rule:
        y = track.get("year")
        checks.append(y is not None and y <= rule["year_max"])

    if not checks:
        return False
    return all(checks) if combine == "AND" else any(checks)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def evaluate_query(
    query_obj: dict,
    truth_by_id: dict[str, dict],
    hits: list[tuple[str, float]],
    k: int,
) -> dict:
    rule = query_obj["rule"]

    total_relevant = sum(1 for t in truth_by_id.values() if is_relevant(t, rule))

    relevant_flags: list[bool] = []
    rel_distances: list[float] = []
    non_rel_distances: list[float] = []
    for tid, dist in hits[:k]:
        t = truth_by_id.get(tid)
        if t is None:
            relevant_flags.append(False)
            non_rel_distances.append(dist)
            continue
        ok = is_relevant(t, rule)
        relevant_flags.append(ok)
        (rel_distances if ok else non_rel_distances).append(dist)

    n_rel_in_topk = sum(relevant_flags)
    precision = n_rel_in_topk / k if k else 0.0
    recall_denom = min(k, total_relevant) if total_relevant else 0
    recall = (n_rel_in_topk / recall_denom) if recall_denom else 0.0
    hit = bool(n_rel_in_topk)

    mrr = 0.0
    for i, flag in enumerate(relevant_flags, start=1):
        if flag:
            mrr = 1.0 / i
            break

    return {
        "query":              query_obj["q"],
        "total_relevant":     total_relevant,
        "topk":               [
            {"track_id": tid, "distance": dist, "relevant": flag, "label": _label(truth_by_id.get(tid))}
            for (tid, dist), flag in zip(hits[:k], relevant_flags)
        ],
        "precision_at_k":     precision,
        "recall_at_k":        recall,
        "mrr":                mrr,
        "hit":                hit,
        "rel_distance_med":   statistics.median(rel_distances) if rel_distances else None,
        "nonrel_distance_med":statistics.median(non_rel_distances) if non_rel_distances else None,
    }


def _label(track: dict | None) -> str:
    if not track:
        return "<unknown>"
    return f"{track.get('artist','?')} — {track.get('title','?')}"


def aggregate(per_query_results: list[dict]) -> dict:
    n = len(per_query_results)
    if n == 0:
        return {}
    return {
        "n_queries":    n,
        "mean_p_at_k":  sum(r["precision_at_k"] for r in per_query_results) / n,
        "mean_r_at_k":  sum(r["recall_at_k"]    for r in per_query_results) / n,
        "mean_mrr":     sum(r["mrr"]            for r in per_query_results) / n,
        "hit_rate":     sum(1 for r in per_query_results if r["hit"]) / n,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

INDEX_TABLES = {
    "audio": "vec_tracks_clap",
    "text":  "vec_tracks_clap_text",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db",      default=os.environ.get("KALINKA_DB", "/home/envel/kalinka/localfiles.db"))
    ap.add_argument("--queries", default=str(Path(__file__).resolve().parent.parent / "queries.json"))
    ap.add_argument("--truth",   default="tmp/ai_search_eval/ground_truth.jsonl")
    ap.add_argument("--out",     default="tmp/ai_search_eval/results.json")
    ap.add_argument("--k",       type=int, default=10)
    ap.add_argument("--indexes", default="audio,text", help="comma-separated subset of {audio,text}")
    args = ap.parse_args()

    queries_doc = json.loads(Path(args.queries).read_text())
    truth_by_id: dict[str, dict] = {}
    for line in Path(args.truth).read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        truth_by_id[rec["track_id"]] = rec
    sys.stderr.write(f"Loaded {len(truth_by_id)} ground-truth tracks\n")

    indexes = [i.strip() for i in args.indexes.split(",") if i.strip()]
    for i in indexes:
        if i not in INDEX_TABLES:
            sys.stderr.write(f"Unknown index '{i}' — must be one of {list(INDEX_TABLES)}\n")
            return 2

    encode = load_clap_encoder()
    conn = open_db(args.db)

    out: dict[str, Any] = {
        "db":      args.db,
        "k":       args.k,
        "indexes": indexes,
        "categories": {},
    }

    for cat in queries_doc["categories"]:
        cat_name = cat["name"]
        out["categories"][cat_name] = {"per_query": [], "aggregates": {}}
        for q in cat["queries"]:
            blob = encode(q["q"])
            entry: dict[str, Any] = {"query": q["q"], "rule": q["rule"], "indexes": {}}
            for idx in indexes:
                table = INDEX_TABLES[idx]
                hits = knn(conn, table, blob, args.k)
                entry["indexes"][idx] = evaluate_query(q, truth_by_id, hits, args.k)
            out["categories"][cat_name]["per_query"].append(entry)

        for idx in indexes:
            per = [e["indexes"][idx] for e in out["categories"][cat_name]["per_query"]]
            out["categories"][cat_name]["aggregates"][idx] = aggregate(per)

    out["overall"] = {}
    for idx in indexes:
        all_per: list[dict] = []
        for cat in out["categories"].values():
            for e in cat["per_query"]:
                all_per.append(e["indexes"][idx])
        out["overall"][idx] = aggregate(all_per)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
    sys.stderr.write(f"Wrote {args.out}\n")

    for idx in indexes:
        ov = out["overall"][idx]
        sys.stderr.write(
            f"[{idx:5s}] P@{args.k}={ov['mean_p_at_k']:.3f}  "
            f"R@{args.k}={ov['mean_r_at_k']:.3f}  "
            f"MRR={ov['mean_mrr']:.3f}  "
            f"Hit@{args.k}={ov['hit_rate']:.3f}\n"
        )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
