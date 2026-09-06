# Browse filters — design

Status: **implemented**. §1 records what the sources could do before this work, §2 the decisions and the alternatives they closed off, §3 the contract as built. §4.1 and §5.1 also cover the sectioned library, which is the same contract applied to one catalog holding several kinds.

Scope: the plugin SDK (`packages/kalinka-plugin-sdk`), the server's `/browse` surface, the three input modules (`localfiles`, `jamendo`, and `kalinka-plugin-qobuz` out of tree), and the Flutter app's filter surface (`KalinkaAI`, sibling repo). Empirical numbers come from the live Jamendo API and a real `localfiles.db`, probed 2026-09-06.

---

## 1. Where things stand

### 1.1 What exists

The app already has the whole filter surface built: `BrowseFilterQuery` carries `text`, `type` and `genreIds`; `BrowseFilterForm` renders a debounced search field, a kind row and a genre chip group; `FacetSupport.unsupported` renders a control muted so a facet the backend cannot honour never reaches the request. What it lacks is truth from the server. `CatalogPage.filterCapabilities` (`lib/providers/search_session_provider.dart`) hardcodes text as unsupported and genre as supported for **every** category, and never reads the `can_genre_filter` flag the server sends. A Jamendo or localfiles category therefore shows a genre group backed by an empty list.

Server and SDK know exactly one filter. `InputModule.browse(entity_id, offset, limit, genre_ids)` takes a list of genre `EntityId`s; `GET /browse/{id}?genre_ids=` validates the type and passes them through; `GET /genre/list?source=` concatenates every enabled module's `list_genre()` page and sums their totals. The `Catalog` model carries a single `can_genre_filter: bool`.

Per module:

- **Qobuz** is the only one that works end to end: `list_genre()` proxies Qobuz's `genre/list`, and the featured shelves forward `genre_ids` comma-joined, which Qobuz treats as OR.
- **Jamendo** returns an empty genre list and ignores `genre_ids`; every shelf declares `can_genre_filter=False`.
- **localfiles** returns an empty genre list and accepts `genre_ids` only to drop it. Its database already has a per-token, cross-field matcher — `_token_where` in `input_module_db.py` requires every folded query token to hit `title`, `album title` or `artist name` — but it is wired only to `search_*`, not to the browse queries.

### 1.2 What the sources can actually do

This is what bounds the design. The sources are, with one exception, remote endpoints with a handful of query parameters, not databases.

| | genre vocabulary | genre filter | text filter | combination |
|---|---|---|---|---|
| **Qobuz** | its own numeric taxonomy, via API | featured shelves (`genre_ids`) | none on featured endpoints | OR |
| **Jamendo** | free-form tags; no list endpoint | `/tracks/` only | all four entity endpoints, entity's own name (`namesearch`) | AND |
| **localfiles** | the library's own tags | any shelf | any shelf, title/artist/album per token | anything |

Jamendo specifics, measured against the live API:

- `tags` is honoured only by `/tracks/`. `/albums/`, `/artists/` and `/playlists/` silently drop it and say so in `headers.warnings` (`The following parameters are not recognized for this method: [tags]`). A genre facet on an album shelf cannot be promised.
- Multiple tags **intersect**: `tags=electronic+ambient` returns only tracks carrying both, `tags=rock+guitar` likewise. Qobuz's `genre_ids=1,2` unions. The app's chip row reads as a union. Combination semantics differ per source and cannot be assumed by a client.
- `namesearch` is accepted by all four endpoints and matches the entity's own name, fuzzily. A track shelf filtered on text will not match on artist name without a second request.
- The genre vocabulary is a long tail: 108 distinct `musicinfo.tags.genres` values over 800 popular tracks, of which the top 25 (`pop`, `electronic`, `rock`, `dance`, `indie`, `ambient`, `folk`, `synthpop`, `hiphop`, `electronica`, `filmscore`, `singersongwriter`, `rnb`, ...) carry most of the mass. Values are slugs (`singersongwriter`, `filmscore`) and need display labels.
- Under rapid successive querying the API intermittently answers `status: success` with zero results. This was seen only while probing in a tight loop; filters are applied on confirm, not per keystroke (§5), so a shelf issues one request per applied change and the behaviour is not expected in normal use.

localfiles specifics, measured on a real library: 49 of 58 albums carry a genre, 22 distinct values, almost all from the file's own tags through `tag_consensus` (2 claims come from Deezer, none from MusicBrainz). The values are free text, multi-valued and inconsistent — `Electro` beside `Electronic`, `Рок` beside `Rock`, `New Wave, Ambient`, `future pop/ebm`. Genre exists on `albums` only, not on `tracks`, and is unindexed.

---

## 2. Decisions

### 2.1 Vocabularies are per source; nothing is merged

A category page is single-source by construction, and `/genre/list?source=` already scopes to one. Merging vocabularies across sources (a canonical taxonomy with an alias map) was considered and dropped: it is only needed for cross-source surfaces, which have no filters today, and it would put a maintained mapping table between every library's tags and the user. localfiles exposes the library's own tags, split and folded; Jamendo exposes its own; Qobuz keeps its numeric ids.

### 2.2 Text constrains the shelf's own query

Typing "miles" into **Popular Albums** means: the query that shelf already runs, with the same ordering, plus a text predicate. localfiles adds `_token_where` to the shelf's SQL; Jamendo adds `namesearch` to the same request; Qobuz, whose featured endpoints take no text, does not offer the facet on those shelves. Pagination and `total` stay honest because the module runs the filtered query.

Two alternatives were rejected. Post-filtering the shelf's loaded page breaks `total`, breaks pagination, and page one of a popularity list almost never contains the word — the box would read as broken. Handing text off to `search()` scoped by source and entity kind costs nothing on the backend (`/search/{type}/{query}?sources=` exists today) but discards the shelf's curation: every album shelf of one source would return identical results for the same word.

### 2.3 Fields are open; structure is closed

Adding a filter must never be a breaking API change. The previous shape — `genre_ids` as a named `browse()` argument — costs one SDK major, a server parameter, a Dart model change and an app widget per field, across two repos. The replacement is a JSON document whose **keys are open** (`genre`, `year`, `origin`, `label`, `format` are data, not schema) and whose **selector shapes are closed** (text, values, range).

What is deliberately *not* taken from a GraphQL-style query is a boolean expression tree. GraphQL works because a resolver can execute anything the schema permits. Behind `browse()` sit fixed remote endpoints: Qobuz can union genres, Jamendo can intersect tags on one endpoint, and only localfiles could evaluate `{"or": [{"not": ...}]}`. A grammar that expressive would let clients build queries that fail at runtime on most sources. The extensibility that is actually needed is over fields, not over boolean structure. Top-level fields AND together; within a values field the source declares which combinations it honours.

### 2.4 Sources declare, server and app stay dumb

Each catalog carries the list of fields it accepts, with kind, label and honoured operations. The app renders one control per declared field by kind and never knows what a genre is. The server parses the document's syntax and passes it through; it does not know which fields exist. The module owns both the declaration and the interpretation, so a new field is a change in one module in one repo.

### 2.5 Unknown is an error, never a no-op

A module that receives a field it did not declare, or an operation it did not offer, fails the request. Silently unfiltered results that look filtered is the one failure mode this design could otherwise hide, and the app's existing rule — a placeholder facet never reaches the request — exists to prevent exactly that.

---

## 3. The contract

### 3.1 The filter document

```json
{
  "q":      {"contains": "miles"},
  "genre":  {"any": ["jazz", "funk"]},
  "year":   {"gte": 1990, "lte": 1999},
  "origin": {"any": ["US"]}
}
```

Keys are field ids chosen by the module. Values are selectors, and there are three:

| kind | keys | meaning |
|---|---|---|
| text | `contains` | free text; no vocabulary |
| values | `any`, `all`, `none` | union, intersection, exclusion of value ids |
| range | `gte`, `lte` | inclusive bounds; either may be absent |

Semantics: every top-level field must hold (AND). Within a values selector, `any` is OR over its ids, `all` is AND, `none` excludes; a selector may carry more than one of them and all must hold. Selectors forbid unknown keys and reject the empty object, which is what keeps the three-way union unambiguous to a parser. A field absent from the document is unconstrained; a document of `{}` is the unfiltered shelf.

### 3.2 Filter specs on the catalog

A document is only safe to build when the client knows what the catalog accepts. The specs ride inline on `Catalog`, replacing `can_genre_filter` — three fields each, already fetched with the shelf, present before the sheet opens:

```json
"filters": [
  {"id": "q",     "kind": "text",   "label": "Search"},
  {"id": "genre", "kind": "values", "label": "Genre", "ops": ["all"]},
  {"id": "year",  "kind": "range",  "label": "Year",  "bounds": [1965, 2024]}
]
```

`ops` is what makes §1.2's finding expressible: Jamendo declares `["all"]`, Qobuz `["any"]`, localfiles `["any", "all", "none"]`, and the chips mean what the source will actually do. A range field carries its bounds in the spec and needs no vocabulary request. A catalog with no filters carries an empty list and the app renders no control.

### 3.3 Values

Vocabularies stay a separate, lazy request: they can run to hundreds of entries, they are only needed when the sheet opens, and they are cacheable. One generic endpoint serves every values field and replaces `/genre/list`:

```
GET /browse/{catalog_id}/filter/{field}/values?offset=0&limit=50&q=
```

returning `{offset, limit, total, items: [{id, name, count?}]}`. `q` is type-ahead over the vocabulary, for the day a library has 800 genres. `count` is optional; localfiles can give album counts, Jamendo cannot. Scoping by catalog rather than by source costs nothing — the catalog id already names the source, and a module with one vocabulary ignores the rest of it — and buys precision: New Releases' year bounds are not the library's, and Jamendo's genre field exists on one shelf only.

Convention: for a field named `genre`, a value's `id` is the same string the module puts in `Album.genre.id.id`, so an album's genre can be turned into a filter selection without a lookup.

### 3.4 Transport

```
GET /browse/{id}?offset=0&limit=50&filter=<url-encoded JSON>
```

Browse stays a GET: idempotent, retryable, and the app's existing refetch keyed on a URL string keeps working unchanged. Real documents are 60–120 bytes encoded, nowhere near a URL limit. `offset` and `limit` remain query parameters — they are not filters.

The parameter arrives as a string and is parsed explicitly with `FilterQuery.model_validate_json` rather than declared as `Json[FilterQuery]`: FastAPI 0.139 refuses a model-typed query parameter, and parsing it by hand is also what lets a shape error carry the same detail as a refusal.

If a document ever outgrows a URL — a hundred selected genres — `POST /browse/{id}/query` with the identical body is a two-line addition sharing the same parser. It is not built until something needs it.

### 3.5 SDK surface

```python
class TextSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contains: str

class ValuesSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    any: List[str] = []
    all: List[str] = []
    none: List[str] = []

class RangeSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gte: Optional[int] = None
    lte: Optional[int] = None

Selector = Union[TextSelector, ValuesSelector, RangeSelector]

class FilterQuery(RootModel[Dict[str, Selector]]):
    """What a caller asked a browse to satisfy. Field ids are the module's own;
    only the selector shapes are fixed. Every field must hold."""
    def fields(self) -> Set[str]: ...
    def reject_undeclared(self, declared: Iterable[str]) -> None: ...
    def text(self, field: str = "q") -> str: ...
    def values(self, field: str) -> Optional[ValuesSelector]: ...
    def range(self, field: str) -> Optional[RangeSelector]: ...


class FilterKind(str, Enum):
    TEXT = "text"
    VALUES = "values"
    RANGE = "range"

class FilterOp(str, Enum):
    ANY = "any"
    ALL = "all"
    NONE = "none"

class FilterSpec(BaseModel):
    """One field a catalog accepts. `ops` for VALUES says which combinations
    the source honours; `bounds` for RANGE is the span the source can offer."""
    id: str
    kind: FilterKind
    label: str
    ops: List[FilterOp] = []
    bounds: Optional[Tuple[int, int]] = None

class Catalog(BaseModel):
    ...
    filters: List[FilterSpec] = []      # replaces can_genre_filter


class FilterValue(BaseModel):
    id: str
    name: str
    count: Optional[int] = None

class FilterValueList(BaseModel):
    offset: int
    limit: int
    total: int
    items: List[FilterValue]


class UnsupportedFilter(ValueError):
    """Raised by a module for a field it did not declare or an op it did not
    offer. The server answers 422 naming the field."""
    def __init__(self, field: str, reason: str): ...


class InputModule(Protocol):
    async def browse(
        self,
        entity_id: EntityId,
        offset: PositiveInt = 0,
        limit: PositiveInt = 50,
        filter: FilterQuery = FilterQuery({}),
    ) -> BrowseItemList: ...

    async def list_filter_values(
        self,
        catalog_id: EntityId,
        field: str,
        offset: int = 0,
        limit: int = 50,
        q: str = "",
    ) -> FilterValueList: ...
```

The `browse()` signature is the last breaking change for filtering. Adding a field afterwards is: append one `FilterSpec` to the catalogs that support it, answer `list_filter_values` for it, read `filter.values(...)` or `filter.range(...)` in `browse`. No SDK release, no server change, no app change, nothing in the qobuz repo.

Helpers exist so that modules read typed selectors rather than re-parsing dicts. `text()` returns `""` for an absent field; `values()` and `range()` return `None`. A helper called with a field whose selector is of another kind raises `UnsupportedFilter`, since a module asking for `values("year")` has declared `year` wrongly.

`reject_undeclared` is the rule of §2.5 in one place rather than one copy per module: every listing calls it with what it declared before reading anything, and a listing that declares nothing calls it with nothing. That is what makes the refusal cover album, artist and playlist listings and the shelf index too, not only the shelves that do have filters.

`Genre`, `EntityType.GENRE` and `Album.genre` stay: an album's genre is display data and an identity, unrelated to how filtering travels. `GenreList`, `list_genre()`, `/genre/list`, `genre_ids` and `can_genre_filter` retire.

### 3.6 Errors

| condition | who detects | response |
|---|---|---|
| malformed JSON, unknown selector key, empty selector | server (Pydantic) | 422 `{"detail": {"field": ..., "reason": "not a valid selector"}}` |
| undeclared field, unoffered op | module (`UnsupportedFilter`) | 422 `{"detail": {"field": ..., "reason": ...}}` |
| module exception or timeout | server, as today | 500 |

Both 422s carry the same body, so the app has one error shape to read whoever rejected the document, and the selector union's internals never reach a caller.

The app treats 422 on a browse as a programming error surfaced in the error state, not as "no results".

### 3.7 Field id conventions

Field ids are the module's own, but two are reserved so a consumer can recognise them without a vocabulary lookup, and the SDK names them (`filters.TEXT_FIELD`, `filters.TYPE_FIELD`). `q` (kind text) is the search field at the top of the sheet. `type` (kind values) is the entity kind, and its value ids are `SearchType` values (`album`, `artist`, `track`, `playlist`) — only a listing that mixes kinds declares it, and it is what turns a shelf's VIEW ALL into a filter rather than a second kind of navigation. Everything else renders in declared order below them. Suggested ids for fields already in view: `genre`, `year` (range over release or recording year), `origin` (values; whatever the source's notion of provenance is). A module may expose a second text field (`lyrics`, say); it renders as a second field, labelled.

Matching `type` by id is the one place a consumer reads a field name. The alternative — a third `FilterKind` — was rejected because the kind is about a selector's shape, and `type` is an ordinary values field; only its meaning is well known.

A text field's `label` is where a source says what it matches, because sources differ and only the source knows: localfiles labels its field "Search titles, artists and albums", Jamendo "Search track names". The app shows the label as the field's placeholder. The `contains` promise itself stays minimal — the result narrows to items whose text matches, as the source defines matching — since Jamendo's fuzzy `namesearch` and localfiles' per-token AND are both honest under it, and promising substring or word-boundary semantics would promise what two of three sources cannot keep.

---

## 4. Per-source behaviour

### 4.1 localfiles

Vocabulary: `albums.genre` split on `,`, `;` and `/`, trimmed and folded with the existing `fold_for_match`, counted by album, ordered by count then name. `id` is the folded form, `name` the most frequent original casing, `count` the album count. This is the library's own vocabulary, uncurated by design (§2.1); `Electro` and `Electronic` remain two entries because the user's tags say so.

Filtering: genre on album shelves matches `albums.genre` against the selected ids after the same split-and-fold; artist shelves match artists with at least one matching album; track shelves inherit the album's genre. `ops` declares `any`, `all` and `none`, all trivially expressible in SQL. Text on every shelf is `_token_where` added to the shelf's own query, which lifts it out of `search_*` into one shared place. `year` becomes a range field the moment it is wanted: `tracks.recording_year` exists, and bounds are a `MIN`/`MAX`.

Storage: matching a multi-valued text column with `LIKE` is correct but scans; at the scale of a real library (thousands of albums) an `album_genres(album_id, genre_id)` table populated at index time and derived once for existing rows by a migration is the right shape. It is an internal change to the plugin, invisible to the contract, and can land when the scan measurably hurts.

Catalogs: the module's root offers one catalog, `library` ("My Library"), declaring `q`, `type` and `genre`. It carries four sections — `artists`, `albums`, `tracks`, `playlists` — each a catalog in its own right declaring `q` and `genre`. A section holding nothing is left out, so the sections say what the library contains.

The library and its sections are two addresses for one listing: browsing `library` under `type: {any: [album]}` and browsing `albums` answer the same rows with the same total. The section exists so a consumer can preview each kind without knowing how to write that constraint; the constraint exists so VIEW ALL is a filter rather than a second navigation model. A test asserts the two agree.

Browsing `library` with no `type` answers the kinds interleaved by recency, newest first — a `UNION ALL` over each kind's `(id, last_updated)` under that kind's own predicate, paged before any row is materialised, with `kind` and `id` breaking timestamp ties so a row cannot cross pages. It must be items and never the section cards, because the consumers that browse a catalog for its first few things — the home shelf preview, the card-art collage, the AI-search router — have no interest in sections and nothing to show without items.

A playlist carries no genre, so its genre predicate is `has_genre(NULL, ?)`: a genre it must satisfy leaves none, one it must not is satisfied by every playlist. The playlists section declares `genre` anyway, so all four sections answer the page's filter in the same terms and an empty one simply hides.

Every catalog carries its own `filters` list, so one that cannot honour a field simply omits it — a section refuses `type`, since it is already one kind.

### 4.2 Jamendo

Vocabulary: a constant in the plugin — Jamendo publishes no tag list, and the shipped mood index (`jamendo_index_v2.sqlite`) holds only embeddings. The constant is seeded from the empirical top of `musicinfo.tags.genres` (§1.2), with a display label per slug (`singersongwriter` → "Singer-songwriter", `filmscore` → "Film score", `rnb` → "R&B"). `count` is absent. `q` on the values endpoint filters the constant by label.

Filtering: `genre` on **Popular Tracks only**, forwarded as `tags=` joined with spaces, `ops: ["all"]` because that is what the API does. `q` on all five shelves, forwarded as `namesearch=` on the shelf's own endpoint, with the same `order` the shelf already uses. The album, artist and playlist shelves do not declare `genre`.

Robustness: a `status: success` response with zero results is treated like any other empty page; the module does not retry within the request budget (it is bounded by the server's per-call timeout, and a retry would double it). The app's empty state already distinguishes "nothing here" from "nothing matches these filters".

### 4.3 Qobuz (out of tree)

Vocabulary: `genre/list` as today, ids unchanged (numeric strings), through `list_filter_values`. Filtering: `genre` with `ops: ["any"]` on the featured shelves that take `genre_ids` today, forwarded comma-joined exactly as now. No `q` on the featured endpoints, because Qobuz offers no text parameter there; the field is simply not declared, and the app renders no search box on those shelves. The plugin moves in the same SDK major as everything else (§6).

---

## 5. App

### 5.1 Sectioned catalogs

A catalog whose `Catalog` carries `sections` renders as one shelf per section until a kind is chosen (`CatalogSectionsView`). Each shelf browses its own catalog for a preview — `preview_config.items_count` items, five by default — shimmering in its own space until its items arrive, so a slow shelf never holds up the rest. A shelf that comes back empty is left out entirely; one that fails says so in its own space, leaving the rest of the page and the filters that could undo it usable.

Each shelf builds its own request from **its own** declared fields, not the page's, so a constraint a shelf cannot honour is dropped before it is sent rather than refused by its source. That is also why a shelf never sees `type`.

VIEW ALL narrows the page to that shelf's kind — `query.copyWith(type: …)`, which the existing chip row already renders with an X that puts the shelves back. Beyond the shelf widget, it needs no navigation, no route and no new state. It shows only when the shelf's total exceeds what it is showing, and only when the section named a kind: `preview_config.content_type` is that name, which is why the SDK now documents it as what a listing holds rather than a styling hint.

### 5.2 Capabilities and transport

The filter surface was already built — a folded pill in the title bar, an overlay card with staged edits, a capability-driven form, and the applied-filter chip row. It is unchanged. `BrowseFilterCapabilities` stays the seam, `FacetSupport` stays its vocabulary, and the type group and the text field's live debounce path stay where they are, unreached, for the Favourites screen they were built for.

What changed is only where the capabilities come from and what leaves on the request:

- `CatalogPage.filterCapabilities` is built by `BrowseFilterCapabilities.fromSpecs` from the catalog's declared `filters`, instead of hardcoding `genre: supported, text: unsupported` for every category. This is what makes text live where a source honours it, and it fixes the previous behaviour of offering genre on sources that had none.
- A facet the source did not declare is `hidden`, not `unsupported`. The muted placeholder means "this app knows the backend cannot honour it"; now the backend says so per shelf, and it also refuses a field it never offered, so there is no affordance left to stand in for.
- `BrowseFilterCapabilities` carries the declared `FilterSpec` for its text and genre facets, plus the catalog and field id the vocabulary comes from. That is what lets a selection travel under the operation its source honours — Jamendo's chips send `all`, Qobuz's send `any` — with no source knowledge in any widget.
- `BrowseFilterQuery.encoded(capabilities)` is the document as the server sees it, and `serverKey` **is** that string rather than a parallel encoding of the same facets, so the reload key and the request cannot disagree about what travels. The kind facet no longer needs a synthetic term: it travels in the document like everything else.
- `browseGenresProvider` is keyed by `(catalogId, field)` and fetches `/browse/{id}/filter/{field}/values`, mapping to the same `Genre` the chips already render. Per catalog rather than per source, because a field is declared per shelf.
- `CatalogCardPlan` and `CatalogPage` carry `filters` and `sections` from the `Catalog` the page was opened from.
- `fromSpecs` tells the two values fields apart by id: `type` is the kind field, any other is the vocabulary the genre facet fills from. Matching on kind alone would have made the first-declared values field the genre one.
- The kind group lights up only where the source declared a `type` field **and** the catalog names the kinds it holds. A single-kind category has nothing to choose between, so the group stays hidden as before — `presentTypes` is the section list, so the app never guesses which kinds a source has.
- `KalinkaPlayerApi.browse` takes the encoded document; `getGenres` becomes `getFilterValues`. Building the document needs the capabilities, which is the surface's business, so the transport takes a string.
- `genrePills` on the old search screen is removed: it was fed by `/genre/list` and read by no widget.

The `range` kind has no control, because no source declares one. `FilterSpec.parseList` drops a spec whose kind this build does not know, so a source that declares one shows fewer fields rather than a blank gap; shipping `year` means adding the kind and its control together.

Filters are applied on confirm, not as they are edited: the overlay stages and one request follows the applied document. That is its existing behaviour, and it is why a text field costs one request per search rather than one per keystroke.

## 6. Rollout

One API change, not two. An earlier plan landed per-source genre on today's `genre_ids` first and the document second; it was dropped because text cannot be added as a named parameter and then moved into the document without changing the API twice, and the transitional genre plumbing would have been written twice as well. Everything below landed together, in this order.

1. **SDK**: §3.5 verbatim. `__version__` goes to `3.0.0` in the same commit as the API change (RELEASING.md, "Major"): widen the five in-tree consumer pins and qobuz's to `>=3,<4`, raise every `REQUIRES_SDK` floor.
2. **Server**: `/browse/{id}` takes `filter`; `/browse/{id}/filter/{field}/values` added; `/genre/list` and `genre_ids` removed; `UnsupportedFilter` mapped to 422.
3. **localfiles**: vocabulary and per-shelf filtering per §4.1, including text, which is the same `_token_where` the search path already uses.
4. **Jamendo**: constant vocabulary, `tags` on the track shelf, `namesearch` on all five, per §4.2.
5. **Qobuz** (out of tree): §4.3, released against SDK `3.x`.
6. **App**: §5.

Retired in the same wave: `genre_ids`, `can_genre_filter`, `GenreList`, `list_genre`, `/genre/list`, `browseGenresProvider`.

No later field needs any of this again. `year`, `origin` and whatever follows are one `FilterSpec`, one `list_filter_values` branch and one read in `browse`, in the module that has the data.

## 7. Testing

The suites below exist and pass; the server's playqueue suite is untouched by this work and is skipped.

- **SDK** (`packages/kalinka-plugin-sdk/tests`): selector parsing — each kind round-trips; unknown keys and the empty object are rejected; the union never mis-parses one kind as another; helper accessors return the typed selector or raise `UnsupportedFilter` on a kind mismatch; `reject_undeclared` refuses an unoffered field and refuses everything for a listing that declares nothing.
- **Server** (`packages/kalinka-server/tests/test_browse_route.py`): `filter` decoded from the query string and handed to the module intact; malformed JSON and a bad selector are 422 in the same shape as a refusal; `UnsupportedFilter` from a module is 422 with the field named; a module failure stays 500; the values endpoint routes to the right module by catalog id.
- **localfiles** (`packages/kalinka-plugin-localfiles/tests/test_browse_filters.py`): vocabulary derivation from a fixture with multi-valued, mixed-case and non-Latin tags; `any`/`all`/`none` on albums, artists and tracks; a value matching as a whole and not as a substring; text spanning title, album and artist; `total` counting the filtered listing; every listing that declares nothing refusing a field. For the library: the root offering one catalog with a shelf per kind it holds; the flat listing answering items and never the sections; every kind interleaved, newest first, and paging over the interleave neither repeating nor skipping a row; `type` narrowing to one kind and to several; a section agreeing with the library under its own kind; a genre a playlist cannot have excluding it and its negation including it; an unknown `type` value and an operation other than `any` refused; the kind vocabulary matching the sections.
- **Jamendo** (`packages/kalinka-plugin-jamendo/tests/test_browse_filters.py`): `tags=` emitted on `popular-tracks` only and joined with spaces; `namesearch=` emitted alongside the shelf's own `order` and date window; a union of genres refused rather than quietly intersected; an undeclared field refused before the request goes out.
- **App** (`test/browse_filter_form_test.dart`, `test/catalog_page_filters_test.dart`, `test/search_filter_overlay_test.dart`, `test/catalog_sections_test.dart`): the existing suites, updated where the seam moved — capabilities built from a declaration; a facet a source did not declare rendering as hidden rather than muted; text and genre travelling as the fields and operation the source declared; `serverKey` being the document it will send. For sections: the kind field told apart from the genre one; the kinds offered being the ones the sections name; a category with no sections offering no kind group; each shelf browsing its own catalog at its own preview size; a shelf shimmering until its items arrive; an empty shelf left out; VIEW ALL narrowing the page to that kind; and the page's filter reaching each shelf in that shelf's own terms.

---

## 8. Deliberate exclusions

- **Boolean trees, OR across fields, negation beyond `none`.** Nothing behind `browse()` could execute them (§2.3).
- **Cross-source vocabulary normalisation.** Only cross-source surfaces would need it, and none has filters (§2.1).
- **Selection-dependent counts** (faceted search: "genres still available given year=1990s"). The values request could carry the current document later without changing shape; not built.
- **A POST body** for the document, until a document outgrows a URL (§3.4).
- **A single-select flag** on values fields. Both sources with a genre parameter accept several values; if a source with a scalar parameter appears, a `max` on `FilterSpec` is the whole change.
- **Genre on Jamendo album shelves** via `/tracks/?tags=&groupby=album_id`. It works, but it turns an album shelf into a track query with a mapping step and makes `total` meaningless.
- **Text on a Jamendo track shelf matching artist names.** `namesearch` matches the entity's own name; matching artists too means a second request and a merge under the 3-second budget. The declared behaviour is "matches the entity's name", which is what the label says.

---

## 9. Open questions

- The app sends one operation per values field: the source's `any` where it is offered, otherwise the first the source declared. A source offering both `any` and `all` therefore cannot be asked for an intersection yet. A per-field toggle is the change when something wants one, and it needs no contract change.
- `year` is the obvious first range field — localfiles has `tracks.recording_year`, Jamendo has `datebetween`, Qobuz has nothing on featured shelves. It is the one case that is not module-only: the app needs the `range` kind and its control before a source may declare it.
