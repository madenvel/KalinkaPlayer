"""Rows in a collections file, as the write API will one day leave them.

Shared by the store's tests and the source's: both need a collection that
already holds something, and the snapshot has to be a real ``Track`` or the
source is reading a shape it will never meet in production.
"""

import json
import time

import aiosqlite

from kalinka_plugin_sdk.datamodel import Album, Artist, EntityId, EntityType, Track


def track(source: str, local_id: str, title: str, artist: str, album: str, duration: int):
    track_id = EntityId(id=local_id, type=EntityType.TRACK, source=source)
    album_id = EntityId(id=f"al-{local_id}", type=EntityType.ALBUM, source=source)
    artist_id = EntityId(id=f"ar-{local_id}", type=EntityType.ARTIST, source=source)
    return Track(
        id=track_id,
        title=title,
        duration=duration,
        performer=Artist(id=artist_id, name=artist),
        album=Album(id=album_id, title=album),
    )


async def seed_collection(path, id, name, *, updated_at=0, description=""):
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO collections (id, name, description, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (id, name, description, updated_at, updated_at),
        )
        await db.commit()


async def seed_entry(
    path,
    collection_id,
    entry_id,
    *,
    position=0,
    source="qobuz",
    title="A Song",
    artist="A Band",
    album="An Album",
    duration=100,
    genres=(),
    track_json=None,
):
    snapshot = track(source, entry_id, title, artist, album, duration)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO entries (entry_id, collection_id, position, entity_id,"
            " source, title, artist, album, duration, track_json, added_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                collection_id,
                position,
                snapshot.id.to_string,
                source,
                title,
                artist,
                album,
                duration,
                track_json
                if track_json is not None
                else snapshot.model_dump_json(),
                int(time.time()),
            ),
        )
        for genre_id, genre_name in genres:
            await db.execute(
                "INSERT INTO entry_genres (entry_id, genre_id, genre_name)"
                " VALUES (?, ?, ?)",
                (entry_id, genre_id, genre_name),
            )
        await db.commit()


def unreadable_snapshot() -> str:
    """A snapshot no ``Track`` can be made of — schema drift, as a row."""
    return json.dumps({"id": "kalinka:qobuz:track:x", "title": "Gone"})
