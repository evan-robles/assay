"""chemkit MCP server package."""
# Single source of truth for the version is chemkit_engine.__init__.__version__
# (also read by pyproject's dynamic version). Re-export it here so there is no
# drift. Fall back gracefully if the engine isn't importable in some context.
try:
    from .chemkit_engine import __version__  # noqa: F401  (in-repo layout)
except Exception:  # pragma: no cover
    try:
        from chemkit_engine import __version__  # noqa: F401  (installed layout)
    except Exception:
        __version__ = "unknown"
