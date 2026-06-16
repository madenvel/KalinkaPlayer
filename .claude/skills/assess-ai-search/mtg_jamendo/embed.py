"""Transcode clips -> WAV (ffmpeg) and CLAP-embed with the PRODUCTION ONNX model.

Builds mtg_bench.db: a vec0 audio index + a tracks table carrying the official
MTG-Jamendo tags (ground truth). Same encoder the embedder/production search use.
libsndfile cannot decode a truncated MP3, so ffmpeg transcodes first.

Artifacts go to $MTG_BENCH_DIR (default <repo>/tmp/mtg_jamendo_eval).
Model files from $KALINKA_MODEL_DIR (default ~/kalinka/models).
"""
from __future__ import annotations
import importlib.util, json, os, subprocess, sys
from pathlib import Path
import numpy as np, sqlite_vec, sqlite3

REPO = Path(__file__).resolve().parents[4]
WORK = Path(os.environ.get("MTG_BENCH_DIR", REPO / "tmp" / "mtg_jamendo_eval"))
CLIPS = WORK / "clips"
DB = WORK / "mtg_bench.db"
MODEL_DIR = os.path.expanduser(os.environ.get("KALINKA_MODEL_DIR", "~/kalinka/models"))
CLAP_SRC = REPO / "packages/kalinka-plugin-localfiles/src/kalinka_plugin_localfiles/embedder/clap_onnx.py"
SECS = 12


def load_clap():
    spec = importlib.util.spec_from_file_location("clap_onnx_standalone", CLAP_SRC)
    m = importlib.util.module_from_spec(spec); sys.modules["clap_onnx_standalone"] = m; spec.loader.exec_module(m)
    model = m.ClapOnnxModel(model_dir=MODEL_DIR); model.load()
    assert model.is_loaded, f"ONNX CLAP failed to load from {MODEL_DIR}"
    return model


def to_wav(jid):
    mp3 = CLIPS / f"{jid}.mp3"
    wav = CLIPS / f"{jid}.wav"
    if wav.exists() and wav.stat().st_size > 1000:
        return str(wav)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-t", str(SECS),
                        "-ac", "1", "-ar", "48000", str(wav)], capture_output=True)
    return str(wav) if (r.returncode == 0 and wav.exists()) else None


def main():
    clap = load_clap()
    print(f"ONNX CLAP loaded from {MODEL_DIR}", file=sys.stderr)
    manifest = [json.loads(l) for l in open(WORK / "manifest.fetched.jsonl")]

    db = sqlite3.connect(DB)
    db.enable_load_extension(True); db.load_extension(sqlite_vec.loadable_path()); db.enable_load_extension(False)
    db.execute("DROP TABLE IF EXISTS tracks")
    db.execute("CREATE TABLE tracks (track_id TEXT PRIMARY KEY, jamendo_id INT, tags TEXT)")
    db.execute("DROP TABLE IF EXISTS vec_tracks_clap")
    db.execute("CREATE VIRTUAL TABLE vec_tracks_clap USING vec0(track_id TEXT PRIMARY KEY, embedding float[512])")

    ok = bad = 0
    for i, m in enumerate(manifest, 1):
        wav = to_wav(m["jamendo_id"])
        if not wav:
            bad += 1; continue
        vec = clap.get_audio_embedding(wav)
        if vec is None:
            bad += 1; continue
        v = np.asarray(vec, dtype=np.float32); n = float(np.linalg.norm(v))
        blob = (v / n if n > 0 else v).tobytes()
        db.execute("INSERT INTO tracks VALUES (?,?,?)", (m["track_id"], m["jamendo_id"], json.dumps(m["tags"])))
        db.execute("INSERT INTO vec_tracks_clap (track_id, embedding) VALUES (?,?)", (m["track_id"], blob))
        ok += 1
        if i % 100 == 0:
            db.commit(); print(f"  {i}/{len(manifest)} ok={ok} bad={bad}", file=sys.stderr)
    db.commit()
    n_vec = db.execute("SELECT COUNT(*) FROM vec_tracks_clap").fetchone()[0]
    print(f"embedded {ok} tracks (failed {bad}); vec rows={n_vec}; db={DB}")


if __name__ == "__main__":
    main()
