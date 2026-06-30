"""MTG-Jamendo benchmark WITH the production mood (valence/arousal) leg.

Same corpus / queries / encoder as run_eval.py, but ranks two ways and prints a
before/after table:

  * baseline  — pure CLAP text->audio KNN (what run_eval.py measures)
  * va        — CLAP candidates UNIONED with a (V,A) mood leg, blended exactly
                like production: final = (1 - w*conf)*clap + (w*conf)*mood

The mood path mirrors searcher.SearchWorker._query_to_va +
searcher._do_search's mood block + searcher_db.knn_search_mood, using the
deployed va_head_v{N}.onnx (per-track V,A from the stored embedding) and
mood_index_v{N}.npz (query->target V,A). Config knobs use the production
defaults (MoodConfig / SearchConfig in config_model.py).

Writes results_va.json. Env: MTG_BENCH_DIR, KALINKA_MODEL_DIR, EVAL_K,
VA_ARTIFACT_DIR (dir holding va_head_v*.onnx + mood_index_v*.npz; auto-located
under the model dirs if unset).
"""
from __future__ import annotations
import importlib.util, json, math, os, re, sys
from pathlib import Path
import numpy as np, sqlite_vec, sqlite3

REPO = Path(__file__).resolve().parents[4]
WORK = Path(os.environ.get("MTG_BENCH_DIR", REPO / "tmp" / "mtg_jamendo_eval"))
DB = WORK / "mtg_bench.db"
MODEL_DIR = os.path.expanduser(os.environ.get("KALINKA_MODEL_DIR", "~/kalinka/models"))
CLAP_SRC = REPO / "packages/kalinka-plugin-localfiles/src/kalinka_plugin_localfiles/embedder/clap_onnx.py"
K = int(os.environ.get("EVAL_K", "10"))

# --- production defaults (config_model.py: MoodConfig / SearchConfig) ---------
MOOD_ENABLED = True
MOOD_WEIGHT = 0.6
MOOD_CANDIDATES = 200
MOOD_NN_FALLBACK = True
MOOD_NN_THRESHOLD = 0.3
MOOD_NN_TOP_K = 3
KNN_CANDIDATE_LIMIT = 50

# --- int8 storage format (embedding_utils.py) --------------------------------
CLAP_INT8_CAP = 0.25
CLAP_INT8_SCALE = 127.0 / CLAP_INT8_CAP

# Filler words ignored when scoring mood purity (searcher.py _FILLER_WORDS).
_FILLER_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "by", "with", "from", "into", "my", "me", "i", "you", "your", "we", "us",
    "it", "its", "this", "that", "these", "those", "some", "something",
    "anything", "like", "want", "need", "give", "play", "playing", "song",
    "songs", "music", "track", "tracks", "tune", "tunes", "sound", "sounds",
    "playlist", "vibe", "vibes", "mood", "feeling", "feel", "get", "got", "im",
    "am", "are", "is", "be", "now", "tonight", "today", "day", "night", "time",
    "really", "very", "more", "bit", "little", "kinda", "sorta", "stuff",
})


def _i8_roundtrip(v):
    """encode_embedding -> decode_embedding (production stores int8)."""
    q = np.clip(np.round(v * CLAP_INT8_SCALE), -127.0, 127.0).astype(np.int8)
    return q.astype(np.float32) / CLAP_INT8_SCALE


def load_clap():
    src_root = str(REPO / "packages/kalinka-plugin-localfiles/src")
    if src_root not in sys.path:
        sys.path.insert(0, src_root)
    from kalinka_plugin_localfiles.embedder.clap_onnx import ClapOnnxModel
    model = ClapOnnxModel(model_dir=MODEL_DIR); model.load(); return model


def _find_artifact(stem):
    """Locate va_head_v*.onnx / mood_index_v*.npz across the model dirs."""
    dirs = []
    if os.environ.get("VA_ARTIFACT_DIR"):
        dirs.append(os.path.expanduser(os.environ["VA_ARTIFACT_DIR"]))
    dirs += [MODEL_DIR, os.path.expanduser("~/kalinka/var/lib/kalinka/models")]
    for d in dirs:
        hits = sorted(Path(d).glob(stem)) if os.path.isdir(d) else []
        if hits:
            return str(hits[-1])
    raise FileNotFoundError(f"{stem} not found under {dirs}")


def query_to_va(query, q_i8, words, va, emb):
    """searcher.SearchWorker._query_to_va — (target (V,A), confidence)."""
    tokens = set(re.findall(r"[a-z]+", query.lower()))
    hits = [i for i, w in enumerate(words) if w in tokens]
    if hits:  # 1) literal mood word(s)
        tv = float(np.mean([va[i][0] for i in hits]))
        ta = float(np.mean([va[i][1] for i in hits]))
        matched = {words[i] for i in hits}
        content = [t for t in tokens if len(t) >= 3 and t not in _FILLER_WORDS]
        share = sum(t in matched for t in content) / len(content) if content else 1.0
        return (tv, ta), float(share)
    if not MOOD_NN_FALLBACK or q_i8 is None:  # 2) CLAP-text NN fallback
        return None, 0.0
    qn = float(np.linalg.norm(q_i8))
    if qn == 0.0:
        return None, 0.0
    sims = emb @ (q_i8 / qn)
    order = np.argsort(-sims)[:MOOD_NN_TOP_K]
    top_cos = float(sims[order[0]])
    if top_cos < MOOD_NN_THRESHOLD:
        return None, 0.0
    w = np.clip(sims[order], 0.0, None)
    if w.sum() <= 0:
        return None, 0.0
    tv = float((va[order, 0] * w).sum() / w.sum())
    ta = float((va[order, 1] * w).sum() / w.sum())
    denom = 1.0 - MOOD_NN_THRESHOLD
    conf = (top_cos - MOOD_NN_THRESHOLD) / denom if denom > 0 else 1.0
    return (tv, ta), float(min(1.0, max(0.0, conf)))


def metrics(top_ids, tag, track_tags, total_rel, N):
    flags = [tag in track_tags.get(t, set()) for t in top_ids]
    nrel = sum(flags)
    p = nrel / K
    mrr = next((1.0 / i for i, f in enumerate(flags, 1) if f), 0.0)
    rand = total_rel / N if N else 0.0
    return {"p": p, "mrr": mrr, "hit": bool(nrel), "rand": rand, "lift": p - rand}


def main():
    import onnxruntime as ort

    clap = load_clap()
    va_sess = ort.InferenceSession(_find_artifact("va_head_v*.onnx"))
    d = np.load(_find_artifact("mood_index_v*.npz"), allow_pickle=True)
    words = [str(w) for w in d["words"]]
    mood_va = d["va"].astype(np.float32)
    mood_emb = d["text_emb"].astype(np.float32)

    db = sqlite3.connect(DB)
    db.enable_load_extension(True); db.load_extension(sqlite_vec.loadable_path())
    db.enable_load_extension(False); db.row_factory = sqlite3.Row

    track_tags = {r["track_id"]: set(json.loads(r["tags"]))
                  for r in db.execute("SELECT track_id, tags FROM tracks")}
    N = len(track_tags)

    # Per-track (V,A): stored embedding -> int8 roundtrip -> deployed head, exactly
    # like embedder._compute_va (decode_embedding -> get_valence_arousal).
    track_va = {}
    rows = db.execute("SELECT track_id, embedding FROM vec_tracks_clap").fetchall()
    embs = np.stack([_i8_roundtrip(np.frombuffer(r["embedding"], dtype=np.float32))
                     for r in rows])
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    embs = np.where(norms > 0, embs / norms, embs).astype(np.float32)
    out_va = va_sess.run(None, {"embedding": embs})[0]
    for r, va in zip(rows, out_va):
        track_va[r["track_id"]] = (float(va[0]), float(va[1]))

    queries = json.load(open(WORK / "queries.json"))["categories"]
    out = {"db": str(DB), "k": K, "n_corpus": N, "categories": {}}
    mood_fired = 0

    for cat, qs in queries.items():
        recs = []
        for q in qs:
            tag, qtext = q["tag"], q["q"]
            total_rel = sum(1 for t in track_tags.values() if tag in t)

            qf = np.asarray(clap.get_text_embedding(qtext), dtype=np.float32)
            qn = float(np.linalg.norm(qf))
            qf = qf / qn if qn > 0 else qf
            blob = qf.tobytes()

            # CLAP KNN leg (top KNN_CANDIDATE_LIMIT) over the float audio index.
            knn = db.execute(
                "SELECT track_id, distance FROM vec_tracks_clap "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (blob, KNN_CANDIDATE_LIMIT)).fetchall()
            ids = list(dict.fromkeys(h["track_id"] for h in knn))
            dists = [h["distance"] for h in knn]
            dmin, dmax = min(dists), max(dists)
            drange = (dmax - dmin) or 1.0
            knn_map = {h["track_id"]: 1.0 - (h["distance"] - dmin) / drange for h in knn}

            base_top = [tid for tid, _ in sorted(
                knn_map.items(), key=lambda kv: -kv[1])[:K]]

            # --- mood leg (searcher._do_search) ---
            q_i8 = _i8_roundtrip(qf)
            target, conf = (None, 0.0)
            mood_weight = 0.0
            mood_map = {}
            if MOOD_ENABLED:
                target, conf = query_to_va(qtext, q_i8, words, mood_va, mood_emb)
                if target is not None and conf > 0.0:
                    mood_weight = MOOD_WEIGHT * conf
                    cand = sorted(
                        ((math.dist(target, va), tid) for tid, va in track_va.items()),
                        key=lambda x: x[0])[:MOOD_CANDIDATES]
                    ids = list(dict.fromkeys(ids + [tid for _, tid in cand]))
                    dd = {tid: math.dist(target, track_va[tid])
                          for tid in ids if tid in track_va}
                    if dd:
                        mn, mx = min(dd.values()), max(dd.values())
                        rng = (mx - mn) or 1.0
                        mood_map = {tid: 1.0 - (v - mn) / rng for tid, v in dd.items()}

            if mood_weight > 0.0:
                mood_fired += 1
                scored = []
                for tid in ids:
                    s = knn_map.get(tid, 0.0)
                    s = (1.0 - mood_weight) * s + mood_weight * mood_map.get(tid, 0.0)
                    scored.append((s, tid))
                va_top = [tid for _, tid in sorted(scored, key=lambda st: -st[0])[:K]]
            else:
                va_top = base_top

            recs.append({
                "q": qtext, "tag": tag, "total_relevant": total_rel,
                "mood_conf": round(conf, 3), "mood_weight": round(mood_weight, 3),
                "target_va": [round(target[0], 2), round(target[1], 2)] if target else None,
                "base": metrics(base_top, tag, track_tags, total_rel, N),
                "va": metrics(va_top, tag, track_tags, total_rel, N),
            })
        out["categories"][cat] = recs

    def agg(recs, mode, key):
        v = [r[mode][key] for r in recs if r["total_relevant"] > 0]
        return sum(v) / len(v) if v else 0.0

    out["summary"] = {}
    allrecs = [r for recs in out["categories"].values() for r in recs]
    for cat, recs in list(out["categories"].items()) + [("OVERALL", allrecs)]:
        nn = sum(1 for r in recs if r["total_relevant"] > 0)
        out["summary"][cat] = {
            "n": nn,
            "base": {k: agg(recs, "base", k) for k in ("p", "mrr", "hit", "lift")},
            "va": {k: agg(recs, "va", k) for k in ("p", "mrr", "hit", "lift")},
            "rand": sum(r["base"]["rand"] for r in recs if r["total_relevant"] > 0) / max(1, nn),
        }

    json.dump(out, open(WORK / "results_va.json", "w"), indent=2)

    print(f"corpus={N} tracks, K={K}, mood leg fired on {mood_fired} queries  "
          f"(results -> {WORK/'results_va.json'})")
    print(f"\n{'category':11} | {'n':>2} | {'P@K base->va':>14} | "
          f"{'lift base->va':>17} | {'MRR base->va':>14}")
    print("-" * 70)
    for cat, s in out["summary"].items():
        b, v = s["base"], s["va"]
        print(f"{cat:11} | {s['n']:>2} | {b['p']:.3f} -> {v['p']:.3f}   | "
              f"{b['lift']:+.3f} -> {v['lift']:+.3f}   | "
              f"{b['mrr']:.3f} -> {v['mrr']:.3f}")


if __name__ == "__main__":
    main()
