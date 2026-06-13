# Jamendo Plugin for Kalinka Music Player

## Overview

An input-module plugin that lets you search, browse and play music from
[Jamendo](https://www.jamendo.com) — a catalogue of free, Creative-Commons /
royalty-free music — inside the Kalinka play queue, alongside local files and
other sources.

> **Disclaimer**: This plugin is an independent, community-developed integration
> and is not affiliated with or endorsed by Jamendo. It is provided "as is".
> Streaming via the Jamendo API is intended for personal use; commercial use of
> Jamendo tracks requires a licence from Jamendo Licensing.

## Configuration

| Setting        | Description                                                                 |
| -------------- | --------------------------------------------------------------------------- |
| `client_id`    | **Required.** Free Jamendo API key. Register an app at https://devportal.jamendo.com |
| `audio_format` | Streaming quality: MP3 VBR (default), MP3 96 kbps, OGG, or FLAC.             |

## Capabilities

Implemented:

- **Search** — tracks, albums, artists, playlists (Jamendo `namesearch`).
- **Browse** — a discovery root catalog (Popular Tracks, New Releases, Popular
  Albums, Popular Artists, Featured Playlists) and drill-down into albums,
  artists and playlists.
- **Playback** — `get_track_info` returns metadata plus the streaming URL in
  the configured format.
- **`get`** — detail lookup for a track / album / artist / playlist.

Intentionally **not** implemented:

- **Favourites** (`list_favorite`, `add_to_favorite`, …) and **playlist
  management** (`playlist_create`, …) — these require an OAuth2 user session,
  which is out of scope. They degrade to empty results / no-ops.
- **`list_genre`** — Jamendo has no genre taxonomy (it uses free-form tags), so
  this returns an empty list and catalogs disable genre filtering.
- **`ai_search`** — left at the SDK default (empty) until the semantic backend
  is wired up.

## Known limitations

- **No exact result totals.** The Jamendo API reports only the size of the
  current page, never a grand total. Reported totals are therefore estimates
  tuned to keep "load more" pagination working.
- **Free-tier rate limits.** The Jamendo API rate-limits requests per
  client_id; heavy browsing may be throttled.
- **FLAC is best-effort.** Lossless streams exist only for tracks whose artist
  enabled download; otherwise Jamendo serves a lossy stream.

## Building

```bash
./scripts/build_wheel.sh   # build the wheel (version from setuptools-scm)
./scripts/build_deb.sh     # build the .deb (wheel + Debian control)
```

Release versions come from a git tag of the form
`kalinka-plugin-jamendo-v1.2.3`; untagged builds produce a dev version.

## Testing

```bash
pytest            # smoke + offline mapping tests (no network/client_id needed)
```
