"""The registry of browsable sources: plugins as they stand right now, plus
the server's own."""

from kalinka_server.browse_source import BrowseSourceRegistry, RegisteredSource


class _Source:
    def __init__(self, name):
        self._name = name

    def module_name(self):
        return self._name


def _entry(name, *, builtin=False):
    return RegisteredSource(
        name=name, title=name.title(), source=_Source(name), builtin=builtin
    )


def test_sources_are_listed_by_name_whoever_provides_them():
    registry = BrowseSourceRegistry(
        lambda: [_entry("qobuz"), _entry("localfiles")],
        [_entry("collections", builtin=True)],
    )

    assert [entry.name for entry in registry.entries()] == [
        "collections",
        "localfiles",
        "qobuz",
    ]


def test_a_plugin_disabled_after_startup_stops_being_reachable():
    enabled = {"qobuz"}
    registry = BrowseSourceRegistry(
        lambda: [_entry(name) for name in enabled],
        [_entry("collections", builtin=True)],
    )
    assert registry.get("qobuz") is not None

    enabled.clear()

    assert registry.get("qobuz") is None
    assert registry.get("collections") is not None


def test_an_unknown_name_resolves_to_nothing():
    registry = BrowseSourceRegistry(lambda: [], [_entry("collections", builtin=True)])

    assert registry.get("nope") is None


def test_a_builtin_says_so():
    registry = BrowseSourceRegistry(
        lambda: [_entry("qobuz")], [_entry("collections", builtin=True)]
    )

    builtin = {entry.name: entry.builtin for entry in registry.entries()}

    assert builtin == {"collections": True, "qobuz": False}
