"""Shared pytest configuration.

The regression suite drives skills as SUBPROCESSES (`python skills/<name>/
scripts/run.py ...`) launched from a tmp cwd, which is the honest way to test
the CLI spine. Those children import `assay_core` and `skills.*`, so the repo
root has to be on their sys.path. When the package is pip-installed (as CI does)
that happens for free; in a plain working copy it does not, and the failure is
badly misleading:

  * ~20 tests fail with `ModuleNotFoundError: No module named 'assay_core'`;
  * worse, `_have_pyscf()` probes availability by running the same script and
    matching its stderr for a pyscf import error. The child dies on `assay_core`
    FIRST, that string never appears, so the probe reports pyscf as PRESENT and
    every dft/hf test runs and fails instead of skipping.

One missing path entry therefore reads as ~50 broken tests. Exporting PYTHONPATH
for the session makes the suite behave identically installed or not.
"""
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: tests that take >30 s (organics, CREST runs, multi-step pipelines)",
    )

    # In-process imports (tests/test_cli_interface.py imports assay_core directly).
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    # Subprocess imports: every skill child inherits this. Prepend rather than
    # overwrite so an intentional PYTHONPATH from the caller still applies, and
    # skip it when the repo root is already there.
    existing = os.environ.get("PYTHONPATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if str(_REPO_ROOT) not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([str(_REPO_ROOT), *parts])
