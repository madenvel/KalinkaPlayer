"""Single source of truth for the SDK version.

This is a hand-maintained source file (the SDK does not use setuptools_scm).
`pyproject.toml` reads it via ``[tool.setuptools.dynamic]`` and ``__init__.py``
re-exports it. Bump the major only for a breaking API change, then widen the
consumers' ``>=1,<2`` pins accordingly. See RELEASING.md.
"""

__version__ = "1.2.0"
