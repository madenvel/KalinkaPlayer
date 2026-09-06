"""Filtering contract for browsable catalogs.

Field ids are the module's own — ``genre``, ``year``, ``origin`` are data, not
schema — while the selector shapes here are fixed. A module declares the fields
a catalog accepts (:class:`FilterSpec`), the caller sends a
:class:`FilterQuery`, and adding a field later touches only the module that has
the data.
"""

from enum import Enum
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, RootModel, model_validator


class UnsupportedFilter(ValueError):
    """Raised by a module for a field it did not declare, or an operation it
    did not offer. The server answers 422 naming the field; a filter is never
    dropped silently, because results that look filtered but are not is the
    one failure this contract must not hide."""

    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


# Field ids every source spells the same way, so a consumer can render them
# without a lookup: free text over whatever the listing holds, and the entity
# kind a mixed listing can be narrowed to (values are `SearchType` values).
TEXT_FIELD = "q"
TYPE_FIELD = "type"


class TextSelector(BaseModel):
    """Free text. What it matches is the source's business — see the field's
    ``label``, which is where a source says so."""

    model_config = ConfigDict(extra="forbid")

    contains: str


class ValuesSelector(BaseModel):
    """Value ids drawn from the field's vocabulary. ``any`` unions, ``all``
    intersects, ``none`` excludes; whichever are present must all hold."""

    model_config = ConfigDict(extra="forbid")

    any: List[str] = []
    all: List[str] = []
    none: List[str] = []

    @model_validator(mode="after")
    def _reject_empty(self) -> "ValuesSelector":
        if not (self.any or self.all or self.none):
            raise ValueError("values selector must carry any, all or none")
        return self


class RangeSelector(BaseModel):
    """Inclusive bounds; either may be absent for an open end."""

    model_config = ConfigDict(extra="forbid")

    gte: Optional[int] = None
    lte: Optional[int] = None

    @model_validator(mode="after")
    def _reject_empty(self) -> "RangeSelector":
        if self.gte is None and self.lte is None:
            raise ValueError("range selector must carry gte or lte")
        if self.gte is not None and self.lte is not None and self.gte > self.lte:
            raise ValueError("range selector gte must not exceed lte")
        return self


Selector = Union[TextSelector, ValuesSelector, RangeSelector]


class FilterQuery(RootModel[Dict[str, Selector]]):
    """What a caller asked a browse to satisfy: field id → selector.

    Every field must hold. A field absent from the query is unconstrained, and
    an empty query is the unfiltered listing.

    The accessors are how a module reads it; they raise
    :class:`UnsupportedFilter` when a field carries a selector of another kind,
    since that means the module declared the field as something it is not.
    """

    root: Dict[str, Selector] = {}

    def fields(self) -> Set[str]:
        """Every field the caller constrained."""
        return set(self.root)

    def reject_undeclared(self, declared: Iterable[str]) -> None:
        """Refuse any field this listing did not offer.

        Every module calls this before reading a query: dropping a constraint
        instead would answer with a listing that looks filtered and is not.
        """
        unknown = self.fields() - set(declared)
        if unknown:
            raise UnsupportedFilter(sorted(unknown)[0], "not filterable here")

    def text(self, field: str = TEXT_FIELD) -> str:
        """The text asked for, or ``""`` when the field is unconstrained."""
        selector = self.root.get(field)
        if selector is None:
            return ""
        if not isinstance(selector, TextSelector):
            raise UnsupportedFilter(field, "expected a text selector")
        return selector.contains

    def values(self, field: str) -> Optional[ValuesSelector]:
        selector = self.root.get(field)
        if selector is None:
            return None
        if not isinstance(selector, ValuesSelector):
            raise UnsupportedFilter(field, "expected a values selector")
        return selector

    def range(self, field: str) -> Optional[RangeSelector]:
        selector = self.root.get(field)
        if selector is None:
            return None
        if not isinstance(selector, RangeSelector):
            raise UnsupportedFilter(field, "expected a range selector")
        return selector


class FilterKind(str, Enum):
    TEXT = "text"
    VALUES = "values"
    RANGE = "range"


class FilterOp(str, Enum):
    ANY = "any"
    ALL = "all"
    NONE = "none"


class FilterSpec(BaseModel):
    """One field a catalog accepts.

    Attributes:
        id: The field id a :class:`FilterQuery` addresses it by.
        kind: Which selector shape the field takes.
        label: What to show above the control. For a TEXT field this is also
            where the source says what it matches ("Search track names"),
            because sources differ and only the source knows.
        ops: VALUES only — the combinations this source honours. A source whose
            API intersects declares ``[ALL]`` and the UI stops implying a union.
        bounds: RANGE only — the span the source can offer, so a range field
            needs no vocabulary request.
    """

    id: str
    kind: FilterKind
    label: str
    ops: List[FilterOp] = []
    bounds: Optional[Tuple[int, int]] = None


class FilterValue(BaseModel):
    """One entry of a VALUES field's vocabulary.

    Attributes:
        id: What a :class:`ValuesSelector` carries.
        name: Display label.
        count: How many items the value covers, where the source can say.
    """

    id: str
    name: str
    count: Optional[int] = None


class FilterValueList(BaseModel):
    """Paginated vocabulary of one VALUES field."""

    offset: int
    limit: int
    total: int
    items: List[FilterValue]
