"""The collections write API.

Reading a collection goes through the browse plane like any other source, so
nothing here answers a listing. What lives here is everything that *changes*
one — the half that has no place in a source's contract, because no plugin
implements it.

A write cannot degrade the way a listing does. With the file unavailable a
listing answers empty and the app shows an empty shelf; a write must say so,
or the app reports a collection nobody made.
"""

import logging

from fastapi import FastAPI, HTTPException
from kalinka_plugin_sdk.datamodel import EntityId, EntityType
from pydantic import BaseModel, Field

from .source import SOURCE_NAME, collection_id
from .store import CollectionStore

logger = logging.getLogger(__name__.split(".")[-1])

MAX_NAME = 200
MAX_DESCRIPTION = 2000


class NewCollection(BaseModel):
    """What making a collection takes: a name, and optionally a line about it."""

    name: str = Field(max_length=MAX_NAME)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)


class CollectionEdit(BaseModel):
    """What renaming takes."""

    name: str = Field(max_length=MAX_NAME)


class CollectionRef(BaseModel):
    """A collection the client can now go to: its browse id and its name.

    Deliberately not the whole item — the listing it appears in is refetched
    anyway, and its cover does not exist until something is in it.
    """

    id: str
    name: str


def _required_name(raw: str) -> str:
    """The name a write asked for, refusing one that is only whitespace."""
    name = raw.strip()
    if not name:
        raise HTTPException(
            status_code=422,
            detail={"field": "name", "reason": "a collection needs a name"},
        )
    return name


def _local_id(entity_id: str) -> str:
    """The store's id behind a collection's browse id. An id belonging to
    anything else names nothing here, so it is a miss rather than a refusal."""
    try:
        parsed = EntityId.from_string(entity_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="No such collection")
    if parsed.source != SOURCE_NAME or parsed.type != EntityType.PLAYLIST:
        raise HTTPException(status_code=404, detail="No such collection")
    return parsed.id


def register_collection_routes(app: FastAPI, store: CollectionStore) -> None:
    """Mount the collections write endpoints on `app`."""

    @app.post("/collections", status_code=201)
    async def create_collection(payload: NewCollection) -> CollectionRef:
        """Make an empty collection."""
        name = _required_name(payload.name)
        try:
            row = await store.create_collection(name, payload.description.strip())
        except RuntimeError as e:
            logger.error("Could not create collection: %s", e)
            raise HTTPException(status_code=503, detail="Collections unavailable")
        return CollectionRef(id=collection_id(row.id).to_string, name=row.name)

    @app.patch("/collections/{entity_id}")
    async def rename_collection(
        entity_id: str, payload: CollectionEdit
    ) -> CollectionRef:
        """Give a collection another name."""
        name = _required_name(payload.name)
        local_id = _local_id(entity_id)
        try:
            row = await store.rename_collection(local_id, name)
        except RuntimeError as e:
            logger.error("Could not rename %s: %s", entity_id, e)
            raise HTTPException(status_code=503, detail="Collections unavailable")
        if row is None:
            raise HTTPException(status_code=404, detail="No such collection")
        return CollectionRef(id=collection_id(row.id).to_string, name=row.name)
