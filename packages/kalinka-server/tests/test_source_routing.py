"""Which half of a source a request reaches.

Reading goes to every browsable source, the server's own included; anything
that needs audio goes only to the modules that have it. A source that browses
but streams nothing is empty on those planes, never a missing module.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kalinka_plugin_sdk.inputmodule import InputModule

from kalinka_server import server
from kalinka_server.browse_source import BrowseSourceRegistry, RegisteredSource


class _Source:
    def __init__(self, name):
        self._name = name

    def module_name(self):
        return self._name


class _Module(InputModule):
    def __init__(self, name):
        self._name = name

    def module_name(self):
        return self._name


@pytest.fixture
def registry(monkeypatch):
    """A server whose only browsable source is the built-in one."""
    entry = RegisteredSource(
        name="collections",
        title="Collections",
        source=_Source("collections"),
        builtin=True,
    )
    registry = BrowseSourceRegistry(lambda: [], [entry])
    monkeypatch.setattr(server, "_browse_registry", registry)
    return registry


def test_a_builtin_answers_for_its_own_ids(registry):
    source = server.browse_source_from_id("kalinka:collections:playlist:c1")

    assert source.module_name() == "collections"


def test_an_unknown_source_is_404(registry):
    with pytest.raises(HTTPException) as failure:
        server.browse_source_from_id("kalinka:nope:track:1")

    assert failure.value.status_code == 404


def test_asking_for_everything_reaches_the_builtin(registry):
    assert [s.module_name() for s in server.extract_browse_sources(None)] == [
        "collections"
    ]


def test_asking_a_browsable_source_for_suggestions_is_empty_not_missing(registry):
    assert server.extract_modules("collections") == []


def test_asking_for_a_source_nobody_knows_is_still_404(registry):
    with pytest.raises(HTTPException) as failure:
        server.extract_modules("nope")

    assert failure.value.status_code == 404


def test_a_disabled_module_is_not_reached_by_naming_it(registry, monkeypatch):
    """The enabled check used to guard only the ask-for-everything branch,
    which is the one nothing takes any more."""
    module = _Module("qobuz")
    monkeypatch.setattr(
        server.modules,
        "prepared_input_modules",
        {"qobuz": SimpleNamespace(interface=module)},
    )
    monkeypatch.setattr(server.modules, "enabled_input_modules", {"qobuz"})
    assert server.extract_modules("qobuz") == [module]

    monkeypatch.setattr(server.modules, "enabled_input_modules", set())
    with pytest.raises(HTTPException) as failure:
        server.extract_modules("qobuz")

    assert failure.value.status_code == 404


def test_a_blank_sources_is_not_every_source(registry):
    """`sources=` fell through to the ask-for-everything branch, which is how
    a client could still get the merged listing the per-source routes replaced.
    """
    for blank in ("", "   ", ",", "collections,"):
        with pytest.raises(HTTPException) as failure:
            server.extract_browse_sources(blank)
        assert failure.value.status_code == 422, blank

        with pytest.raises(HTTPException) as failure:
            server.extract_modules(blank)
        assert failure.value.status_code == 422, blank


def test_a_source_nobody_knows_is_refused_not_dropped(registry):
    """Answering with the sources that happened to be recognised is a listing
    that looks complete and is not."""
    with pytest.raises(HTTPException) as failure:
        server.extract_browse_sources("collections,nope")

    assert failure.value.status_code == 404
    assert "nope" in failure.value.detail
