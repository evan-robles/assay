"""Skill discovery — the single source of truth for the skill registry.

Walks ``skills/*/scripts/run.py``, imports each self-contained skill module, and
reads its manifest (``SKILL_NAME`` kebab display name + ``SUBCOMMAND`` engine
subcommand) plus its ``build_parser()``. The MCP server, the ``assay`` CLI, and
the lints all build their view of "what skills exist" from THIS function instead
of a hand-maintained table, so the registry can never drift from the skills on
disk (DESIGN.md §10.2 registry-sync).

Each skill's ``build_parser()`` is the authoritative arg spec; introspecting it
(via ``assay_core.arg_spec.params_from_parser``) gives the server each tool's
typed signature.
"""
from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Repo root = parent of the installed assay_core package; skills/ lives there.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _REPO_ROOT / "skills"


@dataclass(frozen=True)
class SkillInfo:
    name: str            # kebab display name / MCP tool name (SKILL_NAME)
    subcommand: str      # engine subcommand (SUBCOMMAND), e.g. "sp"
    package: str         # underscore package dir, e.g. "single_point_energy"
    run_path: str        # absolute path to scripts/run.py
    module: str          # importable module, e.g. "skills.single_point_energy.scripts.run"


def _ensure_repo_on_path() -> None:
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))


def _iter_skill_packages() -> List[str]:
    """Underscore package dirs under skills/ that hold a scripts/run.py."""
    out: List[str] = []
    if not _SKILLS_DIR.is_dir():
        return out
    for child in sorted(_SKILLS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        if (child / "scripts" / "run.py").is_file():
            out.append(child.name)
    return out


_CACHE: Optional[Dict[str, SkillInfo]] = None


def discover_skills(*, refresh: bool = False) -> Dict[str, SkillInfo]:
    """Map kebab tool name -> SkillInfo for every self-contained skill on disk.

    Imports each skill's run.py to read its manifest. Cached after the first call
    (pass refresh=True to rescan). A skill folder that lacks the SKILL_NAME /
    SUBCOMMAND / build_parser manifest is skipped with a note on stderr — it is
    not yet converted to the inverted contract.
    """
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    _ensure_repo_on_path()
    infos: Dict[str, SkillInfo] = {}
    for pkg in _iter_skill_packages():
        mod_name = f"skills.{pkg}.scripts.run"
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:  # noqa: BLE001 - a broken skill must not kill discovery
            sys.stderr.write(f"# discovery: skipping {pkg}: import failed ({e})\n")
            continue
        name = getattr(mod, "SKILL_NAME", None)
        sub = getattr(mod, "SUBCOMMAND", None)
        if not name or not sub or not callable(getattr(mod, "build_parser", None)):
            sys.stderr.write(
                f"# discovery: skipping {pkg}: missing SKILL_NAME/SUBCOMMAND/build_parser\n")
            continue
        infos[name] = SkillInfo(
            name=name, subcommand=sub, package=pkg,
            run_path=str(_SKILLS_DIR / pkg / "scripts" / "run.py"),
            module=mod_name,
        )
    _CACHE = infos
    return infos


def skill_by_subcommand(subcommand: str) -> Optional[SkillInfo]:
    for info in discover_skills().values():
        if info.subcommand == subcommand:
            return info
    return None


def build_parser_for(name_or_sub: str) -> Optional[Callable]:
    """Return the build_parser() callable for a skill by tool name or subcommand."""
    infos = discover_skills()
    info = infos.get(name_or_sub) or skill_by_subcommand(name_or_sub)
    if info is None:
        return None
    mod = importlib.import_module(info.module)
    return mod.build_parser
