"""A source that could not answer one leg of a request."""


class SourceFailed(Exception):
    """One of a source's legs raised. Nothing partial is passed off as its
    listing — the caller reports the source as unavailable."""

    def __init__(self, source: str, cause: BaseException):
        self.source = source
        self.cause = cause
        super().__init__(f"{source}: {cause!r}")
