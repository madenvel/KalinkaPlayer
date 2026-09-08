"""Where collections live: one SQLite file the server owns.

A collection holds tracks from any number of sources, so the row keeps the
track as it was when it was added. That snapshot is what a listing renders
from: a collection reads back whole while a source is down, disabled or
uninstalled, and only playback needs the source itself.

The columns beside the snapshot exist to be queried — narrowing by text,
source or genre is SQL here rather than a filter pass over a loaded list.
Stated in this layer's own terms (:class:`EntryFilter`), so the query layer
stays a query layer and the module above translates once.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

import aiosqlite

logger = logging.getLogger(__name__.split(".")[-1])


@dataclass(frozen=True)
class CollectionRow:
    """One collection, with the totals a listing shows under its name."""

    id: str
    name: str
    description: str
    created_at: int
    updated_at: int
    track_count: int
    duration: int


@dataclass(frozen=True)
class EntryRow:
    """One track's place in a collection, and the snapshot taken when it was
    added. ``entry_id`` is what a write addresses — a track may appear twice
    and its position may change."""

    entry_id: str
    entity_id: str
    position: int
    track_json: str


@dataclass(frozen=True)
class NewEntry:
    """A track on its way into a collection: the columns a listing queries,
    beside the snapshot it will be read back from."""

    entity_id: str
    source: str
    title: str
    artist: str
    album: str
    duration: int
    track_json: str
    genres: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AddOutcome:
    """What an add left behind: the rows it wrote, and the ones it did not
    because the collection already held that track."""

    added: int
    already_there: int


@dataclass(frozen=True)
class ReplaceOutcome:
    """What a replace left behind: the rows the collection now holds, and the
    ones it dropped to make room."""

    added: int
    dropped: int


@dataclass(frozen=True)
class EntryFilter:
    """What a browse asked a collection's tracks to narrow to.

    Every constraint is a union within itself and an intersection across the
    three: text AND one of the sources AND one of the genres.
    """

    text: str = ""
    sources: Tuple[str, ...] = ()
    genres: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FacetValue:
    """One value a facet offers, with how many entries carry it."""

    id: str
    name: str
    count: int


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS collections (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entries (
        entry_id TEXT PRIMARY KEY,
        collection_id TEXT NOT NULL
            REFERENCES collections (id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        entity_id TEXT NOT NULL,
        source TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        artist TEXT NOT NULL DEFAULT '',
        album TEXT NOT NULL DEFAULT '',
        duration INTEGER NOT NULL DEFAULT 0,
        track_json TEXT NOT NULL,
        added_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entry_genres (
        entry_id TEXT NOT NULL REFERENCES entries (entry_id) ON DELETE CASCADE,
        genre_id TEXT NOT NULL,
        genre_name TEXT NOT NULL,
        PRIMARY KEY (entry_id, genre_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_entries_place ON entries (collection_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_entry_genres_genre ON entry_genres (genre_id)",
)

# Counted rather than carried, so a collection's totals cannot drift from its
# entries.
_TRACK_COUNT = "(SELECT COUNT(*) FROM entries e WHERE e.collection_id = c.id)"
_DURATION = (
    "(SELECT COALESCE(SUM(e.duration), 0) FROM entries e WHERE e.collection_id = c.id)"
)


def _placeholders(values: Sequence[str]) -> str:
    return ", ".join("?" * len(values))


class CollectionStore:
    """The collections file: what a listing reads back, and what a write
    leaves behind.

    One connection for the process: SQLite serialises writers anyway, and a
    connection per request would buy nothing at this size. Open it once at
    startup and close it at shutdown.
    """

    def __init__(self, path: str):
        self._path = path
        self._db: Optional[aiosqlite.Connection] = None
        self._opened = False

    async def open(self) -> None:
        """Create the file and its schema if absent, then hold the connection.

        A file that cannot be opened leaves the store unavailable rather than
        raising: collections are one source among several, and losing them
        must not cost the server its startup — and with it playback.
        """
        self._opened = True
        db = None
        try:
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            db = await aiosqlite.connect(self._path)
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            for statement in _SCHEMA:
                await db.execute(statement)
            await db.commit()
        except Exception as e:
            logger.error("Collections unavailable (%s): %s", self._path, e)
            # A connection that failed on its first statement still owns a
            # thread; leaving it would outlive the store.
            if db is not None:
                await db.close()
            self._db = None
            return
        self._db = db
        logger.info("Collections store ready at %s", self._path)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def is_good(self) -> bool:
        """Whether the file behind this store can be read."""
        return self._db is not None

    def _readable(self) -> bool:
        """False when the file could not be opened — a listing then answers
        empty. Never opening the store at all is a wiring bug, and says so.
        """
        if not self._opened:
            raise RuntimeError("Collection store used before open()")
        return self.is_good()

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Collection store is unavailable")
        return self._db

    async def create_collection(
        self, name: str, description: str = ""
    ) -> CollectionRow:
        """Add an empty collection and return it.

        Unlike a listing, a write cannot degrade quietly: a file that will not
        open raises, so the caller answers "unavailable" rather than reporting
        a collection nobody made.
        """
        if not self._readable():
            raise RuntimeError("Collections file is unavailable")
        now = int(time.time())
        row = CollectionRow(
            id=uuid.uuid4().hex[:12],
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
            track_count=0,
            duration=0,
        )
        await self._conn.execute(
            "INSERT INTO collections (id, name, description, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (row.id, row.name, row.description, row.created_at, row.updated_at),
        )
        await self._conn.commit()
        logger.info("Created collection %s (%s)", row.id, row.name)
        return row

    async def rename_collection(
        self, collection_id: str, name: str
    ) -> Optional[CollectionRow]:
        """Give a collection another name and return it as it now reads, or
        None when there is no such collection.

        A rename is a change to the collection like any other, so it moves to
        the front of a listing ordered by when things last changed.
        """
        if not self._readable():
            raise RuntimeError("Collections file is unavailable")
        cursor = await self._conn.execute(
            "UPDATE collections SET name = ?, updated_at = ? WHERE id = ?",
            (name, int(time.time()), collection_id),
        )
        await self._conn.commit()
        if cursor.rowcount == 0:
            return None
        logger.info("Renamed collection %s to %s", collection_id, name)
        return await self.get_collection(collection_id)

    async def add_entries(
        self,
        collection_id: str,
        entries: Sequence[NewEntry],
        allow_duplicates: bool = False,
    ) -> Optional[AddOutcome]:
        """Append ``entries`` to a collection and say how that went, or None
        when there is no such collection.

        A track the collection already holds is left out unless
        ``allow_duplicates`` says otherwise, and a batch that names one twice
        counts as holding it from its first row on.
        """
        if not self._readable():
            raise RuntimeError("Collections file is unavailable")
        if await self.get_collection(collection_id) is None:
            return None

        held: Optional[Set[str]] = None
        if not allow_duplicates:
            cursor = await self._conn.execute(
                "SELECT entity_id FROM entries WHERE collection_id = ?",
                (collection_id,),
            )
            held = {row["entity_id"] for row in await cursor.fetchall()}

        cursor = await self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM entries WHERE collection_id = ?",
            (collection_id,),
        )
        row = await cursor.fetchone()

        now = int(time.time())
        added = await self._insert(
            collection_id, entries, int(row[0]) + 1, held, now
        )
        await self._touch(collection_id, now)
        logger.info("Added %d tracks to collection %s", added, collection_id)
        return AddOutcome(added=added, already_there=len(entries) - added)

    async def replace_entries(
        self,
        collection_id: str,
        entries: Sequence[NewEntry],
        allow_duplicates: bool = False,
    ) -> Optional[ReplaceOutcome]:
        """Make a collection hold exactly ``entries``, or None when there is
        no such collection.

        Nothing survives to be duplicated, so ``allow_duplicates`` decides
        only whether a batch naming a track twice writes it twice.

        Emptying and refilling are one transaction: a write that failed
        halfway would leave the collection holding neither what it had nor
        what was asked for.
        """
        if not self._readable():
            raise RuntimeError("Collections file is unavailable")
        if await self.get_collection(collection_id) is None:
            return None

        cursor = await self._conn.execute(
            "DELETE FROM entries WHERE collection_id = ?", (collection_id,)
        )
        dropped = cursor.rowcount
        now = int(time.time())
        held: Optional[Set[str]] = None if allow_duplicates else set()
        added = await self._insert(collection_id, entries, 0, held, now)
        await self._touch(collection_id, now)
        logger.info(
            "Collection %s now holds %d tracks, dropping %d",
            collection_id,
            added,
            dropped,
        )
        return ReplaceOutcome(added=added, dropped=dropped)

    async def _insert(
        self,
        collection_id: str,
        entries: Sequence[NewEntry],
        position: int,
        held: Optional[Set[str]],
        now: int,
    ) -> int:
        """Write ``entries`` from ``position`` on and answer how many rows
        that came to.

        ``held`` names what the collection must not end up holding twice, and
        grows as rows are written; None writes every entry as it comes.
        Uncommitted: the caller decides what else belongs in the transaction.
        """
        added = 0
        for entry in entries:
            if held is not None:
                if entry.entity_id in held:
                    continue
                held.add(entry.entity_id)
            entry_id = uuid.uuid4().hex
            await self._conn.execute(
                "INSERT INTO entries (entry_id, collection_id, position, entity_id,"
                " source, title, artist, album, duration, track_json, added_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id,
                    collection_id,
                    position,
                    entry.entity_id,
                    entry.source,
                    entry.title,
                    entry.artist,
                    entry.album,
                    entry.duration,
                    entry.track_json,
                    now,
                ),
            )
            for genre_id, genre_name in entry.genres:
                await self._conn.execute(
                    "INSERT OR IGNORE INTO entry_genres (entry_id, genre_id,"
                    " genre_name) VALUES (?, ?, ?)",
                    (entry_id, genre_id, genre_name),
                )
            position += 1
            added += 1
        return added

    async def _touch(self, collection_id: str, now: int) -> None:
        """Mark the collection changed and commit what led here."""
        await self._conn.execute(
            "UPDATE collections SET updated_at = ? WHERE id = ?",
            (now, collection_id),
        )
        await self._conn.commit()

    async def list_collections(
        self, offset: int = 0, limit: int = 50, text: str = ""
    ) -> Tuple[List[CollectionRow], int]:
        """Collections, most recently changed first, optionally narrowed by
        name. Empty while the file is unavailable."""
        if not self._readable():
            return [], 0
        where, params = _name_where(text)
        total = await self._count(f"SELECT COUNT(*) FROM collections c{where}", params)
        cursor = await self._conn.execute(
            f"SELECT c.*, {_TRACK_COUNT} AS track_count, {_DURATION} AS duration"
            f" FROM collections c{where}"
            " ORDER BY c.updated_at DESC, c.name ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        rows = await cursor.fetchall()
        return [_collection(row) for row in rows], total

    async def get_collection(self, collection_id: str) -> Optional[CollectionRow]:
        if not self._readable():
            return None
        cursor = await self._conn.execute(
            f"SELECT c.*, {_TRACK_COUNT} AS track_count, {_DURATION} AS duration"
            " FROM collections c WHERE c.id = ?",
            (collection_id,),
        )
        row = await cursor.fetchone()
        return _collection(row) if row is not None else None

    async def list_entries(
        self,
        collection_id: str,
        offset: int = 0,
        limit: int = 50,
        listing: EntryFilter = EntryFilter(),
    ) -> Tuple[List[EntryRow], int]:
        """A collection's tracks in their own order, narrowed by ``listing``."""
        if not self._readable():
            return [], 0
        where, params = _entry_where(collection_id, listing)
        total = await self._count(f"SELECT COUNT(*) FROM entries e{where}", params)
        cursor = await self._conn.execute(
            f"SELECT e.entry_id, e.entity_id, e.position, e.track_json FROM entries e"
            f"{where} ORDER BY e.position ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        rows = await cursor.fetchall()
        return [
            EntryRow(
                entry_id=row["entry_id"],
                entity_id=row["entity_id"],
                position=row["position"],
                track_json=row["track_json"],
            )
            for row in rows
        ], total

    async def sources_by_collection(
        self, collection_ids: Sequence[str]
    ) -> Dict[str, List[str]]:
        """Which sources each of ``collection_ids`` draws on, most represented
        first — what a listing shows beside every name, in one query."""
        if not collection_ids or not self._readable():
            return {}
        cursor = await self._conn.execute(
            "SELECT collection_id, source, COUNT(*) AS n FROM entries"
            f" WHERE collection_id IN ({_placeholders(collection_ids)})"
            " GROUP BY collection_id, source ORDER BY collection_id, n DESC, source",
            tuple(collection_ids),
        )
        rows = await cursor.fetchall()
        by_collection: Dict[str, List[str]] = {}
        for row in rows:
            by_collection.setdefault(row["collection_id"], []).append(row["source"])
        return by_collection

    async def sources_of(self, collection_id: str) -> List[FacetValue]:
        """The sources this collection draws on, most represented first."""
        if not self._readable():
            return []
        cursor = await self._conn.execute(
            "SELECT source, COUNT(*) AS n FROM entries WHERE collection_id = ?"
            " GROUP BY source ORDER BY n DESC, source ASC",
            (collection_id,),
        )
        rows = await cursor.fetchall()
        return [
            FacetValue(id=row["source"], name=row["source"], count=row["n"])
            for row in rows
        ]

    async def genres_of(self, collection_id: str) -> List[FacetValue]:
        """The genres this collection's tracks carry, most common first. A
        genre named differently by two sources keeps the commoner spelling."""
        if not self._readable():
            return []
        cursor = await self._conn.execute(
            "SELECT g.genre_id, COUNT(*) AS n,"
            "       (SELECT g2.genre_name FROM entry_genres g2"
            "         JOIN entries e2 ON e2.entry_id = g2.entry_id"
            "        WHERE g2.genre_id = g.genre_id AND e2.collection_id = ?"
            "        GROUP BY g2.genre_name ORDER BY COUNT(*) DESC, g2.genre_name"
            "        LIMIT 1) AS genre_name"
            "  FROM entry_genres g JOIN entries e ON e.entry_id = g.entry_id"
            " WHERE e.collection_id = ?"
            " GROUP BY g.genre_id ORDER BY n DESC, genre_name ASC",
            (collection_id, collection_id),
        )
        rows = await cursor.fetchall()
        return [
            FacetValue(id=row["genre_id"], name=row["genre_name"], count=row["n"])
            for row in rows
        ]

    async def _count(self, sql: str, params: Sequence) -> int:
        cursor = await self._conn.execute(sql, tuple(params))
        row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0


def _collection(row: aiosqlite.Row) -> CollectionRow:
    return CollectionRow(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        track_count=row["track_count"],
        duration=row["duration"],
    )


def _name_where(text: str) -> Tuple[str, List[str]]:
    tokens = text.split()
    if not tokens:
        return "", []
    clauses = " AND ".join(["c.name LIKE ? ESCAPE '\\'"] * len(tokens))
    return f" WHERE {clauses}", [_contains(token) for token in tokens]


def _entry_where(collection_id: str, listing: EntryFilter) -> Tuple[str, List]:
    clauses = ["e.collection_id = ?"]
    params: List = [collection_id]

    for token in listing.text.split():
        clauses.append(
            "(e.title LIKE ? ESCAPE '\\' OR e.artist LIKE ? ESCAPE '\\'"
            " OR e.album LIKE ? ESCAPE '\\')"
        )
        params.extend([_contains(token)] * 3)

    if listing.sources:
        clauses.append(f"e.source IN ({_placeholders(listing.sources)})")
        params.extend(listing.sources)

    if listing.genres:
        clauses.append(
            "EXISTS (SELECT 1 FROM entry_genres g WHERE g.entry_id = e.entry_id"
            f" AND g.genre_id IN ({_placeholders(listing.genres)}))"
        )
        params.extend(listing.genres)

    return f" WHERE {' AND '.join(clauses)}", params


def _contains(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
