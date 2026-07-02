# Releasing & Versioning

How to cut a release and bump versions in this monorepo. Read this before
tagging or changing any version — it is the source of truth for the process.

## The model (two version lines)

There are two independent things to version, and they work differently:

| What | Packages | Versioned by | Needs a git tag? |
|------|----------|--------------|------------------|
| **App bundle** | `kalinka-server`, `kalinka-plugin-localfiles`, `kalinka-plugin-musiccast`, `kalinka-plugin-dummydevice` | A single `kalinka-vX.Y.Z` git tag (via setuptools_scm) | **Yes** |
| **Plugin SDK** | `kalinka-plugin-sdk` | Its **own SemVer** — a constant in source | **No** |

- The **app bundle** is lockstep: one tag versions the server and all
  first-party plugins together. This is the only tag you create for a release.
- The **SDK** is the plugin API contract, versioned by its **own SemVer**,
  independent of the app/`kalinka-v*` version. **Major** = a breaking API
  change; **minor** = a backwards-compatible addition; **patch** = a fix.
  Plugins and the server depend on it as `kalinka-plugin-sdk>=1,<2`, so
  backwards-compatible minor/patch bumps (`1.0 → 1.1 → …`) never break existing
  plugins, while a major bump (`→ 2.0`) does — those plugins must be re-pinned
  and rebuilt. The SDK's `1.x` measures **API compatibility**; the app's `0.x`
  measures **product maturity** — they are different axes and are expected to
  differ. The server also verifies the installed SDK major at startup and
  refuses to run on a mismatch (see [Runtime enforcement](#runtime-enforcement-startup-guard)).

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
   SDK keeps its own SemVer version (e.g. `kalinka-plugin-sdk_1.0.0_all.deb`).

> The version comes from `git describe`, so **build from the tagged commit with
> a clean tree**. Between tags you'll get dev versions like `0.2.1.dev3+g<sha>.dYYYYMMDD`,
> which is expected for development builds but not for a release.

---

## Data releases (models, indexes) — never let them become "latest"

Releases that host data assets rather than software (`jamendo-ai-*`,
`clap-onnx-*`) are cut by hand. **Always create them with `--latest=false`**:

```bash
gh release create jamendo-ai-v2 --latest=false --title "…" --notes "…" <assets…>
```

GitHub marks the most recently created release as "latest" by default, and
`/releases/latest` is what the website's download buttons link to and what
`scripts/install-release.sh` users expect — it must always resolve to a
`kalinka-v*` software release. (This went wrong once: `jamendo-ai-v1` was
published a day after `kalinka-v3.2.0` and the download buttons landed users on
sqlite index files.) The release workflow re-pins `--latest` whenever it
publishes a `kalinka-v*` release, but don't rely on publish ordering to fix a
mislabeled data release.

---

## Bump the SDK version

The SDK version lives in **one place**:

- [`packages/kalinka-plugin-sdk/src/kalinka_plugin_sdk/_version.py`](packages/kalinka-plugin-sdk/src/kalinka_plugin_sdk/_version.py) → `__version__ = "1.0.0"`

`pyproject.toml` derives the packaging version from it via
`[tool.setuptools.dynamic] version = {attr = "kalinka_plugin_sdk._version.__version__"}`,
so you never edit the version in two places.

### Minor or patch (backwards compatible — e.g. `1.0.0` → `1.1.0`)
Added an API, fixed a bug, nothing removed/changed:

1. Edit `__version__` in `_version.py`.
2. Done. Consumers pin `>=1,<2`, which already accepts it — **no plugin
   changes, no re-pinning, no rebuild required**. Existing plugins keep working.

### Major (breaking — e.g. `1.x` → `2.0.0`)
Removed or changed an existing public API (a protocol change):

1. Edit `__version__` in `_version.py` to `2.0.0`.
2. Widen **every consumer pin** from `<2` to `<3`, i.e. `kalinka-plugin-sdk>=2,<3`:
   - `packages/kalinka-server/pyproject.toml`
   - `packages/kalinka-plugin-localfiles/pyproject.toml`
   - `packages/kalinka-plugin-musiccast/pyproject.toml`
   - `packages/kalinka-plugin-dummydevice/pyproject.toml`
3. Update the plugins/server to the new API and confirm they build & run.
4. Cut a new `kalinka-vX.Y.Z` app release — a protocol break is a server change,
   so the **server version moves with it**. Third-party plugins built for the old
   major won't install against the new server (their `<2` excludes SDK `2.x`).

Find the spots to touch:
```bash
grep -rn 'kalinka-plugin-sdk *[>=<]' packages/*/pyproject.toml   # the 4 consumer pins
grep -n  '__version__' packages/kalinka-plugin-sdk/src/kalinka_plugin_sdk/_version.py  # the 1 SDK source
```

The SDK ships in the same `make build-all-deb` run as the app bundle; it does
not need a tag or a separate release step.

### Runtime enforcement (startup guard)

Dependency pins only fire when an install goes through the resolver. As a
backstop, the **server checks the installed SDK at startup**: it reads its own
`kalinka-plugin-sdk` requirement (from package metadata — the same `>=1,<2`
pin, no second source of truth) and **refuses to start** if the installed SDK
falls outside it. This catches the cases pins can't — `pip install --no-deps`,
`dpkg --force-depends`, or upgrading the SDK in place to a different major.
"No legacy SDK" means the server supports only the SDK **major** it was built
against. The check lives in
[`sdk_compat.py`](packages/kalinka-server/src/kalinka_server/sdk_compat.py).

---

## Quick reference

```bash
# Cut an app release
git tag kalinka-vX.Y.Z && git push origin kalinka-vX.Y.Z
make build-all-deb                       # -> debs/

# Bump the SDK (minor/patch): edit one line, then rebuild
$EDITOR packages/kalinka-plugin-sdk/src/kalinka_plugin_sdk/_version.py   # __version__
make build-all-deb
```

## Rules of thumb

- **One tag per release** (`kalinka-v*`). Never tag individual packages.
- **Clean tree on the tagged commit**, or the version carries a dev/dirty suffix.
- **SDK = one constant.** Minor/patch touches only `_version.py`; major also
  widens the four `>=1,<2` consumer pins.
- Compatibility is guaranteed **within a major version only** — that's what the
  `>=N,<N+1` pins encode.
