# Collections — design

A collection is a list of tracks the user assembled themselves, from any number of sources at once. Nothing else in Kalinka spans sources: an album belongs to the source that has it, a playlist belongs to the service that hosts it, and the library is the files on this machine. A collection is the one place where a Qobuz track, a Jamendo track and a local file sit in one order.

This document covers what a collection is, where it lives, and how it is reached. Reading is implemented on both sides, and so is the first write — making one. §7 is the editing that is still to come and §8 the rest of the write API it needs; the `EDIT` that starts it is drawn and disabled.

## 1. Decisions

### 1.1 A collection is a playlist

It is `EntityType.PLAYLIST`, under a source named `collections`. No new entity type: the app already renders playlist rows, unrolls them inline, searches them by name and offers the type as a filter, and every one of those works for a collection with no code that knows what a collection is. A fifth type would have meant touching each of those surfaces to teach it a concept the user does not distinguish — they made a list, and lists are playlists.

### 1.2 The server owns them, not localfiles

localfiles is the local-files library, and is likely to become a thin module over a media server. A list that spans sources cannot belong to one of them. It also cannot reuse localfiles' playlist tables, whose rows are foreign keys into the local track table — the one shape a mixed list can never take. Those tables and the playlist write methods go away with the write API (§7).

### 1.3 Read is a source; write is its own API

Reading a collection is browsing, so collections are a *browse source* (§2) and every read surface reaches them for free: the browse root, the Discover shelf, name search, the merged playlist listing, the filter vocabularies. Writing is not: a collection is the only editable thing in the system, so routing its writes through a contract every plugin implements would have every plugin declare methods only one of them means. The write API stands on its own (§7), and the read contract shrinks to what a built-in can honestly answer.

### 1.4 Entries are snapshots

A row keeps the track as it was when it was added — id, title, artist, album, duration, genres, cover — beside the id. A collection therefore reads back whole while a source is down, disabled or uninstalled, and only playback needs the source itself. The snapshot is not refreshed; a track renamed upstream keeps the name it was collected under until someone re-adds it.

### 1.5 `can_edit` is how a client knows

`BrowseItem.can_edit` sits beside `can_browse` and `can_add` and means: the server accepts writes that change what this item holds. A collection sets it (its tracks can change) and so does the shelf above them (which collections exist can change) — never a track, which is nobody's list. A client offers editing where it is set and never learns a source name, a type or an id convention. It is advisory — the write API refuses an id it does not own, whatever a client believed — and it does not promise a generic write contract: today `can_edit` and the collections API are the same thing.

## 2. Browse sources

`InputModule` is two contracts wearing one name: what a source *is* (listings, lookups, names, vocabularies) and what a source *has* (audio, suggestions, favourites, content). A built-in has the first and none of the second.

`BrowseSource` is the first half — `browse`, `get`, `search`, `list_filter_values`, `playlist_user_list` — as a `Protocol`, so an input module satisfies it structurally and no plugin declares anything. `BrowseSourceRegistry` holds the enabled input modules (resolved per call, so a source disabled at runtime stops being reachable) plus the server's built-ins.

Which registry a route uses is now a statement about what the route needs:

| Reaches every browse source | Reaches input modules only |
| --- | --- |
| `GET /browse`, `GET /browse/{id}`, `GET /browse/{id}/values` | `GET /ai_search` |
| `GET /get/{id}` | `GET /favorite/*` |
| `GET /search/matches` | `GET /content/*` |
| `GET /playlist/list` | queue track resolution |

Asking a suggestion or favourites endpoint for a browse-only source is an empty answer, not a 404: the source exists, that plane does not. A name nobody knows is still a 404.

`GET /server/modules` lists built-ins alongside plugins with `"builtin": true`, no config and no packages, so a client can search and browse them and skip the planes they lack.

## 3. Storage

One SQLite file, `<state_dir>/collections.db`, WAL, opened at startup and closed at shutdown.

```
collections   id, name, description, created_at, updated_at
entries       entry_id, collection_id, position, entity_id, source,
              title, artist, album, duration, track_json, added_at
entry_genres  entry_id, genre_id, genre_name
```

`track_json` is the snapshot and everything beside it exists to be queried — narrowing is SQL, not a filter pass over a loaded list. Track count and duration are subqueries rather than columns, so they cannot go stale. `entry_id` is what a write addresses: a track may appear twice, and a position moves.

A snapshot that can no longer be parsed is skipped with a warning rather than failing the listing: one row lost to schema drift must not cost the whole collection.

## 4. What a collection looks like

One record, two payloads on the same id:

- **As a playlist** — a row that unrolls in place, with `can_browse`, `can_add`, `can_edit`, the owner "You", `timestamp` being when it last changed, and a subtitle of its track count.
- **As a catalog** — a page under that same id, declaring the fields it can be narrowed by.

The item carries both because the two views are the same entity, and the filter contract hangs fields off a catalog. A client picks the payload its surface needs.

Every item states `can_browse` and `can_add` explicitly, even when the value is the default. Responses are serialised with `exclude_unset`, so a flag an item never set is absent from the JSON entirely, and a client reading it as a plain boolean fails on the whole listing rather than on that one row.

Its cover is the one a list of tracks has always had, not the wide background a catalog card sits on: `CatalogArtService` picks a style per item — `ArtStyle.COVER` for anything carrying a playlist payload, `ArtStyle.CARD` otherwise — and renders the cover as a square 2x2 mosaic of the first four distinct albums it browses, or that one album alone below four, since a half-filled grid reads as a mistake. The URL lands on both payloads, because the art is the entity's and not one view of it. A collection with nothing in it is skipped entirely, and so is one whose albums cannot be fetched: a mosaic is its albums and nothing else, so with none to hand there is no art to ship and the client draws its own stand-in.

Filters, declared per level:

| Level | Fields |
| --- | --- |
| The `collections` shelf | `q` over collection names |
| One collection | `q` over title, artist and album; `source`; `genre` |

`source` and `genre` are values fields offering `any` only — `all` and `none` are refused rather than ignored. The `source` vocabulary is the sources this collection actually draws on, named the way the rest of the server names them; the `genre` vocabulary comes from the snapshots, keeping the commoner spelling where two sources disagree.

Collections answer `search` for the playlist kind only. The tracks inside are the sources' own rows and are found there; a name search that returned them twice would be answering for someone else.

## 5. Playing a collection

The queue holds `TrackInfo` objects, each carrying the retriever made by the source that produced it, so playback of a mixed list needs nothing new — *once the tracks are resolved by their owners*.

That was the one real gap. `POST /queue/add` browsed a container and then asked the *container's* module for every track id it found, which is right for an album and impossible for a collection. `queue_add.track_infos_for` now browses the container through its browse source, groups the tracks it finds by their own source, asks each owner once for the distinct ids it owns, and reassembles the original order.

A source that cannot be reached fails the whole add rather than silently queueing what is left: a gap in the middle of a list the user is watching is worse than an error. A track a source simply does not return is left out, with a warning naming the count.

## 6. Where a collection is shown

Navigation is two levels — `Queue → Discover → Collections` — and a collection is not a third. The Collections screen is both the list and the detail: a collection unrolls in place there, the way an album or a source's playlist unrolls anywhere else. Nothing about a collection opens a screen of its own.

The app keys everything on the two flags the server sends and never on a source name.

- **`builtin` on a module** decides placement. The Discover root shows the built-in source's playlist shelf as YOUR COLLECTIONS above the EXPLORE CATALOGS rule, with its first few as rows, the count, and VIEW ALL; the catalog cards below skip built-in sources, and a built-in source carries no badge, like the local library. With no collections yet the section shows what a collection is and a live CREATE COLLECTION action. With no built-in source at all — an older server — there is no section.
- **`can_edit` on an item** decides what it is. On a row it means a collection: it unrolls with its own cover and summary rather than a source playlist's. On the open page it means the Collections screen itself, whose head carries `New` and `Edit`, and where the invitation stands when there are none. A write to that listing restarts it, the same way a filter change does.

The Discover rows are shortcuts, not a second place to unroll: tapping one opens the Collections screen, scrolls to that collection, unrolls it and marks it briefly, so the jump reads as having landed somewhere; VIEW ALL opens the same screen at its top with everything closed; the play button on a row starts the collection without going anywhere. Returning to Discover restores the scroll it was left at. The root has to keep a settled height, which is the reason the shelf itself never expands.

An unrolled collection carries the same `Play all` / `Enqueue` pair a section of search results carries — one action reads the same everywhere, so both surfaces use the same two chips — and the pair stays through a selection: a header that came and went would move every row under it. What changes is its line, which reports how many of the container's tracks the selection holds instead of the tap-to-play hint that is no longer true. A collection row long-presses into the selection like every other container row, so a set can be gathered across collections and played or queued in one go.

A row says what the collection holds in one line — how many tracks, how long it runs — from `Playlist.track_count` and `Playlist.duration`, with the sources it draws on standing beside it as their own coloured letters from `Catalog.sources` rather than counted. The local library is lettered here, where every other row in the app leaves it unmarked as the default: what a mixed list is made of is the point of the line. An empty one says so instead of counting to zero. A collection with no mosaic yet gets the generated rings with the playlist glyph over them, the same tile the empty states use, and an empty one gets that tile even where stale art exists.

The "OR" left the divider: a section may sit between the entry and the rule, and the label has to read the same whether one does or not.

## 7. Editing, and where it will sit

Editing is a mode of the Collections screen, entered by its own `Edit` and covering every collection at once — the conventional shape (iOS Edit, Material's contextual selection), one toggle rather than a session per row. Making a collection is not part of it: `New` sits beside `Edit` in the screen's head and works on its own, because creating is a single write with nothing to stage. Both are neutral pills, carrying no berry at all. Berry has exactly two jobs (PALETTE.md, *Fill or outline*): a **fill** commits a decision — every filled button in the app is one, from Connect and Show results to the sheet's own `CREATE` — and an **outline** is a decision already made, shown where it took effect, which is precisely what the applied-filter chips directly below these are. A standing toolbar action is neither, and drawing it as an outline made it read as another filter in effect. The one fill a screen at rest may carry goes to the empty state's `CREATE COLLECTION`, where making one is the only thing to do.

Inside a session, staged and reversible: the toolbar becomes `EDITING · N CHANGES` with `CANCEL` and `DONE`, each touched collection shows its own change count and a `RESET` that clears only its staging, and `CANCEL` confirms before discarding. Within one collection a row can be dragged to a new position and removed. Between collections, `MOVE` opens a destination sheet (the other collections, and `+ NEW COLLECTION`); the source shows `−1 moved to Sunday Morning`, the destination `+1 pending`.

**Dragging a track from one collection into another is deliberately not built.** No mainstream music app does it on a phone — Spotify, Apple Music and YouTube Music all use a "move to" sheet on touch and keep drag for a desktop sidebar that cannot scroll away. The reasons apply here: the destination scrolls out of view mid-gesture, and one gesture would carry two meanings (reorder within, move between) decided by a few pixels at the drop. The sheet says which collection it is going to, out loud, and costs one tap.

Renaming and deleting a collection are single writes and do not belong in a staged session. Renaming is a pencil at the trailing end of an unrolled collection's action row, set apart from `Play all` and `Enqueue` because it acts on the list rather than on its music; deleting will join it there. It is deliberately not a control on every row at rest — renaming is rare, and a second button beside the chevron would compete with it for the same thumb while adding chrome to a listing that is mostly read. Reordering the collections themselves is out for now: the shelf orders by most recently changed and the table has no position column, so it is a schema change rather than a control.

## 8. Writes

```
POST   /collections                  create                      (done)
PATCH  /collections/{id}             rename                      (done)
DELETE /collections/{id}             delete
POST   /collections/{id}/entries     add ids of any kind; containers expanded by owner
DELETE /collections/{id}/entries     remove by entry id
PUT    /collections/{id}/order       reorder by entry id
```

`POST /collections` takes a name (trimmed; blank is a 422 naming the field) and an optional description, and answers with the browse id to go to and the name as stored — not the whole item, since the listing is refetched anyway and a new collection has no cover until something is in it. Names are not identifiers: two collections may share one. A write cannot degrade the way a listing does, so a file that will not open is a 503 rather than a success reporting a collection nobody made.

`PATCH /collections/{id}` takes a name under the same rules and answers the same reference. Its path carries the browse id, as every other id-taking route does, so an id belonging to another source is a 404 rather than a refusal — it names nothing here. A rename counts as a change to the collection, which therefore leads a listing ordered by when things last changed; a name that comes back unchanged is not sent at all.

Adding takes ids of any kind and expands containers through the same helper the queue uses, taking each snapshot from the browsed track. Duplicates are skipped unless asked for, and the answer says how many were added and how many were already there. Any id that is not a collection is a 404.

A move between collections is a remove and an add of the same entry, and the staged session in §7 commits a batch of them at once, so it needs one request that either lands whole or not at all rather than a sequence a dropped connection can halve.

Retired in the same change: `playlist_create`, `playlist_update`, `playlist_delete`, `playlist_add_tracks` and `playlist_remove_tracks` leave the SDK contract and their routes leave the server; localfiles drops its playlist tables; Qobuz drops its implementations; the template and Jamendo drop their stubs. `playlist_user_list` stays — listing a source's own playlists is reading.

## 9. Deliberate exclusions

Refreshing snapshots against their sources, exporting to a source, importing a source's playlist as a live link rather than a copy, smart or rule-based collections, sharing, and change events over the queue socket. One user, one app: the app refreshes after its own writes.

Sort order beyond "most recently changed first" is also out. The filter contract has text and values fields and no ordering facet, and adding one for this would be a contract change earning a single listing.
