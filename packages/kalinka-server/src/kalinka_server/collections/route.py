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
from pydantic import BaseModel, Field

from .source import collection_id
from .store import CollectionStore

logger = logging.getLogger(__name__.split(".")[-1])

MAX_NAME = 200
MAX_DESCRIPTION = 2000


class NewCollection(BaseModel):
    """What making a collection takes: a name, and optionally a line about it."""

    name: str = Field(max_length=MAX_NAME)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)


class CollectionRef(BaseModel):
    """A collection the client can now go to: its browse id and its name.

    Deliberately not the whole item — the listing it appears in is refetched
    anyway, and its cover does not exist until something is in it.
    """

    id: str
    name: str


def register_collection_routes(app: FastAPI, store: CollectionStore) -> None:
    """Mount the collections write endpoints on `app`."""

    @app.post("/collections", status_code=201)
    async def create_collection(payload: NewCollection) -> CollectionRef:
        """Make an empty collection."""
        name = payload.name.strip()
        if not name:
            raise HTTPException(
                status_code=422,
                detail={"field": "name", "reason": "a collection needs a name"},
            )
        try:
            row = await store.create_collection(name, payload.description.strip())
        except RuntimeError as e:
            logger.error("Could not create collection: %s", e)
            raise HTTPException(status_code=503, detail="Collections unavailable")
        return CollectionRef(id=collection_id(row.id).to_string, name=row.name)
