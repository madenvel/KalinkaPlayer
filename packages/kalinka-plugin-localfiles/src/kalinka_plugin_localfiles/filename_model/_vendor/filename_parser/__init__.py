"""Lazy imports keep dataset preparation independent of the model runtime."""
__all__ = ["FilenameParser", "parse_filename", "parse_path"]


def __getattr__(name):
    if name in __all__:
        from . import model
        return getattr(model, name)
    raise AttributeError(name)
