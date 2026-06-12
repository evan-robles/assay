#!/usr/bin/env python3
"""Generate self-contained, SINGLE-FILE skill scripts.

Each skill becomes skills/<name>/ containing exactly:
  SKILL.md            - the doc + a "Running this skill" section
  <name>.py           - ONE self-contained script: a bootstrap that registers the
                        bundled engine modules into sys.modules under their real
                        names, the embedded module sources, and a launcher that
                        calls the chemkit CLI pinned to this skill's subcommand.
  requirements.txt    - pip deps for this skill + external-binary notes

There is NO _engine/ directory: the engine is inlined into <name>.py. Module
identity is preserved (each engine module keeps its own namespace via a lazy
in-memory import loader), so tasks that share top-level names (every task has
run(); several share _run_mopac, etc.) do NOT collide — `opt.run` and
`freq.run` stay distinct.

Source of truth: a chemkit source tree. Resolution order:
  1. $CHEMKIT_SRC (a path to a src/chemkit tree), if set;
  2. ./src/chemkit in the repo, if present;
  3. otherwise restored from git ($CHEMKIT_SRC_REF, default HEAD) into a temp
     dir — src/chemkit was removed from the working tree once skills became
     self-contained, so this is the normal path.
Package-relative imports are rewritten to folder-local `_engine.*` names and the
modules are embedded (readable raw blocks; per-module base64 only if a source
contains both triple-quote styles).

Run from the repo root:  python tools/build_skill_folders.py
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(REPO, "skills")

# The engine source of truth. src/chemkit was removed from the working tree once
# the skills became self-contained, so by default we restore it from git into a
# temp dir. Override with CHEMKIT_SRC=/path/to/src/chemkit to use a live tree.
SRC_ENV = os.environ.get("CHEMKIT_SRC")
SRC_GIT_REF = os.environ.get("CHEMKIT_SRC_REF", "HEAD")

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

BACKENDS_RELPATHS = [
    "backends/__init__.py",
    "backends/pyscf/__init__.py",
    "backends/pyscf/calculator.py",
    "backends/pyscf/dft.py",
    "backends/pyscf/hf.py",
    "backends/pyscf/molecule.py",
    "backends/pyscf/scf.py",
]
# Engine root files (always bundled).
ROOT_RELPATHS = ["__init__.py", "io.py", "schema.py", "calculators.py", "cli.py"]
TASKS_ALWAYS = ["tasks/__init__.py", "tasks/_mopac_parsers.py"]

SENTINEL = "###@@@CHEMKIT_MODULE_BOUNDARY@@@###"


# ---------------------------------------------------------------------------
# Import rewriting: package-relative imports -> folder-local `_engine.*`.
# (Same scheme used by the earlier _engine bundling; the embedded modules are
# registered under exactly these `_engine.*` names by the bootstrap.)
# ---------------------------------------------------------------------------
TASK_REWRITES = [
    (re.compile(r"\bfrom \.([a-z_][a-z0-9_]*) import"), r"from _engine.tasks.\1 import"),
    (re.compile(r"\bfrom \. import\b"), "from _engine.tasks import"),
    (re.compile(r"\bfrom \.\.([a-z_][a-z0-9_.]*)"), r"from _engine.\1"),
]
ROOT_REWRITES = [
    (re.compile(r"\bfrom \.([A-Za-z_][\w.]*) import"), r"from _engine.\1 import"),
    (re.compile(r"\bfrom \. import\b"), "from _engine import"),
]
ROOT_FILES = {"__init__.py", "io.py", "schema.py", "calculators.py", "cli.py",
              "resolve.py"}


def _rewrite(text: str, *, rules) -> str:
    out = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("from __future__"):
            out.append(line)
            continue
        for pat, repl in rules:
            line = pat.sub(repl, line)
        out.append(line)
    return "".join(out)


def _engine_src_dir() -> str:
    """Return a path to a chemkit source tree, restoring from git if needed."""
    if SRC_ENV and os.path.isdir(SRC_ENV):
        return SRC_ENV
    live = os.path.join(REPO, "src", "chemkit")
    if os.path.isdir(live):
        return live
    # Restore from git (src/chemkit was deleted once skills went self-contained).
    tmp = tempfile.mkdtemp(prefix="chemkit_src_")
    subprocess.run(
        "git archive %s src/chemkit | tar -x -C %s" % (SRC_GIT_REF, tmp),
        shell=True, cwd=REPO, check=True,
    )
    restored = os.path.join(tmp, "src", "chemkit")
    if not os.path.isdir(restored):
        sys.exit(
            "Could not restore src/chemkit from git ref %r. Set CHEMKIT_SRC to a "
            "chemkit source tree, or CHEMKIT_SRC_REF to a ref that contains it."
            % SRC_GIT_REF
        )
    return restored


# relpath (e.g. "tasks/opt.py") -> source text (rewritten to _engine.*)
def collect_module_sources() -> dict:
    src_dir = _engine_src_dir()
    sources: dict[str, str] = {}
    for root, _dirs, files in os.walk(src_dir):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            abspath = os.path.join(root, fn)
            rel = os.path.relpath(abspath, src_dir)
            with open(abspath) as f:
                text = f.read()
            # backends/* keep their same-package `from .x` imports (siblings
            # inside _engine.backends.pyscf) — do NOT rewrite them.
            if rel.startswith("backends" + os.sep) or rel.startswith("backends/"):
                pass
            elif rel.startswith("tasks" + os.sep) or rel.startswith("tasks/"):
                text = _rewrite(text, rules=TASK_REWRITES)
            elif os.path.basename(rel) in ROOT_FILES:
                text = _rewrite(text, rules=ROOT_REWRITES)
            sources[rel.replace(os.sep, "/")] = text
    return sources


def _relpath_to_modname(rel: str) -> str:
    """tasks/opt.py -> _engine.tasks.opt ; __init__.py -> _engine ;
    backends/pyscf/__init__.py -> _engine.backends.pyscf"""
    no_ext = rel[:-3] if rel.endswith(".py") else rel
    parts = no_ext.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["_engine", *parts]) if parts else "_engine"


def _is_package(rel: str) -> bool:
    return os.path.basename(rel) == "__init__.py"


# ---------------------------------------------------------------------------
# Single-file emission
# ---------------------------------------------------------------------------
BOOTSTRAP = '''#!/usr/bin/env python3
"""Self-contained `{name}` skill — chemistry engine inlined.

This single file bundles everything the `{name}` skill needs. It registers the
embedded engine modules into sys.modules under their real names (preserving each
module's namespace, so tasks that share function names like run()/_run_mopac do
NOT collide), then runs the chemkit CLI pinned to the `{task}` subcommand.

Run standalone:  python {name}.py --help
"""
import base64 as _b64
import importlib.abc as _iabc
import importlib.machinery as _imach
import sys as _sys

# Lazy in-memory loader: the embedded module sources are exec'd by Python's
# normal import machinery ON FIRST IMPORT, so dependency order is driven by the
# actual `import` statements (not by us). Each module keeps its own namespace,
# so tasks that share top-level names (run(), _run_mopac, ...) never collide.
class _EmbeddedFinder(_iabc.MetaPathFinder, _iabc.Loader):
    def __init__(self, modules):
        # name -> (is_package, source_text)
        self._mods = {{}}
        for modname, is_pkg, payload, is_b64 in modules:
            src = _b64.b64decode(payload).decode("utf-8") if is_b64 else payload
            self._mods[modname] = (is_pkg, src)

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self._mods:
            return None
        is_pkg, _src = self._mods[fullname]
        return _imach.ModuleSpec(fullname, self, is_package=is_pkg)

    def create_module(self, spec):
        return None  # default module creation

    def exec_module(self, module):
        is_pkg, src = self._mods[module.__name__]
        if is_pkg:
            # Mark as a package so submodule + relative imports resolve.
            module.__path__ = []
        exec(compile(src, "<embedded:%s>" % module.__name__, "exec"),
             module.__dict__)


def _register_embedded(_MODULES):
    _sys.meta_path.insert(0, _EmbeddedFinder(_MODULES))


'''

LAUNCHER = '''

# --- launcher ---------------------------------------------------------------
from _engine.cli import main as _main  # noqa: E402

if __name__ == "__main__":
    _sys.exit(_main(["{task}", *_sys.argv[1:]]))
'''


def _module_entry(modname: str, is_pkg: bool, src: str) -> str:
    # Emit an embedded-source entry, preferring a READABLE raw triple-quoted
    # block. Pick whichever triple-quote delimiter the source does NOT contain
    # (triple-double vs triple-single); fall back to base64 only in the rare
    # case the source contains BOTH, or ends in a backslash (raw strings can't).
    # Engine modules use docstrings but never both quote styles, so the readable
    # path is taken for every module in practice.
    can_dq = '"""' not in src
    can_sq = "'''" not in src
    trailing_backslash = src.endswith("\\")
    if trailing_backslash or (not can_dq and not can_sq):
        payload = repr(base64.b64encode(src.encode()).decode())
        return f"    ({modname!r}, {is_pkg!r}, {payload}, True),\n"
    delim = '"""' if can_dq else "'''"
    return f'    ({modname!r}, {is_pkg!r}, r{delim}{src}{delim}, False),\n'


def _emit_single_file(name: str, task: str, relpaths: list, sources: dict) -> str:
    parts = [BOOTSTRAP.format(name=name, task=task)]
    parts.append("_EMBEDDED = [\n")
    n = 0
    for rel in relpaths:
        src = sources[rel]
        modname = _relpath_to_modname(rel)
        parts.append(_module_entry(modname, _is_package(rel), src))
        n += 1
    parts.append("]\n\n_register_embedded(_EMBEDDED)\n")
    parts.append(LAUNCHER.format(task=task))
    return "".join(parts), n


def _ordered_relpaths(closure, needs_backends, needs_resolve) -> list:
    rels = list(ROOT_RELPATHS)
    if needs_resolve:
        rels.append("resolve.py")
    rels += list(TASKS_ALWAYS)
    for t in closure:
        rels.append(f"tasks/{t}.py")
    if needs_backends:
        rels += list(BACKENDS_RELPATHS)
    # de-dup preserving order
    seen, out = set(), []
    for r in rels:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# requirements.txt + SKILL.md (unchanged behavior, minor wording)
# ---------------------------------------------------------------------------
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
        "#   openbabel  conda install -c conda-forge openbabel  (obabel/obenergy: SMILES->3D, name lookup, confab)",
    ]
    return "\n".join(lines) + "\n"


RUNNING_SECTION = """
## Running this skill

This skill is a single self-contained script. From inside the folder:

```bash
pip install -r requirements.txt        # Python deps (see file for external binaries)
python {script} --help                 # full argument list
```

The chemistry engine is inlined into `{script}`; no other files are required.
"""


def build_skill(name: str, sources: dict):
    task, closure, needs_backends, needs_resolve, needs_mpl = SKILLS_MANIFEST[name]
    folder = os.path.join(SKILLS, name)
    os.makedirs(folder, exist_ok=True)

    relpaths = _ordered_relpaths(closure, needs_backends, needs_resolve)
    missing = [r for r in relpaths if r not in sources]
    if missing:
        raise SystemExit(f"{name}: missing engine sources: {missing}")

    script, n_modules = _emit_single_file(name, task, relpaths, sources)
    script_name = f"{name}.py"
    with open(os.path.join(folder, script_name), "w") as f:
        f.write(script)

    with open(os.path.join(folder, "requirements.txt"), "w") as f:
        f.write(_requirements(needs_backends, needs_resolve, needs_mpl))

    # SKILL.md: keep existing if present; refresh the Running section wording.
    skill_md = os.path.join(folder, "SKILL.md")
    body = ""
    if os.path.isfile(skill_md):
        with open(skill_md) as f:
            body = f.read()
    if "## Running this skill" in body:
        body = body.split("## Running this skill")[0].rstrip() + "\n"
    body = body.rstrip() + "\n" + RUNNING_SECTION.format(script=script_name)
    with open(skill_md, "w") as f:
        f.write(body)

    # Remove the obsolete _engine/ tree.
    engine = os.path.join(folder, "_engine")
    if os.path.isdir(engine):
        shutil.rmtree(engine)

    return n_modules


def main():
    sources = collect_module_sources()
    if not sources:
        sys.exit(
            "No engine sources found. Set CHEMKIT_SRC to a chemkit source tree "
            "or ensure git ref %r contains src/chemkit." % SRC_GIT_REF
        )
    total = 0
    for name in SKILLS_MANIFEST:
        n = build_skill(name, sources)
        total += 1
        print(f"  built {name}/{name}.py  ({n} embedded module(s))")
    print(f"\nGenerated {total} single-file skill scripts under {SKILLS}/")


if __name__ == "__main__":
    main()
