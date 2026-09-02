"""Environment-variable access with the ASSAY_* / CHEMKIT_* rename handled once.

The project was renamed assay -> ASSAY. Its tunables shipped as `CHEMKIT_*`
and are set in users' shells, MCP client configs, PBS job scripts and the Aurora
sweep controller, so renaming them outright would silently change behavior on
every machine that already has them exported — a remote run quietly becoming a
local one, for instance.

So both spellings are read, `ASSAY_*` taking precedence:

    get("REMOTE_HOST")  ->  ASSAY_REMOTE_HOST, else CHEMKIT_REMOTE_HOST, else ""

Pass the suffix only (no prefix). New code should use this helper rather than
reading os.environ directly, so the compatibility window lives in one file and
can be closed by deleting the fallback here.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

_NEW = "ASSAY_"
_OLD = "CHEMKIT_"


def names(suffix: str) -> tuple[str, str]:
    """The (preferred, legacy) variable names for a suffix."""
    return _NEW + suffix, _OLD + suffix


def get(suffix: str, default: str = "",
        env: Optional[Mapping[str, str]] = None) -> str:
    """Value of ASSAY_<suffix>, falling back to CHEMKIT_<suffix>, else `default`.

    `env` lets a caller resolve against an explicit mapping (the MCP server
    builds a per-call child environment) instead of os.environ.
    """
    src: Mapping[str, str] = os.environ if env is None else env
    new, old = names(suffix)
    for key in (new, old):
        val = src.get(key)
        if val is not None and val != "":
            return val
    return default


def is_set(suffix: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """True if either spelling is present and non-empty."""
    return get(suffix, "", env) != ""


def flag(suffix: str, default: bool = False,
         env: Optional[Mapping[str, str]] = None) -> bool:
    """Boolean tunable. Unset -> `default`; otherwise the usual falsey spellings."""
    raw = get(suffix, "", env)
    if raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")
