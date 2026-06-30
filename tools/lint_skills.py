#!/usr/bin/env python3
"""Lint SKILL.md files against the chemkit skill-standards contract.

Mechanizes the cheap, checkable parts of rules/skill-standards.md so "skills are
authored to a consistent contract" is enforced, not merely asserted. Catches the
drift that silently degrades the agent's tool descriptions and the docs:

  - frontmatter present and parseable (name / description / category);
  - `name` is kebab-case and EQUALS the folder name (the MCP server keys tools
    off the folder; a mismatch means the SKILL.md documents a different tool);
  - `category` is one of the standard set;
  - `description` has no mid-line ": " (breaks unquoted YAML, per the standard);
  - required sections present: the `# <name>` H1, `## Goal`, `## References`;
  - an Author footer (skill-standards requires a human author, not an agent).

Pure stdlib; no YAML dependency (the frontmatter is a tiny fixed block we parse
line-wise). Returns nonzero if any skill fails, so it can gate CI.

Usage:
    python tools/lint_skills.py            # lint every skills/<name>/SKILL.md
    python tools/lint_skills.py <name>     # lint one skill
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, List, Optional

_REPO = Path(__file__).resolve().parent.parent
_SKILLS = _REPO / "skills"

_VALID_CATEGORIES = {
    "materials", "chemistry", "machine-learning", "drug-discovery", "general",
}
_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _parse_frontmatter(text: str) -> Optional[Dict[str, str]]:
    """Parse the leading `---`-delimited YAML block as flat key: value pairs.
    Returns None if there is no frontmatter block."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end].strip("\n")
    fm: Dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def lint_skill(skill_dir: Path) -> List[str]:
    """Return a list of problem strings for one skill ([] = clean)."""
    name = skill_dir.name
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return ["no SKILL.md"]
    text = md.read_text()
    problems: List[str] = []

    fm = _parse_frontmatter(text)
    if fm is None:
        return ["no parseable YAML frontmatter (--- block)"]

    # name
    fm_name = fm.get("name")
    if not fm_name:
        problems.append("frontmatter missing `name`")
    else:
        if not _KEBAB.match(fm_name):
            problems.append(f"name {fm_name!r} is not kebab-case")
        if fm_name != name:
            problems.append(
                f"name {fm_name!r} != folder {name!r} "
                "(MCP server keys the tool off the folder)")

    # description
    desc = fm.get("description")
    if not desc:
        problems.append("frontmatter missing `description`")
    elif ": " in desc:
        problems.append(
            "description contains ': ' (breaks unquoted YAML; rephrase or quote)")

    # category
    cat = fm.get("category")
    if not cat:
        problems.append("frontmatter missing `category`")
    else:
        cats = [c.strip() for c in cat.strip("[]").split(",")] if "," in cat \
            else [cat.strip("[]").strip()]
        bad = [c for c in cats if c not in _VALID_CATEGORIES]
        if bad:
            problems.append(
                f"category {bad} not in {sorted(_VALID_CATEGORIES)}")

    # required sections
    if not re.search(r"^#\s+\S", text, re.MULTILINE):
        problems.append("missing an H1 title (`# <Skill Name>`)")
    if not re.search(r"^##\s+Goal\b", text, re.MULTILINE):
        problems.append("missing `## Goal` section")
    if not re.search(r"^##\s+References\b", text, re.MULTILINE):
        problems.append("missing `## References` section")

    # author footer (a human, not an agent)
    if not re.search(r"\*\*Author:\*\*", text):
        problems.append("missing Author footer (skill-standards requires a human author)")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="lint SKILL.md against skill-standards")
    ap.add_argument("skill", nargs="?", help="one skill name (default: all)")
    args = ap.parse_args()

    if args.skill:
        dirs = [_SKILLS / args.skill]
    else:
        dirs = sorted(d for d in _SKILLS.iterdir()
                      if d.is_dir() and not d.name.startswith("_"))

    n_bad = 0
    for d in dirs:
        problems = lint_skill(d)
        if problems:
            n_bad += 1
            print(f"[FAIL] {d.name}")
            for p in problems:
                print(f"        - {p}")
    total = len(dirs)
    print(f"\n{total - n_bad}/{total} skills pass SKILL.md lint"
          + (f" ({n_bad} with problems)" if n_bad else " — all clean"))
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
