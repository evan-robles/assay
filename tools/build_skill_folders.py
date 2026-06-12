#!/usr/bin/env python3
"""Generate self-contained skill folders (Layout B) from src/chemkit.

Each skill becomes skills/<name>/ containing:
  SKILL.md            - the doc (from skills/<name>.md) + a "Running this skill" section
  <name>.py           - standalone entry point: calls _engine.cli.main(["<task>", ...])
  requirements.txt    - pip deps for this skill + external-binary notes
  _engine/            - folder-local copy of ONLY the code this skill needs,
                        with all package-relative imports rewritten to `_engine.*`

The engine is bundled per-skill so the folder runs with nothing outside it
(no src/chemkit on the path). The per-skill <name>.py reuses the existing CLI
`main()` pinned to one subcommand, so the argument contract is preserved
verbatim rather than re-implemented.

Run from the repo root:  python tools/build_skill_folders.py
"""
from __future__ import annotations

import os
import re
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src", "chemkit")
SKILLS = os.path.join(REPO, "skills")

# --- shared infra always bundled (relative to src/chemkit) ------------------
SHARED_ALWAYS = [
    "__init__.py",
    "io.py",
    "schema.py",
    "calculators.py",
    "cli.py",
    "tasks/__init__.py",
    "tasks/_mopac_parsers.py",
]
BACKENDS_FILES = [
    "backends/__init__.py",
    "backends/pyscf/__init__.py",
    "backends/pyscf/calculator.py",
    "backends/pyscf/dft.py",
    "backends/pyscf/hf.py",
    "backends/pyscf/molecule.py",
    "backends/pyscf/scf.py",
]

# --- per-skill manifest -----------------------------------------------------
# name: (task subcommand, [task modules in transitive closure],
#        needs_backends, needs_resolve, needs_matplotlib)
SKILLS_MANIFEST = {
    "single_point_energy":     ("sp",            ["sp"],                                          True,  False, False),
    "geometry_optimize":       ("opt",           ["opt"],                                         True,  False, False),
    "vibrational_analysis":    ("freq",          ["freq", "opt", "confsearch"],                   True,  False, False),
    "binding_energy":          ("binding",       ["binding", "sp"],                               True,  False, False),
    "redox_potential":         ("redox",         ["redox", "sp"],                                 True,  False, False),
    "conformer_search":        ("confsearch",    ["confsearch", "opt"],                           False, False, False),
    "frontier_orbitals":       ("frontier",      ["frontier"],                                    True,  False, False),
    "electrostatics":          ("electrostatics",["electrostatics"],                              True,  False, False),
    "solvation":               ("solvation",     ["solvation", "sp"],                             True,  False, False),
    "logp":                    ("logp",          ["logp", "sp"],                                  True,  False, False),
    "reaction_profile":        ("profile",       ["reaction_profile", "opt", "freq", "ts",
                                                  "irc", "confsearch"],                           True,  False, True),
    "pka":                     ("pka",           ["pka", "freq", "opt", "confsearch"],            True,  False, False),
    "build_from_smiles":       ("build",         ["build", "opt"],                                False, True,  False),
    "fukui":                   ("fukui",         ["fukui", "electrostatics"],                     True,  False, True),
    "transition_state":        ("ts",            ["ts", "freq", "opt", "confsearch"],             True,  False, False),
    "irc":                     ("irc",           ["irc"],                                         True,  False, False),
    "reaction_energy":         ("rxn-energy",    ["reaction_energy", "sp", "opt", "freq",
                                                  "confsearch"],                                  True,  False, False),
    "conformational_analysis": ("scan",          ["scan", "confsearch", "opt"],                   True,  False, True),
}

# --- import rewriting -------------------------------------------------------
# Order matters: most specific patterns first.
REWRITES = [
    # tasks importing sibling tasks: `from .opt import X`, `from ._mopac_parsers import X`,
    # `from .confsearch import X`  ->  `from _engine.tasks.opt import X`
    (re.compile(r"\bfrom \.([a-z_][a-z0-9_]*) import"), r"from _engine.tasks.\1 import"),
    # `from . import opt`  ->  `from _engine.tasks import opt`
    (re.compile(r"\bfrom \. import\b"), "from _engine.tasks import"),
    # two-dot infra: `from ..calculators import` -> `from _engine.calculators import`
    #                `from ..backends.pyscf...`   -> `from _engine.backends.pyscf...`
    (re.compile(r"\bfrom \.\.([a-z_][a-z0-9_.]*)"), r"from _engine.\1"),
]
# Root-level modules (cli.py, calculators.py, __init__.py, resolve.py) live at
# _engine/ root, so their relative imports are single-dot and root-relative:
#   `from . import __version__`        -> `from _engine import __version__`
#   `from .io import ...`              -> `from _engine.io import ...`
#   `from .tasks import opt`           -> `from _engine.tasks import opt`
#   `from .tasks.sp import _x`         -> `from _engine.tasks.sp import _x`
#   `from .backends.pyscf import ...`  -> `from _engine.backends.pyscf import ...`
ROOT_REWRITES = [
    # `from .<dotted.path> import ...`  (covers .io, .tasks.sp, .backends.pyscf, ...)
    (re.compile(r"\bfrom \.([A-Za-z_][\w.]*) import"), r"from _engine.\1 import"),
    # `from . import name`
    (re.compile(r"\bfrom \. import\b"), "from _engine import"),
]


def _rewrite(text: str, *, is_root: bool) -> str:
    """Rewrite package-relative imports to folder-local _engine.* imports.

    `is_root` selects the ruleset for files that sit at _engine/ root
    (currently only cli.py and __init__.py); everything under tasks/ uses the
    task ruleset.
    """
    rules = ROOT_REWRITES if is_root else REWRITES
    out = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        # never touch __future__ / stdlib / third-party imports
        if stripped.startswith("from __future__"):
            out.append(line)
            continue
        new = line
        for pat, repl in rules:
            new = pat.sub(repl, new)
        out.append(new)
    return "".join(out)


def _copy_engine_file(rel_src: str, engine_dir: str, *, is_root: bool,
                      no_rewrite: bool = False):
    src_path = os.path.join(SRC, rel_src)
    dst_path = os.path.join(engine_dir, rel_src)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with open(src_path) as f:
        text = f.read()
    # Files under backends/ keep their original SAME-PACKAGE relative imports
    # (`from .calculator import ...`) — they are siblings inside
    # _engine/backends/pyscf/, so those imports are already correct and must
    # NOT be rewritten to _engine.*.
    if not no_rewrite:
        text = _rewrite(text, is_root=is_root)
    with open(dst_path, "w") as f:
        f.write(text)


def _entry_script(name: str, task: str) -> str:
    return f'''#!/usr/bin/env python3
"""Standalone entry point for the `{name}` skill.

Self-contained: imports only the bundled `_engine` package in this folder, so
this folder runs with nothing else on the path. Delegates to the chemkit CLI
pinned to the `{task}` subcommand, preserving the full argument contract.

Usage:  python {name}.py [args...]      (see --help)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _engine.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["{task}", *sys.argv[1:]]))
'''


def _requirements(needs_backends: bool, needs_resolve: bool, needs_mpl: bool) -> str:
    lines = [
        "# Python dependencies for this skill (pip install -r requirements.txt).",
        "ase>=3.22",
        "numpy",
    ]
    if needs_backends:
        lines.append("pyscf            # required for --method dft / --method hf")
    if needs_mpl:
        lines.append("matplotlib       # required for the PNG plot output")
    lines += [
        "",
        "# External binaries (NOT pip-installable — install separately, e.g. via conda):",
        "#   xtb      conda install -c conda-forge xtb       (--method xtb)",
        "#   mopac    conda install -c conda-forge mopac     (--method mopac / PM7 postopt)",
    ]
    if needs_resolve:
        lines += [
            "#   openbabel  conda install -c conda-forge openbabel  (obabel: SMILES->3D, name lookup)",
        ]
    if not needs_resolve:
        # confsearch (and any skill that may build/score with obabel) still notes it
        lines += [
            "#   openbabel  conda install -c conda-forge openbabel  (obabel/obenergy, if used)",
        ]
    return "\n".join(lines) + "\n"


RUNNING_SECTION = """
## Running this skill

This skill folder is self-contained. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python {script} --help                 # full argument list
```

The script bundles everything it needs under `_engine/`; no external package
is required on the path.
"""


def build_skill(name: str):
    task, closure, needs_backends, needs_resolve, needs_mpl = SKILLS_MANIFEST[name]
    folder = os.path.join(SKILLS, name)
    engine = os.path.join(folder, "_engine")
    # clean any prior generated engine
    if os.path.isdir(engine):
        shutil.rmtree(engine)
    os.makedirs(engine, exist_ok=True)

    # 1. shared infra. Files that sit at _engine/ root use ROOT rules
    #    (single-dot, root-relative imports); files under tasks/ use task rules.
    for rel in SHARED_ALWAYS:
        is_root = rel in ("__init__.py", "cli.py", "calculators.py")
        _copy_engine_file(rel, engine, is_root=is_root)
    # 2. closure task modules
    for t in closure:
        _copy_engine_file(f"tasks/{t}.py", engine, is_root=False)
    # 3. optional infra
    if needs_resolve:
        _copy_engine_file("resolve.py", engine, is_root=True)
    if needs_backends:
        for rel in BACKENDS_FILES:
            _copy_engine_file(rel, engine, is_root=False, no_rewrite=True)

    # 4. entry script
    script_name = f"{name}.py"
    with open(os.path.join(folder, script_name), "w") as f:
        f.write(_entry_script(name, task))

    # 5. requirements.txt
    with open(os.path.join(folder, "requirements.txt"), "w") as f:
        f.write(_requirements(needs_backends, needs_resolve, needs_mpl))

    # 6. SKILL.md from the existing skills/<name>.md
    md_src = os.path.join(SKILLS, f"{name}.md")
    skill_md = os.path.join(folder, "SKILL.md")
    body = ""
    if os.path.isfile(md_src):
        with open(md_src) as f:
            body = f.read()
    if "## Running this skill" not in body:
        body = body.rstrip() + "\n" + RUNNING_SECTION.format(script=script_name)
    with open(skill_md, "w") as f:
        f.write(body)

    return folder, len(closure)


def main():
    if not os.path.isdir(SRC):
        sys.exit(f"src/chemkit not found at {SRC} (needed as the bundling source).")
    built = []
    for name in SKILLS_MANIFEST:
        folder, ntasks = build_skill(name)
        built.append((name, ntasks))
        print(f"  built {name}/  (engine: {ntasks} task module(s))")
    print(f"\nGenerated {len(built)} skill folders under {SKILLS}/")


if __name__ == "__main__":
    main()
