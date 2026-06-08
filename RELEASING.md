# Releasing & Versioning

How to cut a release and bump versions in this monorepo. Read this before
tagging or changing any version — it is the source of truth for the process.

## The model (two version lines)

There are two independent things to version, and they work differently:

| What | Packages | Versioned by | Needs a git tag? |
|------|----------|--------------|------------------|
| **App bundle** | `kalinka-server`, `kalinka-plugin-localfiles`, `kalinka-plugin-musiccast`, `kalinka-plugin-dummydevice` | A single `kalinka-vX.Y.Z` git tag (via setuptools_scm) | **Yes** |
| **Plugin SDK** | `kalinka-plugin-sdk` | A static constant in source | **No** |

- The **app bundle** is lockstep: one tag versions the server and all
  first-party plugins together. This is the only tag you create for a release.
- The **SDK** is the plugin API contract. It is pinned to a fixed version
  during pre-1.0 development and only changes when you deliberately edit it.
  Plugins and the server depend on it as `kalinka-plugin-sdk>=1,<2`, so any
  `1.x` SDK satisfies them.

Old per-package tags (`kalinka-server-v*`, `kalinka-plugin-*-v*`, `release-*`)
are inert — nothing matches them anymore. Leave them or delete them; they have
no effect.

---

## Release the app bundle (server + plugins)

1. Make sure `main` is at the commit you want to ship and the working tree is
   **clean** (a dirty tree produces a `…devN+dirty` version, not a clean one).

2. Tag and push — **one tag releases everything**:
   ```bash
   git tag kalinka-v0.2.0
   git push origin kalinka-v0.2.0
   ```
   Pick `X.Y.Z` by SemVer: MAJOR = breaking server↔client / user-facing change,
   MINOR = backward-compatible feature, PATCH = fixes.

3. Build the `.deb` packages from the tagged commit:
   ```bash
   make build-all-deb      # builds server + all plugins, output in debs/
   ```
   `debs/` is wiped at the start of each build, so it contains only this
   release's artifacts. Every app package will be named `…0.2.0…`; the bundled
   SDK keeps its own fixed version (e.g. `kalinka-plugin-sdk_1.0.0_all.deb`).

> The version comes from `git describe`, so **build from the tagged commit with
> a clean tree**. Between tags you'll get dev versions like `0.2.1.dev3+g<sha>`,
> which is expected for development builds but not for a release.

---

## Bump the SDK version

The SDK version lives in **one place**:

- [`packages/kalinka-plugin-sdk/src/kalinka_plugin_sdk/__init__.py`](packages/kalinka-plugin-sdk/src/kalinka_plugin_sdk/__init__.py) → `__version__ = "1.0.0"`

`pyproject.toml` derives the packaging version from it via
`[tool.setuptools.dynamic] version = {attr = "kalinka_plugin_sdk.__version__"}`,
so you never edit the version in two places.

### Minor or patch (backward compatible — e.g. `1.0.0` → `1.1.0`)
Added an API, fixed a bug, nothing removed/changed:

1. Edit `__version__` in `__init__.py`.
2. Done. Consumers pin `>=1,<2`, which already accepts it — no other changes.

### Major (breaking — e.g. `1.x` → `2.0.0`)
Removed or changed an existing public API:

1. Edit `__version__` in `__init__.py` to `2.0.0`.
2. Widen **every consumer pin** from `<2` to `<3`, i.e. `kalinka-plugin-sdk>=2,<3`:
   - `packages/kalinka-server/pyproject.toml`
   - `packages/kalinka-plugin-localfiles/pyproject.toml`
   - `packages/kalinka-plugin-musiccast/pyproject.toml`
   - `packages/kalinka-plugin-dummydevice/pyproject.toml`
3. Update the plugins/server to the new API and confirm they build & run.

Find the spots to touch:
```bash
grep -rn 'kalinka-plugin-sdk *[>=<]' packages/*/pyproject.toml   # the 4 consumer pins
grep -n  '__version__' packages/kalinka-plugin-sdk/src/kalinka_plugin_sdk/__init__.py  # the 1 SDK source
```

The SDK ships in the same `make build-all-deb` run as the app bundle; it does
not need a tag or a separate release step.

---

## Quick reference

```bash
# Cut an app release
git tag kalinka-vX.Y.Z && git push origin kalinka-vX.Y.Z
make build-all-deb                       # -> debs/

# Bump the SDK (minor/patch): edit one line, then rebuild
$EDITOR packages/kalinka-plugin-sdk/src/kalinka_plugin_sdk/__init__.py   # __version__
make build-all-deb
```

## Rules of thumb

- **One tag per release** (`kalinka-v*`). Never tag individual packages.
- **Clean tree on the tagged commit**, or the version carries a dev/dirty suffix.
- **SDK = one constant.** Minor/patch touches only `__init__.py`; major also
  widens the four `>=1,<2` consumer pins.
- Compatibility is guaranteed **within a major version only** — that's what the
  `>=N,<N+1` pins encode.
