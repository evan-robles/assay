"""chemkit MCP server package."""
# Single source of truth for the version is assay_core.__init__.__version__
# (also read by pyproject's dynamic version). Re-export it here so there is no
# drift. Fall back gracefully if the engine isn't importable in some context.
try:
    from assay_core import __version__  # noqa: F401
except Exception:  # pragma: no cover
    __version__ = "unknown"
