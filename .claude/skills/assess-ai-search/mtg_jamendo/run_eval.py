"""Run the canonical MTG-Jamendo benchmark.

Encode each query with the PRODUCTION ONNX text encoder, KNN the audio index
(vec_tracks_clap), score top-K against official tags. Reports P@K, MRR,
hit-rate, and lift-over-random per category + overall; writes results.json.

Artifacts go to $MTG_BENCH_DIR (default <repo>/tmp/mtg_jamendo_eval).
Model files from $KALINKA_MODEL_DIR (default ~/kalinka/models).
"""
from __future__ import annotations
import importlib.util, json, os, sys
from pathlib import Path
import numpy as np, sqlite_vec, sqlite3

REPO = Path(__file__).resolve().parents[4]
WORK = Path(os.environ.get("MTG_BENCH_DIR", REPO / "tmp" / "mtg_jamendo_eval"))
DB = WORK / "mtg_bench.db"
MODEL_DIR = os.path.expanduser(os.environ.get("KALINKA_MODEL_DIR", "~/kalinka/models"))
CLAP_SRC = REPO / "packages/kalinka-plugin-localfiles/src/kalinka_plugin_localfiles/embedder/clap_onnx.py"
K = int(os.environ.get("EVAL_K", "10"))


def load_clap():
    spec = importlib.util.spec_from_file_location("clap_onnx_standalone", CLAP_SRC)
    m = importlib.util.module_from_spec(spec); sys.modules["clap_onnx_standalone"] = m; spec.loader.exec_module(m)
    model = m.ClapOnnxModel(model_dir=MODEL_DIR); model.load(); return model


def main():
    clap = load_clap()
    queries = json.load(open(WORK / "queries.json"))["categories"]
    db = sqlite3.connect(DB)
    db.enable_load_extension(True); db.load_extension(sqlite_vec.loadable_path()); db.enable_load_extension(False)
    db.row_factory = sqlite3.Row

    track_tags = {r["track_id"]: set(json.loads(r["tags"])) for r in db.execute("SELECT track_id, tags FROM tracks")}
    N = len(track_tags)

    out = {"db": str(DB), "k": K, "n_corpus": N, "categories": {}, "overall": {}}
    agg = {"p": [], "mrr": [], "hit": [], "lift": []}

    for cat, qs in queries.items():
        recs = []
        for q in qs:
            tag = q["tag"]
            total_rel = sum(1 for t in track_tags.values() if tag in t)
            vec = clap.get_text_embedding(q["q"])
            v = np.asarray(vec, dtype=np.float32); n = float(np.linalg.norm(v))
            blob = (v / n if n > 0 else v).tobytes()
            hits = db.execute("SELECT track_id, distance FROM vec_tracks_clap WHERE embedding MATCH ? ORDER BY distance LIMIT ?", (blob, K)).fetchall()
            flags = [tag in track_tags.get(h["track_id"], set()) for h in hits]
            nrel = sum(flags)
            p = nrel / K
            mrr = next((1.0 / i for i, f in enumerate(flags, 1) if f), 0.0)
            rand = total_rel / N if N else 0.0
            recs.append({"q": q["q"], "tag": tag, "total_relevant": total_rel,
                         "p": p, "mrr": mrr, "hit": bool(nrel), "rand": rand, "lift": p - rand,
                         "top": [{"d": round(h["distance"], 3), "rel": f, "id": h["track_id"]} for h, f in zip(hits, flags)]})
            if total_rel > 0:
                for k_, val in (("p", p), ("mrr", mrr), ("hit", bool(nrel)), ("lift", p - rand)):
                    agg[k_].append(val)
        out["categories"][cat] = recs

    def cmean(recs, key):
        v = [r[key] for r in recs if r["total_relevant"] > 0]
        return sum(v) / len(v) if v else 0.0

    for cat, recs in out["categories"].items():
        nn = sum(1 for r in recs if r["total_relevant"] > 0)
        out["overall"].setdefault("_per_cat", {})[cat] = {
            "n": nn, "p": cmean(recs, "p"), "mrr": cmean(recs, "mrr"),
            "hit": cmean(recs, "hit"), "lift": cmean(recs, "lift"),
            "rand": sum(r["rand"] for r in recs if r["total_relevant"] > 0) / max(1, nn)}
    n = len(agg["p"]) or 1
    out["overall"]["all"] = {"n": len(agg["p"]), "p": sum(agg["p"]) / n, "mrr": sum(agg["mrr"]) / n,
                             "hit": sum(agg["hit"]) / n, "lift": sum(agg["lift"]) / n}

    json.dump(out, open(WORK / "results.json", "w"), indent=2)
    o = out["overall"]
    print(f"corpus={N} tracks, K={K}, queries scored={o['all']['n']}  (results -> {WORK/'results.json'})")
    print(f"{'category':11} | {'n':>2} | {'P@K':>5} {'rand':>5} {'lift':>6} {'MRR':>5} {'hit':>5}")
    print("-" * 56)
    for cat, c in o["_per_cat"].items():
        print(f"{cat:11} | {c['n']:>2} | {c['p']:.3f} {c['rand']:.3f} {c['lift']:+.3f} {c['mrr']:.3f} {c['hit']:.3f}")
    print("-" * 56)
    a = o["all"]
    print(f"{'OVERALL':11} | {a['n']:>2} | {a['p']:.3f} {'':>5} {a['lift']:+.3f} {a['mrr']:.3f} {a['hit']:.3f}")


if __name__ == "__main__":
    main()
