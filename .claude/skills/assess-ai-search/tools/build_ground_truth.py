#!/usr/bin/env python3
"""Build ground-truth JSONL for AI-search evaluation.

Joins tracks / albums / artists, parses tags_predicted, and attaches curated
per-artist labels from queries.json. Writes one record per track.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


def parse_predicted_tags(blob: str | None) -> tuple[list[str], list[str], int | None, float | None]:
    if not blob:
        return [], [], None, None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return [], [], None, None

    top: list[str] = []
    sub: list[str] = []
    for entry in data.get("genres") or []:
        label = (entry.get("label") or "").lower()
        if not label:
            continue
        if "---" in label:
            t, s = label.split("---", 1)
            top.append(t.strip())
            sub.append(s.strip())
        else:
            top.append(label.strip())

    # dedupe while preserving order
    top = list(dict.fromkeys(top))
    sub = list(dict.fromkeys(sub))

    mood = data.get("mood_cluster")
    dance = data.get("danceability")
    return top, sub, mood, dance


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db",      default=os.environ.get("KALINKA_DB", os.path.expanduser("~/kalinka/var/lib/kalinka/localfiles.db")))
    ap.add_argument("--queries", default=str(Path(__file__).resolve().parent.parent / "queries.json"))
    ap.add_argument("--out",     default="tmp/ai_search_eval/ground_truth.jsonl")
    args = ap.parse_args()

    queries = json.loads(Path(args.queries).read_text())
    artist_labels: dict[str, list[str]] = queries.get("artist_labels", {})

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
          t.id            AS track_id,
          t.title         AS title,
          ar.name         AS artist,
          al.title        AS album,
          LOWER(COALESCE(al.genre, '')) AS album_genre,
          al.year         AS year,
          t.tags_predicted AS tags_predicted,
          CASE WHEN t.embedding_clap_audio IS NOT NULL THEN 1 ELSE 0 END AS has_audio_emb,
          CASE WHEN t.embedding_clap_text  IS NOT NULL THEN 1 ELSE 0 END AS has_text_emb
        FROM tracks t
        LEFT JOIN artists ar ON ar.id = t.artist_id
        LEFT JOIN albums  al ON al.id = t.album_id
        """
    ).fetchall()

    written = 0
    with out_path.open("w") as fh:
        for row in rows:
            top, sub, mood, dance = parse_predicted_tags(row["tags_predicted"])
            artist_name = row["artist"] or ""
            record = {
                "track_id":             row["track_id"],
                "title":                row["title"],
                "artist":               artist_name,
                "album":                row["album"],
                "album_genre":          row["album_genre"],
                "year":                 row["year"],
                "predicted_top_genres": top,
                "predicted_subgenres":  sub,
                "mood_cluster":         mood,
                "danceability":         dance,
                "artist_labels":        artist_labels.get(artist_name, []),
                "has_audio_embedding":  bool(row["has_audio_emb"]),
                "has_text_embedding":   bool(row["has_text_emb"]),
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    conn.close()
    sys.stderr.write(f"Wrote {written} records to {out_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
