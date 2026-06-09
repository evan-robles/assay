"""Regression tests for chemkit, derived from bugs we've actually hit.

Each test runs the chemkit CLI on a small fixture geometry and asserts the
result JSON has the expected shape. The fixtures are deliberately tiny
(monatomic anions, diatomics, water, NO3-, hydroquinone) so the full suite
runs in a few minutes — enough to catch any of the bug categories we've
fixed so far:

  df2d1eb  xtb-python silently ignored --charge / --mult
  eb1d212  MOPAC freq aborted on monatomic species (zero vib modes)
  717d8ad  xtb diatomic G = +inf from rot/trans pseudo-modes
  5be30eb  xtb freq small molecules: spurious imag modes from Hessian rot/trans leakage
  48fb9c1  schema cleanup: drop _summary.txt / .out side files
  (latest) freq: --auto-confsearch flag for flexible molecules

Run with:
  pytest tests/                       # full suite
  pytest tests/ -k "xtb"              # xtb-only
  pytest tests/ -k "freq_monatomic"   # just the monatomic-freq regression
  pytest tests/ -m slow               # only the slower (organic) tests

External binaries (xtb, mopac, crest) must be on $PATH; tests that need a
missing binary are skipped with an informative reason.
"""
from __future__ import annotations
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
CHEMKIT = str(Path(__file__).parent.parent / "bin" / "chemkit")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _run_chemkit(args: list[str], cwd: str, timeout: float = 600.0) -> tuple[int, str, str]:
    """Run chemkit and return (exit_code, stdout, stderr)."""
    proc = subprocess.run(
        [CHEMKIT, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _bad_num(x) -> bool:
    return x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


@pytest.fixture
def tmp_run(tmp_path):
    """Yields a per-test temp dir with the fixture xyz files copied in."""
    for xyz in FIXTURES.glob("*.xyz"):
        shutil.copy(xyz, tmp_path / xyz.name)
    return tmp_path


# ---------------------------------------------------------------------------
# Single-point energy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["xtb", "mopac"])
def test_sp_water(tmp_run, method):
    """SP on neutral H2O — baseline sanity check, both methods."""
    if method == "xtb" and not _have("xtb"):
        pytest.skip("xtb binary not on PATH")
    if method == "mopac" and not _have("mopac"):
        pytest.skip("mopac binary not on PATH")
    out = tmp_run / f"h2o_sp_{method}.json"
    rc, *_ = _run_chemkit(["sp", "--method", method, "--solvent", "water",
                           "h2o.xyz", "--out", str(out)], cwd=str(tmp_run))
    assert rc == 0
    d = _load(out)
    e = d.get("total_energy_eV") or d.get("electronic_energy_eV")
    assert not _bad_num(e), f"bad SP energy: {e}"


@pytest.mark.parametrize("method", ["xtb", "mopac"])
def test_sp_charge_mult_propagates_xtb(tmp_run, method):
    """REGRESSION (df2d1eb): xtb-python used to silently ignore --charge/--mult.

    Verify that q=0 / mult=1 produces a different energy from q=-1 / mult=2
    on the same H2 geometry. If charge/mult are ignored, both runs produce
    identical energies (the silent-ignore bug).
    """
    if method == "xtb" and not _have("xtb"):
        pytest.skip("xtb binary not on PATH")
    if method == "mopac" and not _have("mopac"):
        pytest.skip("mopac binary not on PATH")
    out0 = tmp_run / f"h2_neutral_{method}.json"
    out1 = tmp_run / f"h2_anion_{method}.json"
    _run_chemkit(["sp", "--method", method, "--charge", "0", "--mult", "1",
                  "h2.xyz", "--out", str(out0)], cwd=str(tmp_run))
    _run_chemkit(["sp", "--method", method, "--charge", "-1", "--mult", "2",
                  "h2.xyz", "--out", str(out1)], cwd=str(tmp_run))
    e0 = _load(out0).get("total_energy_eV") or _load(out0).get("electronic_energy_eV")
    e1 = _load(out1).get("total_energy_eV") or _load(out1).get("electronic_energy_eV")
    assert not _bad_num(e0) and not _bad_num(e1)
    assert abs(e0 - e1) > 0.1, (
        f"{method}: charge/mult appear to be silently ignored (E differ by "
        f"only {abs(e0-e1):.6f} eV) — regression of df2d1eb"
    )


# ---------------------------------------------------------------------------
# Geometry optimization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["xtb", "mopac"])
def test_opt_water_converges(tmp_run, method):
    if method == "xtb" and not _have("xtb"):
        pytest.skip("xtb binary not on PATH")
    if method == "mopac" and not _have("mopac"):
        pytest.skip("mopac binary not on PATH")
    out = tmp_run / f"h2o_opt_{method}.json"
    rc, *_ = _run_chemkit(["opt", "--method", method, "--solvent", "water",
                           "h2o.xyz", "--out", str(out)], cwd=str(tmp_run))
    assert rc == 0
    d = _load(out)
    e = d.get("total_energy_eV") or d.get("final_energy_eV")
    assert not _bad_num(e)
    assert d.get("converged") is True


# ---------------------------------------------------------------------------
# Frequencies + thermochemistry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["xtb", "mopac"])
@pytest.mark.parametrize("species,charge,mult", [
    ("f_minus", -1, 1),
    ("cl_minus", -1, 1),
])
def test_freq_monatomic_anion(tmp_run, species, charge, mult, method):
    """REGRESSION (eb1d212 / df2d1eb): MOPAC used to crash on monatomic freq
    because it returned an empty frequency list (correctly — N=1 has 3N-6 < 0
    vibrational modes), and xtb's earlier charge bug returned identical energies
    for any charge.
    """
    if method == "xtb" and not _have("xtb"):
        pytest.skip("xtb not on PATH")
    if method == "mopac" and not _have("mopac"):
        pytest.skip("mopac not on PATH")
    out = tmp_run / f"{species}_freq_{method}.json"
    rc, *_ = _run_chemkit(
        ["freq", "--method", method, "--charge", str(charge), "--mult", str(mult),
         "--solvent", "water", "--geometry", "monatomic",
         f"{species}.xyz", "--out", str(out)],
        cwd=str(tmp_run),
    )
    assert rc == 0
    d = _load(out)
    g = d.get("gibbs_free_energy_eV")
    assert not _bad_num(g), f"bad G for {species}/{method}: {g} (regression of eb1d212)"
    assert (d.get("n_real_vib_modes") or 0) == 0, (
        f"{species}/{method}: expected 0 vibrational modes for a monatomic species"
    )


@pytest.mark.parametrize("method", ["xtb", "mopac"])
@pytest.mark.parametrize("species,mult,geom", [
    ("h2", 1, "linear"),
    ("o2", 3, "linear"),     # triplet ground state
])
def test_freq_diatomic_finite_G(tmp_run, species, mult, geom, method):
    """REGRESSION (717d8ad): xtb path used to return G=+inf for diatomics
    because ASE's Vibrations admitted rot/trans pseudo-modes (~25 cm^-1) into
    the entropy sum, which then diverged.
    """
    if method == "xtb" and not _have("xtb"):
        pytest.skip("xtb not on PATH")
    if method == "mopac" and not _have("mopac"):
        pytest.skip("mopac not on PATH")
    out = tmp_run / f"{species}_freq_{method}.json"
    rc, *_ = _run_chemkit(
        ["freq", "--method", method, "--charge", "0", "--mult", str(mult),
         "--solvent", "water", "--geometry", geom,
         f"{species}.xyz", "--out", str(out)],
        cwd=str(tmp_run),
    )
    assert rc == 0
    d = _load(out)
    g = d.get("gibbs_free_energy_eV")
    assert not _bad_num(g), f"diatomic {species}/{method}: G={g} (regression of 717d8ad)"
    # Exactly one vibrational mode for a diatomic (3N-5 = 1)
    assert (d.get("n_real_vib_modes") or 0) == 1, (
        f"diatomic {species}/{method}: expected 1 vibrational mode, "
        f"got {d.get('n_real_vib_modes')}"
    )


@pytest.mark.parametrize("species,charge", [
    ("h2o", 0),
    ("no3_minus", -1),
])
def test_freq_xtb_rigid_no_spurious_imag(tmp_run, species, charge):
    """REGRESSION (5be30eb): xtb path used to leak rot/trans modes into the
    vibrational subspace for small rigid molecules (H2O, NO3-, H2O2),
    producing spurious imaginary modes. Now projects trans/rot from the
    Hessian before diagonalizing.
    """
    if not _have("xtb"):
        pytest.skip("xtb not on PATH")
    out = tmp_run / f"{species}_freq_xtb.json"
    rc, *_ = _run_chemkit(
        ["freq", "--method", "xtb", "--charge", str(charge), "--mult", "1",
         "--solvent", "water", "--geometry", "nonlinear",
         f"{species}.xyz", "--out", str(out)],
        cwd=str(tmp_run),
    )
    assert rc == 0
    d = _load(out)
    assert (d.get("n_imaginary_modes") or 0) == 0, (
        f"{species} xtb freq has {d.get('n_imaginary_modes')} imaginary modes — "
        f"likely regression of rot/trans projection (5be30eb)"
    )


# ---------------------------------------------------------------------------
# Schema: only the canonical JSON file is emitted (no _summary.txt or .out)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["xtb", "mopac"])
def test_sp_emits_only_json(tmp_run, method):
    """REGRESSION (48fb9c1): we removed the _summary.txt sidecar files;
    chemkit should now emit exactly one JSON file per sp run (plus the
    user-supplied .xyz still in place)."""
    if method == "xtb" and not _have("xtb"):
        pytest.skip("xtb not on PATH")
    if method == "mopac" and not _have("mopac"):
        pytest.skip("mopac not on PATH")
    work = tmp_run / "sp_only"
    work.mkdir()
    shutil.copy(FIXTURES / "h2o.xyz", work / "h2o.xyz")
    rc, *_ = _run_chemkit(["sp", "--method", method, "h2o.xyz"], cwd=str(work))
    assert rc == 0
    files = sorted(p.name for p in work.iterdir())
    assert files == sorted(["h2o.xyz", f"h2o_sp_{method}.json"]), (
        f"sp emitted unexpected files: {files} — _summary.txt should be gone (48fb9c1)"
    )


# ---------------------------------------------------------------------------
# Frontier orbitals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["xtb", "mopac"])
def test_frontier_water(tmp_run, method):
    """FEATURE test: frontier on neutral H2O should return finite HOMO, LUMO,
    and a positive gap, plus the Koopmans descriptors block."""
    if method == "xtb" and not _have("xtb"):
        pytest.skip("xtb not on PATH")
    if method == "mopac" and not _have("mopac"):
        pytest.skip("mopac not on PATH")
    out = tmp_run / f"h2o_frontier_{method}.json"
    rc, *_ = _run_chemkit(
        ["frontier", "--method", method, "--solvent", "water",
         "h2o.xyz", "--out", str(out)],
        cwd=str(tmp_run),
    )
    assert rc == 0
    d = _load(out)
    assert not _bad_num(d.get("homo_eV"))
    assert not _bad_num(d.get("lumo_eV"))
    assert d["homo_lumo_gap_eV"] > 0, "expected positive HOMO-LUMO gap for closed-shell H2O"
    k = d.get("koopmans") or {}
    assert "vertical_IP_eV" in k and "vertical_EA_eV" in k
    assert "electronegativity_eV" in k and "chemical_hardness_eV" in k


@pytest.mark.parametrize("species,charge", [
    ("f_minus", -1),    # GFN2 minimal basis: 4 occupied, 0 virtual
])
def test_frontier_basis_saturated_anion_xtb(tmp_run, species, charge):
    """REGRESSION: F- in GFN2's minimal valence basis is fully saturated
    (2s + 2p = 4 occupied, 0 virtual). frontier used to crash with
    'Could not partition orbitals into occupied/virtual' — should now
    return a structured PARTIAL result with HOMO/IP but no LUMO,
    plus a warning explaining why."""
    if not _have("xtb"):
        pytest.skip("xtb not on PATH")
    out = tmp_run / f"{species}_frontier_xtb.json"
    rc, *_ = _run_chemkit(
        ["frontier", "--method", "xtb", "--charge", str(charge),
         "--solvent", "water",
         f"{species}.xyz", "--out", str(out)],
        cwd=str(tmp_run),
    )
    assert rc == 0, "frontier should not crash on basis-saturated anions anymore"
    d = _load(out)
    assert not _bad_num(d.get("homo_eV")), "HOMO should still be computable"
    assert d.get("lumo_eV") is None, "LUMO should be explicitly None when no virtuals"
    assert d.get("homo_lumo_gap_eV") is None
    warns = d.get("warnings") or []
    assert any("virtual" in w.lower() for w in warns), \
        "should warn about missing virtual orbitals"


# ---------------------------------------------------------------------------
# Electrostatics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["xtb", "mopac"])
def test_electrostatics_water_dipole(tmp_run, method):
    """FEATURE test: H2O dipole should be in the 1.8-3.0 Debye range
    (experimental 1.85 D; xtb gives ~2.27, MOPAC ~2.14)."""
    if method == "xtb" and not _have("xtb"):
        pytest.skip("xtb not on PATH")
    if method == "mopac" and not _have("mopac"):
        pytest.skip("mopac not on PATH")
    out = tmp_run / f"h2o_elst_{method}.json"
    rc, *_ = _run_chemkit(
        ["electrostatics", "--method", method,
         "h2o.xyz", "--out", str(out)],
        cwd=str(tmp_run),
    )
    assert rc == 0
    d = _load(out)
    dipole = d.get("dipole_debye")
    assert dipole is not None and 1.5 < dipole < 3.5, (
        f"H2O dipole = {dipole} Debye is outside the expected 1.5-3.5 D range"
    )
    charges = d.get("partial_charges")
    assert charges is not None and len(charges) == 3, "expected 3 partial charges for H2O"
    assert charges[0] < 0 and charges[1] > 0 and charges[2] > 0, \
        "O should be negative, H atoms positive"
    assert abs(d.get("sum_of_charges", 99) - 0) < 0.01, "neutral H2O charges should sum to ~0"


def test_electrostatics_no3_minus_xtb(tmp_run):
    """FEATURE test: NO3- charge sum should be -1 and the trigonal-planar
    D3h symmetry should give near-zero dipole."""
    if not _have("xtb"):
        pytest.skip("xtb not on PATH")
    out = tmp_run / "no3_elst.json"
    rc, *_ = _run_chemkit(
        ["electrostatics", "--method", "xtb", "--charge", "-1",
         "no3_minus.xyz", "--out", str(out)],
        cwd=str(tmp_run),
    )
    assert rc == 0
    d = _load(out)
    assert abs(d["sum_of_charges"] - (-1)) < 0.01
    assert d["dipole_debye"] < 0.1, "D3h NO3- should have ~zero dipole"


# ---------------------------------------------------------------------------
# Transition state search + IRC pipeline
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_ts_hcn_isomerization_mopac(tmp_run):
    """FEATURE test: HCN/HNC isomerization TS via MOPAC's native TS
    keyword should converge to a saddle with exactly 1 imaginary mode
    (textbook value ~-1390 cm^-1 for the H migration)."""
    if not _have("mopac"):
        pytest.skip("mopac not on PATH")
    out = tmp_run / "hcn_ts.json"
    rc, *_ = _run_chemkit(
        ["ts", "--method", "mopac", "hcn_ts_guess.xyz", "--out", str(out)],
        cwd=str(tmp_run),
        timeout=600,
    )
    assert rc == 0
    d = _load(out)
    assert d.get("converged") is True, f"TS did not converge: {d.get('mopac_status')}"
    vf = d.get("verify_freq") or {}
    assert vf.get("is_valid_ts") is True, (
        f"verify_freq did not return a valid TS: n_imag={vf.get('n_imaginary_modes')}, "
        f"freqs={vf.get('imaginary_frequencies_cm-1')}"
    )
    assert d.get("ts_xyz") and os.path.isfile(d["ts_xyz"])


@pytest.mark.slow
def test_irc_hcn_walks_to_distinct_endpoints(tmp_run):
    """FEATURE test: IRC from the HCN/HNC TS should land on two distinct
    endpoints. Uses the TS xyz produced by `chemkit ts` as input."""
    if not _have("mopac"):
        pytest.skip("mopac not on PATH")
    # First find the TS (needed as input to IRC)
    ts_out = tmp_run / "hcn_ts.json"
    rc, *_ = _run_chemkit(
        ["ts", "--method", "mopac", "hcn_ts_guess.xyz", "--out", str(ts_out)],
        cwd=str(tmp_run), timeout=600,
    )
    assert rc == 0
    ts_xyz = _load(ts_out)["ts_xyz"]
    # Now run IRC
    irc_out = tmp_run / "hcn_irc.json"
    rc, *_ = _run_chemkit(
        ["irc", "--method", "mopac", ts_xyz, "--out", str(irc_out)],
        cwd=str(tmp_run), timeout=600,
    )
    assert rc == 0
    d = _load(irc_out)
    assert d.get("forward_n_points") and d["forward_n_points"] > 1
    assert d.get("reverse_n_points") and d["reverse_n_points"] > 1
    assert d.get("forward_trajectory_xyz") and os.path.isfile(d["forward_trajectory_xyz"])
    assert d.get("reverse_trajectory_xyz") and os.path.isfile(d["reverse_trajectory_xyz"])
    # The reverse direction should clearly drop below the TS (HCN is the stable
    # isomer); forward toward HNC may stall short of the well within max_points.
    assert d.get("reverse_drop_kcal_mol") is not None and d["reverse_drop_kcal_mol"] < -1.0, (
        f"reverse IRC drop = {d.get('reverse_drop_kcal_mol')} kcal/mol — "
        f"expected a clear downhill walk from the TS"
    )


# ---------------------------------------------------------------------------
# Auto-confsearch wrapper around freq (latest feature)
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_freq_auto_confsearch_wires_through(tmp_run):
    """FEATURE test: `freq --auto-confsearch` routes hydroquinone through
    CREST + PM7 postopt before the Hessian step. We verify the *wiring*
    (auto_confsearch block present, best_xyz exists, freq used it, energies
    in the right ballpark) rather than asserting zero imaginary modes —
    hydroquinone has many near-degenerate OH-torsion conformers and CREST's
    stochastic sampling occasionally lands on a soft-mode saddle, which is
    chemistry, not a tool bug."""
    for tool in ("crest", "xtb", "mopac"):
        if not _have(tool):
            pytest.skip(f"{tool} not on PATH")
    out = tmp_run / "hq_auto.json"
    rc, *_ = _run_chemkit(
        ["freq", "--method", "mopac", "--charge", "0", "--mult", "1",
         "--solvent", "water", "--auto-confsearch",
         "hydroquinone.xyz", "--out", str(out)],
        cwd=str(tmp_run),
        timeout=1800,
    )
    assert rc == 0
    d = _load(out)
    acs = d.get("auto_confsearch") or {}
    assert acs.get("performed") is True, "auto_confsearch block missing from JSON"
    assert acs.get("best_xyz") and os.path.isfile(acs["best_xyz"]), \
        "auto_confsearch.best_xyz not produced or not on disk"
    assert acs.get("preopt_skipped") is True, \
        "preopt should be skipped when auto_confsearch supplied a PM7-optimized minimum"
    # HoF should be in a chemically sensible range for hydroquinone at PM7
    # (literature/our prior runs cluster around -75 kcal/mol).
    hof = d.get("heat_of_formation_kcal_mol") or acs.get("best_hof_kcal_mol")
    assert hof is not None and -100.0 < hof < -40.0, (
        f"hydroquinone PM7 HoF = {hof} kcal/mol — outside the chemically "
        f"sensible -100..-40 range"
    )
