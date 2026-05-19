#!/usr/bin/env python3
"""Single-pass enrichment-quality scanner for localfiles.db.

Outputs:
  - findings.json  (stable schema, diff this across runs)
  - REPORT.md      (human-readable summary)

See ../SKILL.md for what each metric means and why.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Tuple

# ---------------------------------------------------------------------------
# Normalization & heuristics
# ---------------------------------------------------------------------------

_LEADING_PUNCT_RE = re.compile(r"^[\-_.,/ ]+")
_TRAILING_PUNCT_RE = re.compile(r"[\-_.,/ ]+$")
_MULTI_SPACE_RE = re.compile(r"  +")

# Heuristics for "this album title was almost certainly derived from a
# directory name, not a music tag". The disc-subdir token was removed —
# the indexer now strips ``CD1`` / ``Disc 2`` at the folder level, and a
# title that legitimately contains "CD 1" (compilation tag) is not an
# artifact.
_PATH_ARTIFACT_PATTERNS = [
    (re.compile(r"---"),                 "triple-hyphen"),
    (re.compile(r"\b(MP3|FLAC|WAV|WEBM|WEB|OGG|AAC|M4A)\b", re.I), "format-token"),
    (re.compile(r"\d{5,}"),              "long-digit-run"),
    (re.compile(r"^[\-_.,/ ]"),          "leading-punct"),
    (re.compile(r"[\-_.,/ ]$"),          "trailing-punct"),
    (re.compile(r"\bJamendo\b", re.I),   "jamendo-token"),
    (re.compile(r"\bBeatport\b", re.I),  "beatport-token"),
]


def normalize_artist(name: str) -> str:
    """Normalization that mirrors production ``normalize_for_id``.

    Kept inline rather than imported so the skill can run against a DB
    without needing the kalinka package on PYTHONPATH. The two
    implementations must stay in lockstep — if you change one, change
    both (and add a regression test in ``tests/test_name_normalization``
    if the divergence is meaningful).

    Steps:
      1. NFKD + strip combining marks (Beyoncé ≡ Beyonce)
      2. Lowercase
      3. Replace -_./ with space (Jean-Michel ≡ Jean Michel)
      4. Drop remaining non-word chars (P!nk ≡ Pnk)
      5. Collapse whitespace
    """
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.lower()
    n = re.sub(r"[\-_./]", " ", n)
    n = re.sub(r"[^\w\s]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def parent_dir(file_path: str) -> str:
    return os.path.dirname(file_path) if file_path else ""


def percentiles(values: List[float], pcts: Iterable[int]) -> Dict[str, float]:
    if not values:
        return {f"p{p}": None for p in pcts}
    s = sorted(values)
    out = {}
    for p in pcts:
        if p == 0:
            out["p0"] = float(s[0])
        elif p == 100:
            out["p100"] = float(s[-1])
        else:
            idx = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
            out[f"p{p}"] = float(s[idx])
    return out


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def scan(db_path: str) -> Dict[str, Any]:
    db_path = os.path.expanduser(db_path)
    if not os.path.exists(db_path):
        sys.exit(f"DB not found: {db_path}")

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    findings: Dict[str, Any] = {
        "db": db_path,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": {},
        "coverage": {},
        "artist_hygiene": {},
        "album_hygiene": {},
        "match_scores": {},
        "cross_entity": {},
        "duration": {},
    }

    # ---- raw counts ----
    findings["counts"] = {
        "artists": conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0],
        "albums":  conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0],
        "tracks":  conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
    }

    # ---- coverage ----
    def frac(num: int, denom: int) -> float:
        return round(num / denom, 4) if denom else 0.0

    n_artists = findings["counts"]["artists"]
    n_albums = findings["counts"]["albums"]
    n_tracks = findings["counts"]["tracks"]

    findings["coverage"] = {
        "artists_enriched": frac(
            conn.execute("SELECT COUNT(*) FROM artists WHERE enriched != 0").fetchone()[0], n_artists
        ),
        "artists_with_mbid": frac(
            conn.execute("SELECT COUNT(*) FROM artists WHERE mbid IS NOT NULL AND mbid != ''").fetchone()[0], n_artists
        ),
        "albums_enriched": frac(
            conn.execute("SELECT COUNT(*) FROM albums WHERE enriched != 0").fetchone()[0], n_albums
        ),
        "albums_with_mbid": frac(
            conn.execute("SELECT COUNT(*) FROM albums WHERE mbid IS NOT NULL AND mbid != ''").fetchone()[0], n_albums
        ),
        "albums_with_genre": frac(
            conn.execute("SELECT COUNT(*) FROM albums WHERE genre IS NOT NULL AND genre != ''").fetchone()[0], n_albums
        ),
        "albums_with_year": frac(
            conn.execute("SELECT COUNT(*) FROM albums WHERE year IS NOT NULL").fetchone()[0], n_albums
        ),
        "tracks_enriched": frac(
            conn.execute("SELECT COUNT(*) FROM tracks WHERE enriched != 0").fetchone()[0], n_tracks
        ),
        "tracks_with_mbid": frac(
            conn.execute("SELECT COUNT(*) FROM tracks WHERE mbid IS NOT NULL AND mbid != ''").fetchone()[0], n_tracks
        ),
        "tracks_with_duration": frac(
            conn.execute("SELECT COUNT(*) FROM tracks WHERE duration IS NOT NULL AND duration > 0").fetchone()[0], n_tracks
        ),
    }

    # ---- artist hygiene ----
    leading, trailing, multi_space = [], [], []
    norm_groups: Dict[str, List[Dict[str, str]]] = defaultdict(list)

    for row in conn.execute("SELECT id, name, mbid FROM artists WHERE id != 'unknown_artist'"):
        name = row["name"] or ""
        rec = {"id": row["id"], "name": name, "mbid": row["mbid"]}
        if _LEADING_PUNCT_RE.match(name):
            leading.append(rec)
        if _TRAILING_PUNCT_RE.search(name):
            trailing.append(rec)
        if _MULTI_SPACE_RE.search(name):
            multi_space.append(rec)
        norm_groups[normalize_artist(name)].append(rec)

    dup_groups = [
        {"normalized": k, "members": v}
        for k, v in sorted(norm_groups.items(), key=lambda kv: -len(kv[1]))
        if k and len(v) > 1
    ]

    findings["artist_hygiene"] = {
        "leading_punct": leading,
        "trailing_punct": trailing,
        "multi_space": multi_space,
        "probable_duplicates": dup_groups,
    }

    # ---- album hygiene ----
    path_artifact: List[Dict[str, Any]] = []
    for row in conn.execute(
        "SELECT id, title, artist_id, mbid, track_count FROM albums WHERE id != 'unknown_album'"
    ):
        title = row["title"] or ""
        hits = [tag for rx, tag in _PATH_ARTIFACT_PATTERNS if rx.search(title)]
        if hits:
            path_artifact.append(
                {
                    "id": row["id"],
                    "title": title,
                    "artist_id": row["artist_id"],
                    "track_count": row["track_count"],
                    "patterns": hits,
                }
            )

    # ---- folder splits & V/A candidates ----
    # Load (file_path, album_id, artist_id) once and aggregate in Python (sqlite
    # doesn't have reverse()/rfind on the version we ship).
    folder_to_albums: Dict[str, set] = defaultdict(set)
    folder_to_artists: Dict[str, set] = defaultdict(set)
    folder_to_tracks: Dict[str, int] = defaultdict(int)
    album_titles: Dict[str, str] = {}
    artist_names: Dict[str, str] = {}

    for row in conn.execute("SELECT id, title FROM albums"):
        album_titles[row["id"]] = row["title"] or ""
    for row in conn.execute("SELECT id, name FROM artists"):
        artist_names[row["id"]] = row["name"] or ""

    for row in conn.execute(
        "SELECT file_path, album_id, artist_id FROM tracks WHERE file_path IS NOT NULL"
    ):
        d = parent_dir(row["file_path"])
        if not d:
            continue
        folder_to_albums[d].add(row["album_id"])
        folder_to_artists[d].add(row["artist_id"])
        folder_to_tracks[d] += 1

    folder_splits: List[Dict[str, Any]] = []
    va_candidates: List[Dict[str, Any]] = []
    for d, albums in folder_to_albums.items():
        n_t = folder_to_tracks[d]
        n_artists_here = len(folder_to_artists[d])

        if len(albums) > 1:
            folder_splits.append(
                {
                    "parent_dir": d,
                    "track_count": n_t,
                    "album_count": len(albums),
                    "albums": [
                        {"id": aid, "title": album_titles.get(aid, "")}
                        for aid in sorted(albums)
                    ],
                }
            )

        if n_t >= 4 and n_artists_here >= max(4, int(0.75 * n_t)):
            va_candidates.append(
                {
                    "parent_dir": d,
                    "track_count": n_t,
                    "distinct_artists": n_artists_here,
                    "album_ids_in_folder": sorted(albums),
                }
            )

    folder_splits.sort(key=lambda x: -x["track_count"])
    va_candidates.sort(key=lambda x: -x["track_count"])

    findings["album_hygiene"] = {
        "path_artifact_titles": path_artifact,
        "folder_splits": folder_splits,
        "va_candidates": va_candidates,
    }

    # ---- match-score distributions ----
    def score_stats(table: str) -> Dict[str, Any]:
        rows = conn.execute(
            f"SELECT id, match_score, match_similarity FROM {table} "
            f"WHERE match_score IS NOT NULL"
        ).fetchall()
        scores = [float(r["match_score"]) for r in rows]
        sims = [float(r["match_similarity"]) for r in rows if r["match_similarity"] is not None]
        bottom = sorted(rows, key=lambda r: (r["match_score"], r["match_similarity"] or 0))[:10]
        return {
            "n_with_score": len(scores),
            "score": {
                "min": min(scores) if scores else None,
                "median": median(scores) if scores else None,
                "max": max(scores) if scores else None,
                **percentiles(scores, [10, 25, 50, 75, 90]),
            },
            "similarity": {
                "min": min(sims) if sims else None,
                "median": median(sims) if sims else None,
                "max": max(sims) if sims else None,
            },
            "bottom_10": [
                {
                    "id": r["id"],
                    "match_score": r["match_score"],
                    "match_similarity": r["match_similarity"],
                }
                for r in bottom
            ],
        }

    findings["match_scores"] = {
        "artists": score_stats("artists"),
        "albums":  score_stats("albums"),
        "tracks":  score_stats("tracks"),
    }

    # ---- cross-entity consistency ----
    enriched_albums_with_unmatched_tracks = conn.execute(
        """
        SELECT COUNT(DISTINCT a.id)
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        WHERE a.mbid IS NOT NULL AND a.mbid != ''
          AND (t.mbid IS NULL OR t.mbid = '')
        """
    ).fetchone()[0]

    tracks_matched_in_unmatched_albums = conn.execute(
        """
        SELECT COUNT(*)
        FROM tracks t
        LEFT JOIN albums a ON a.id = t.album_id
        WHERE t.mbid IS NOT NULL AND t.mbid != ''
          AND (a.mbid IS NULL OR a.mbid = '')
        """
    ).fetchone()[0]

    # Multi-artist albums anchored to a single artist row. Since the
    # album-ID change, this is no longer a "bug" by itself: a
    # legitimate V/A compilation has many artists but one album row,
    # and that's the *intended* shape. We split the signal into two
    # subcategories so the report distinguishes:
    #
    #   * mistagging artifacts — small albums (≤3 tracks) with 2+
    #     distinct artists. Almost always one or two mistagged tracks
    #     in an otherwise single-artist album. Was the original
    #     "Abbey Road" bug; should be near zero after a re-index.
    #
    #   * V/A compilations — albums with many tracks (≥4) and many
    #     distinct artists (≥4). Real compilations; flagged for the
    #     V/A coalescing fix that comes next.
    multi_artist_albums = conn.execute(
        """
        SELECT a.id, a.title, a.artist_id,
               COUNT(DISTINCT t.artist_id) AS n_track_artists,
               COUNT(*) AS n_tracks
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        WHERE a.id != 'unknown_album'
        GROUP BY a.id
        HAVING n_track_artists > 1
        ORDER BY n_tracks DESC
        """
    ).fetchall()

    # Albums already anchored to ``various_artists`` were handled by the
    # V/A coalescing pass and shouldn't appear in either failure bucket.
    mistagging_candidates = []
    va_candidates_cross = []
    properly_coalesced_va = 0
    for r in multi_artist_albums:
        if r["artist_id"] == "various_artists":
            properly_coalesced_va += 1
            continue
        rec = {
            "album_id": r["id"],
            "title": r["title"],
            "anchor_artist_id": r["artist_id"],
            "distinct_track_artists": r["n_track_artists"],
            "track_count": r["n_tracks"],
        }
        if r["n_tracks"] <= 3 and r["n_track_artists"] >= 2:
            mistagging_candidates.append(rec)
        elif r["n_tracks"] >= 4 and r["n_track_artists"] >= 4:
            va_candidates_cross.append(rec)

    findings["cross_entity"] = {
        "enriched_albums_with_unmatched_tracks": enriched_albums_with_unmatched_tracks,
        "tracks_matched_in_unmatched_albums": tracks_matched_in_unmatched_albums,
        "mistagging_candidates": mistagging_candidates,
        "va_albums_to_coalesce": va_candidates_cross,
        "properly_coalesced_va_albums": properly_coalesced_va,
    }

    # ---- duration signal ----
    findings["duration"] = {
        "tracks_with_duration": frac(
            conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE duration IS NOT NULL AND duration > 0"
            ).fetchone()[0],
            n_tracks,
        ),
        "tracks_matched_with_duration": conn.execute(
            """
            SELECT COUNT(*) FROM tracks
            WHERE mbid IS NOT NULL AND mbid != ''
              AND duration IS NOT NULL AND duration > 0
            """
        ).fetchone()[0],
        "albums_with_total_duration": conn.execute(
            "SELECT COUNT(*) FROM albums WHERE duration IS NOT NULL AND duration > 0"
        ).fetchone()[0],
    }

    conn.close()
    return findings


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%" if x is not None else "—"


def render_markdown(findings: Dict[str, Any]) -> str:
    c = findings["counts"]
    cov = findings["coverage"]
    ah = findings["artist_hygiene"]
    bh = findings["album_hygiene"]
    ms = findings["match_scores"]
    ce = findings["cross_entity"]
    dur = findings["duration"]

    lines: List[str] = []
    lines.append(f"# Enrichment quality — {findings['generated_at']}")
    lines.append("")
    lines.append(f"**DB:** `{findings['db']}`  ")
    lines.append(
        f"**Library:** {c['artists']} artists · {c['albums']} albums · {c['tracks']} tracks"
    )
    lines.append("")

    # Coverage
    lines.append("## 1. Coverage")
    lines.append("")
    lines.append("| Entity | Enriched | With MBID | Other |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| Artists | {fmt_pct(cov['artists_enriched'])} | {fmt_pct(cov['artists_with_mbid'])} | — |"
    )
    lines.append(
        f"| Albums  | {fmt_pct(cov['albums_enriched'])} | {fmt_pct(cov['albums_with_mbid'])} | "
        f"genre {fmt_pct(cov['albums_with_genre'])}, year {fmt_pct(cov['albums_with_year'])} |"
    )
    lines.append(
        f"| Tracks  | {fmt_pct(cov['tracks_enriched'])} | {fmt_pct(cov['tracks_with_mbid'])} | "
        f"duration {fmt_pct(cov['tracks_with_duration'])} |"
    )
    lines.append("")

    # Artist hygiene
    lines.append("## 2. Artist hygiene")
    lines.append("")
    lines.append(f"- Leading punctuation: **{len(ah['leading_punct'])}**")
    lines.append(f"- Trailing punctuation: **{len(ah['trailing_punct'])}**")
    lines.append(f"- Multi-space in name: **{len(ah['multi_space'])}**")
    lines.append(f"- Probable duplicate groups: **{len(ah['probable_duplicates'])}**")
    lines.append("")
    if ah["leading_punct"][:5]:
        lines.append("**Leading-punct examples**")
        for a in ah["leading_punct"][:5]:
            lines.append(f"- `{a['name']}` ({a['id']})")
        lines.append("")
    if ah["probable_duplicates"][:10]:
        lines.append("**Duplicate-artist groups (top 10)**")
        for g in ah["probable_duplicates"][:10]:
            names = " | ".join(m["name"] for m in g["members"])
            lines.append(f"- `{g['normalized']}` → {names}")
        lines.append("")

    # Album hygiene
    lines.append("## 3. Album hygiene")
    lines.append("")
    lines.append(
        f"- Path-artifact titles: **{len(bh['path_artifact_titles'])}**"
    )
    lines.append(f"- Folder splits (one parent dir → ≥2 albums): **{len(bh['folder_splits'])}**")
    lines.append(f"- V/A compilation candidates: **{len(bh['va_candidates'])}**")
    lines.append("")
    if bh["path_artifact_titles"][:5]:
        lines.append("**Path-artifact title examples**")
        for r in bh["path_artifact_titles"][:5]:
            lines.append(
                f"- `{r['title']}` (patterns: {', '.join(r['patterns'])}, tracks: {r['track_count']})"
            )
        lines.append("")
    if bh["folder_splits"][:5]:
        lines.append("**Folder-split examples**")
        for r in bh["folder_splits"][:5]:
            titles = " | ".join(a["title"] for a in r["albums"])
            lines.append(
                f"- `{r['parent_dir']}` → {r['album_count']} albums ({r['track_count']} tracks): {titles}"
            )
        lines.append("")
    if bh["va_candidates"][:5]:
        lines.append("**V/A candidates** (one folder, many artists)")
        for r in bh["va_candidates"][:5]:
            lines.append(
                f"- `{r['parent_dir']}` — {r['track_count']} tracks across {r['distinct_artists']} artists"
            )
        lines.append("")

    # Match score
    lines.append("## 4. Match-score distribution")
    lines.append("")
    lines.append("| Entity | n | min | p10 | median | p90 | max |")
    lines.append("|---|---|---|---|---|---|---|")
    for ent in ("artists", "albums", "tracks"):
        s = ms[ent]["score"]
        lines.append(
            f"| {ent} | {ms[ent]['n_with_score']} | {s['min']} | {s.get('p10')} | "
            f"{s['median']} | {s.get('p90')} | {s['max']} |"
        )
    lines.append("")
    for ent in ("tracks",):
        if ms[ent]["bottom_10"]:
            lines.append(f"**Bottom-10 {ent} matches (lowest score):**")
            for r in ms[ent]["bottom_10"]:
                lines.append(
                    f"- {r['id']}: score={r['match_score']}, similarity={r['match_similarity']}"
                )
            lines.append("")

    # Cross-entity
    lines.append("## 5. Cross-entity consistency")
    lines.append("")
    lines.append(
        f"- Enriched albums with ≥1 unmatched track: **{ce['enriched_albums_with_unmatched_tracks']}**"
    )
    lines.append(
        f"- Tracks matched in unmatched albums: **{ce['tracks_matched_in_unmatched_albums']}**"
    )
    lines.append(
        f"- Mistagging candidates (small albums w/ mixed artists): "
        f"**{len(ce['mistagging_candidates'])}**"
    )
    lines.append(
        f"- V/A compilations to coalesce (≥4 tracks, ≥4 distinct artists): "
        f"**{len(ce['va_albums_to_coalesce'])}**"
    )
    lines.append(
        f"- Properly coalesced V/A albums (anchored to `various_artists`): "
        f"**{ce.get('properly_coalesced_va_albums', 0)}**"
    )
    if ce["mistagging_candidates"][:5]:
        lines.append("")
        lines.append("**Mistagging candidate examples:**")
        for r in ce["mistagging_candidates"][:5]:
            lines.append(
                f"- `{r['title']}` — {r['track_count']} tracks across "
                f"{r['distinct_track_artists']} artists"
            )
    if ce["va_albums_to_coalesce"][:5]:
        lines.append("")
        lines.append("**V/A coalesce candidates:**")
        for r in ce["va_albums_to_coalesce"][:5]:
            lines.append(
                f"- `{r['title']}` — {r['track_count']} tracks across "
                f"{r['distinct_track_artists']} artists"
            )
    lines.append("")

    # Duration
    lines.append("## 6. Duration signal")
    lines.append("")
    lines.append(
        f"- Tracks with a populated duration: **{fmt_pct(dur['tracks_with_duration'])}**"
    )
    lines.append(
        f"- Matched tracks (have mbid) with a populated duration: "
        f"**{dur['tracks_matched_with_duration']}**"
    )
    lines.append(
        f"- Albums with total duration computed: **{dur['albums_with_total_duration']}**"
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db",
        default=os.environ.get("KALINKA_DB", "/home/envel/kalinka/localfiles.db"),
        help="Path to localfiles.db (default: $KALINKA_DB or ~/kalinka/localfiles.db)",
    )
    ap.add_argument(
        "--out",
        default="tmp/enrichment_assess",
        help="Output directory (default: tmp/enrichment_assess)",
    )
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    findings = scan(args.db)

    (out_dir / "findings.json").write_text(json.dumps(findings, indent=2, sort_keys=False))
    (out_dir / "REPORT.md").write_text(render_markdown(findings))

    # One-line headline for stdout / CI
    cov = findings["coverage"]
    ah = findings["artist_hygiene"]
    bh = findings["album_hygiene"]
    ce = findings["cross_entity"]
    print(
        f"artists={findings['counts']['artists']} "
        f"albums={findings['counts']['albums']} "
        f"tracks={findings['counts']['tracks']} | "
        f"alb_mbid={fmt_pct(cov['albums_with_mbid'])} "
        f"trk_mbid={fmt_pct(cov['tracks_with_mbid'])} | "
        f"artist_dups={len(ah['probable_duplicates'])} "
        f"path_titles={len(bh['path_artifact_titles'])} "
        f"folder_splits={len(bh['folder_splits'])} "
        f"mistag={len(ce['mistagging_candidates'])} "
        f"va_coalesce={len(ce['va_albums_to_coalesce'])}"
    )
    print(f"Wrote {out_dir/'findings.json'} and {out_dir/'REPORT.md'}")


if __name__ == "__main__":
    main()
