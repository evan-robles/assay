"""ASSAY MCP server package."""
# Re-export the version from assay_core (the single source, also used by pyproject).
try:
    from assay_core import __version__  # noqa: F401
except Exception:  # pragma: no cover
    __version__ = "unknown"
