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
        # A skill converted to the inverted architecture (DESIGN.md) uses an
        # UNDERSCORE package folder (so it is importable) with the kebab display
        # name in frontmatter — accept folder == name OR the underscore form.
        if fm_name != name and fm_name.replace("-", "_") != name:
            problems.append(
                f"name {fm_name!r} != folder {name!r} "
                "(expected the kebab name, or its underscore package form)")

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


# ---------------------------------------------------------------------------
# Spine lint (DESIGN.md §10.2-1): every self-contained skill's scripts/run.py
# must expose the inverted-architecture contract so a skill can never silently
# lose a guardrail. We check statically (AST) so the lint runs without importing
# heavy chemistry deps.
# ---------------------------------------------------------------------------

def lint_skill_spine(run_py: Path) -> List[str]:
    """Return problems with one skill's scripts/run.py spine ([] = clean)."""
    import ast
    problems: List[str] = []
    try:
        tree = ast.parse(run_py.read_text())
    except (OSError, SyntaxError) as e:
        return [f"run.py unparseable: {e}"]

    top_assigns = {t.id for node in tree.body if isinstance(node, ast.Assign)
                   for t in node.targets if isinstance(t, ast.Name)}
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}

    # (a) discovery manifest
    for const in ("SKILL_NAME", "SUBCOMMAND"):
        if const not in top_assigns:
            problems.append(f"missing module-level {const} manifest constant")
    # (b) typed keyword-only run()
    run_fn = next((n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "run"), None)
    if run_fn is None:
        problems.append("no top-level run() function")
    else:
        a = run_fn.args
        # keyword-only args after the first positional(s) is the typed contract
        if not a.kwonlyargs:
            problems.append("run() has no keyword-only args (typed contract expected)")
    # (c) build_parser()
    if "build_parser" not in funcs:
        problems.append("no build_parser() function")
    # (d) __main__ routes through argkit.run_cli
    src = run_py.read_text()
    if "run_cli(" not in src:
        problems.append("__main__ does not call argkit.run_cli (spine bypass)")
    if '__name__ == "__main__"' not in src and "__name__ == '__main__'" not in src:
        problems.append("no `if __name__ == \"__main__\"` entrypoint")
    return problems


def lint_all_spines() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for d in sorted(_SKILLS.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        run_py = d / "scripts" / "run.py"
        if not run_py.is_file():
            out[d.name] = ["no scripts/run.py (skill not converted to the inverted contract)"]
            continue
        probs = lint_skill_spine(run_py)
        if probs:
            out[d.name] = probs
    return out


# ---------------------------------------------------------------------------
# Registry-sync lint (DESIGN.md §10.2-2): the discovery registry, the server tool
# list, `assay --list-skills`, and the PreToolUse hook's METHOD_REQUIRED_SUBCMDS
# are all derived from the same skills — verify they agree so #5/#6/#11 can never
# silently diverge from the actual skill set.
# ---------------------------------------------------------------------------

def _hook_method_required_subcmds() -> Optional[set]:
    hook = _REPO / ".claude" / "hooks" / "chemkit-method-gate.sh"
    if not hook.is_file():
        return None
    m = re.search(r'METHOD_REQUIRED_SUBCMDS="([^"]*)"', hook.read_text())
    return set(m.group(1).split()) if m else None


# Subcommands that legitimately take NO --method (so they must NOT be in the
# hook's method-required list): pure lookup / structure build.
_NO_METHOD_SUBCMDS = {"resolve", "build"}


def lint_registry_sync() -> List[str]:
    """Return cross-registry drift problems ([] = in sync). Imports assay_core."""
    problems: List[str] = []
    import sys
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    try:
        from assay_core import discovery
    except Exception as e:  # noqa: BLE001
        return [f"cannot import assay_core.discovery: {e}"]

    infos = discovery.discover_skills(refresh=True)
    disc_subs = {info.subcommand for info in infos.values()}

    # server TOOLS
    try:
        from mcp_server import server
        server_subs = {sub for (sub, _folder) in server.TOOLS.values()}
        if server_subs != disc_subs:
            problems.append(
                f"server.TOOLS subcommands != discovery: "
                f"only-server={server_subs - disc_subs}, only-disc={disc_subs - server_subs}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"cannot import mcp_server.server: {e}")

    # hook METHOD_REQUIRED_SUBCMDS: must equal discovered subcommands that take
    # --method (i.e. all discovered minus the no-method ones).
    hook_subs = _hook_method_required_subcmds()
    if hook_subs is None:
        problems.append("could not read METHOD_REQUIRED_SUBCMDS from the hook")
    else:
        expected = disc_subs - _NO_METHOD_SUBCMDS
        if hook_subs != expected:
            problems.append(
                f"hook METHOD_REQUIRED_SUBCMDS out of sync with skills: "
                f"missing={sorted(expected - hook_subs)}, "
                f"extra={sorted(hook_subs - expected)} "
                f"(regenerate from manifests)")
    return problems


# ---------------------------------------------------------------------------
# Dependency-DAG lint (DESIGN.md §5/§8): a composite skill imports its sibling
# skills' run() DIRECTLY (from skills.<pkg>.scripts...). Verify each skill's
# declared `depends_on:` frontmatter matches its ACTUAL sibling imports, that
# every declared dep exists, and that the whole graph is acyclic.
# ---------------------------------------------------------------------------

def _pkg_to_name() -> Dict[str, str]:
    """skill package dir -> kebab display name (from each SKILL.md frontmatter)."""
    out: Dict[str, str] = {}
    for d in _SKILLS.iterdir():
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        md = d / "SKILL.md"
        if md.is_file():
            fm = _parse_frontmatter(md.read_text()) or {}
            out[d.name] = fm.get("name", d.name)
    return out


def _declared_deps(md_text: str) -> List[str]:
    """Parse a `depends_on: [a, b]` (or empty) frontmatter flow-list -> [names]."""
    fm = _parse_frontmatter(md_text) or {}
    raw = fm.get("depends_on", "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.strip("[]").split(",") if x.strip()]


def _actual_sibling_imports(run_py: Path, self_pkg: str) -> set:
    """Sibling skill PACKAGES this run.py imports from (from skills.<pkg>.scripts...)."""
    if not run_py.is_file():
        return set()
    pkgs = set(re.findall(r"from skills\.(\w+)\.scripts", run_py.read_text()))
    pkgs.discard(self_pkg)
    return pkgs


def lint_dependency_dag() -> List[str]:
    """Return DAG problems ([] = clean): declared==actual deps, deps exist, acyclic."""
    problems: List[str] = []
    pkg2name = _pkg_to_name()
    name2pkg = {v: k for k, v in pkg2name.items()}
    graph: Dict[str, set] = {}

    for pkg, name in sorted(pkg2name.items()):
        md = _SKILLS / pkg / "SKILL.md"
        run_py = _SKILLS / pkg / "scripts" / "run.py"
        declared = set(_declared_deps(md.read_text() if md.is_file() else ""))
        actual_pkgs = _actual_sibling_imports(run_py, pkg)
        actual = {pkg2name.get(p, p) for p in actual_pkgs}

        # declared deps must exist as skills
        for dep in declared:
            if dep not in name2pkg:
                problems.append(f"{name}: declared dep {dep!r} is not a known skill")
        # declared must match actual sibling imports (both directions)
        if declared != actual:
            missing = actual - declared
            extra = declared - actual
            if missing:
                problems.append(
                    f"{name}: imports {sorted(missing)} but does not declare them in depends_on")
            if extra:
                problems.append(
                    f"{name}: declares depends_on {sorted(extra)} it does not import")
        graph[name] = actual

    # acyclicity (DFS)
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}

    def visit(n: str, stack: List[str]) -> bool:
        color[n] = GREY
        for m in sorted(graph.get(n, ())):
            if m not in graph:
                continue
            if color[m] == GREY:
                problems.append(f"dependency cycle: {' -> '.join(stack + [n, m])}")
                return True
            if color[m] == WHITE and visit(m, stack + [n]):
                return True
        color[n] = BLACK
        return False

    for n in sorted(graph):
        if color[n] == WHITE:
            visit(n, [])
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="lint SKILL.md + skill spine + registry sync + dep DAG")
    ap.add_argument("skill", nargs="?", help="one skill name (default: all)")
    ap.add_argument("--spine", action="store_true", help="also run the run.py spine lint")
    ap.add_argument("--registry", action="store_true", help="also run the registry-sync lint")
    ap.add_argument("--dag", action="store_true", help="also run the dependency-DAG lint")
    ap.add_argument("--all", action="store_true", help="run every lint")
    args = ap.parse_args()

    if args.all:
        args.spine = args.registry = args.dag = True

    if args.skill:
        dirs = [_SKILLS / args.skill]
    else:
        dirs = sorted(d for d in _SKILLS.iterdir()
                      if d.is_dir() and not d.name.startswith("_"))

    rc = 0

    n_bad = 0
    for d in dirs:
        problems = lint_skill(d)
        if problems:
            n_bad += 1
            print(f"[FAIL] {d.name}")
            for p in problems:
                print(f"        - {p}")
    total = len(dirs)
    print(f"{total - n_bad}/{total} skills pass SKILL.md lint"
          + (f" ({n_bad} with problems)" if n_bad else " — all clean"))
    if n_bad:
        rc = 1

    if args.spine:
        spine_bad = lint_all_spines()
        if args.skill:
            spine_bad = {k: v for k, v in spine_bad.items() if k == args.skill}
        for name, probs in sorted(spine_bad.items()):
            print(f"[SPINE FAIL] {name}")
            for p in probs:
                print(f"        - {p}")
        n = len(list(_SKILLS.glob("*/scripts/run.py")))
        print(f"{n - len(spine_bad)}/{n} skill spines OK"
              + (f" ({len(spine_bad)} with problems)" if spine_bad else " — all clean"))
        if spine_bad:
            rc = 1

    if args.registry:
        reg = lint_registry_sync()
        if reg:
            print("[REGISTRY-SYNC FAIL]")
            for p in reg:
                print(f"        - {p}")
            rc = 1
        else:
            print("registry in sync (discovery == server.TOOLS == hook subcmds)")

    if args.dag:
        dag = lint_dependency_dag()
        if dag:
            print("[DEPENDENCY-DAG FAIL]")
            for p in dag:
                print(f"        - {p}")
            rc = 1
        else:
            print("dependency DAG OK (depends_on == sibling imports, deps exist, acyclic)")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
