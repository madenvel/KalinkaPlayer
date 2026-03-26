#!/usr/bin/env python3
"""
Standalone AI search diagnostic script.

Usage:
    python test_ai_search.py [query] [--limit N]

Defaults to a set of built-in test queries if no argument given.
"""

import argparse
import sys
import time

DB_PATH = "/home/envel/kalinka/localfiles.db"
QUERIES = ["melancholic", "jazz", "upbeat dance", "heavy metal", "calm acoustic"]

# ---------------------------------------------------------------------------

def load_vec(conn):
    import sqlite_vec
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)


def check_db(db):
    """Print DB state summary."""
    print("\n=== Database state ===")
    total = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    embedded = db.execute(
        "SELECT COUNT(*) FROM tracks WHERE embedding_clap_audio IS NOT NULL"
    ).fetchone()[0]
    print(f"  Tracks total:    {total}")
    print(f"  Tracks embedded: {embedded}  ({100*embedded//total if total else 0}%)")

    for table in ("vec_tracks_clap", "vec_albums_clap", "vec_artists_clap"):
        try:
            n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {n} rows")
        except Exception as e:
            print(f"  {table}: ERROR — {e}")

    rows = db.execute(
        "SELECT stage, status, COUNT(*) FROM embedding_jobs GROUP BY stage, status ORDER BY stage, status"
    ).fetchall()
    if rows:
        print("\n  Embedding jobs:")
        for stage, status, cnt in rows:
            print(f"    {stage:30s} {status:12s} {cnt}")
    print()


def load_clap():
    print("Loading CLAP model (laion/clap-htsat-unfused) ...")
    t0 = time.monotonic()
    import laion_clap
    model = laion_clap.CLAP_Module(enable_fusion=False)
    model.load_ckpt()
    elapsed = time.monotonic() - t0
    print(f"  Model loaded in {elapsed:.1f}s\n")
    return model


def encode_query(model, query: str):
    import numpy as np
    t0 = time.monotonic()
    embeddings = model.get_text_embedding([query], use_tensor=False)
    vec = np.array(embeddings[0], dtype=np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    elapsed = time.monotonic() - t0
    print(f"  Query encoded in {elapsed:.3f}s  (dim={len(vec)}, norm={np.linalg.norm(vec):.4f})")
    return vec.tobytes()


def knn_search(db, table: str, pk_col: str, blob: bytes, limit: int):
    t0 = time.monotonic()
    rows = db.execute(
        f"SELECT {pk_col}, distance FROM {table}"
        f" WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (blob, limit),
    ).fetchall()
    elapsed = time.monotonic() - t0
    print(f"  {table}: {len(rows)} results in {elapsed:.3f}s")
    return rows


def resolve_names(db, track_ids, album_ids, artist_ids):
    def fetch(table, id_col, name_col, ids):
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = db.execute(
            f"SELECT {id_col}, {name_col} FROM {table} WHERE {id_col} IN ({placeholders})",
            list(ids),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    tracks = fetch("tracks", "id", "title", track_ids)
    albums = fetch("albums", "id", "title", album_ids)
    artists = fetch("artists", "id", "name", artist_ids)
    return tracks, albums, artists


def run_query(db, model, query: str, limit: int):
    print(f"\n{'='*60}")
    print(f"Query: \"{query}\"")
    print(f"{'='*60}")

    blob = encode_query(model, query)

    track_rows  = knn_search(db, "vec_tracks_clap",  "track_id",  blob, limit)
    album_rows  = knn_search(db, "vec_albums_clap",  "album_id",  blob, limit)
    artist_rows = knn_search(db, "vec_artists_clap", "artist_id", blob, limit)

    track_ids  = [r[0] for r in track_rows]
    album_ids  = [r[0] for r in album_rows]
    artist_ids = [r[0] for r in artist_rows]

    names_t, names_al, names_ar = resolve_names(db, track_ids, album_ids, artist_ids)

    print("\n  Tracks:")
    for id_, dist in track_rows[:10]:
        print(f"    [{dist:.4f}]  {names_t.get(id_, '<unknown>')}  (id={id_})")

    print("\n  Albums:")
    for id_, dist in album_rows[:10]:
        print(f"    [{dist:.4f}]  {names_al.get(id_, '<unknown>')}  (id={id_})")

    print("\n  Artists:")
    for id_, dist in artist_rows[:10]:
        print(f"    [{dist:.4f}]  {names_ar.get(id_, '<unknown>')}  (id={id_})")


def main():
    parser = argparse.ArgumentParser(description="AI search diagnostic")
    parser.add_argument("query", nargs="?", help="Search query (omit to run all built-in queries)")
    parser.add_argument("--limit", type=int, default=10, help="KNN limit per table (default: 10)")
    args = parser.parse_args()

    import sqlite3
    print(f"Opening DB: {DB_PATH}")
    db = sqlite3.connect(DB_PATH)
    load_vec(db)
    check_db(db)

    model = load_clap()

    queries = [args.query] if args.query else QUERIES
    for q in queries:
        run_query(db, model, q, args.limit)

    db.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
