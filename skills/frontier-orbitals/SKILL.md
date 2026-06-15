---
name: frontier-orbitals
description: Computes frontier orbital energies, the HOMO-LUMO gap, and Koopmans reactivity descriptors at a fixed geometry.
category: chemistry
---

# Frontier Orbitals

## Goal
Compute the HOMO and LUMO energies, the HOMO–LUMO gap, the $K$ neighbouring frontier orbitals on each side (HOMO−$K$..HOMO and LUMO..LUMO+$K$), and the standard Koopmans-based global reactivity descriptors (vertical IP, vertical EA, electronegativity $\chi$, hardness $\eta$, softness $S$, electrophilicity index $\omega$). Geometry is used as-is — no optimization.

## Instructions
1. Parse arguments. If the `.xyz` path is missing, stop and ask. If `--method` is missing, use **AskUserQuestion** (header "Method", options `xtb` / `mopac` / `dft` / `hf`).
2. Run the engine.

```bash
# Env: anl_env
python skills/frontier-orbitals/scripts/frontier-orbitals.py --method <xtb|mopac|dft|hf> [--tier <T>] [--functional <F>] [--basis <B>] [--solvent <S>] [--charge N] [--mult N] [--nfrontier K] input.xyz
```

Arguments:
- `input.xyz` — molecular geometry (required).
- `--method {xtb,mopac,dft,hf}` — **required**; backends are GFN2-xTB (xtb-python), PM7 (MOPAC), DFT (PySCF Kohn–Sham eigenvalues), HF (PySCF).
- `--solvent <name>` — implicit solvent (water, methanol, dmso, mecn, dcm, …).
- `--charge N`, `--mult N` — molecular charge and spin multiplicity.
- `--nfrontier K` — frontier orbitals on each side of the gap (default 3).
- DFT-only: `--tier {fast,standard,accurate}`, `--functional <libxc>`, `--basis <name>`.
- HF-only: `--basis <name>`.

Read the JSON and copy it to `<basename>_frontier_<method>.json` in the cwd. Report: HOMO, LUMO, and HOMO–LUMO gap (eV); the full frontier table (HOMO−$K$..HOMO, LUMO..LUMO+$K$) sorted by energy; the Koopmans descriptors from `koopmans` (vertical IP, vertical EA, $\chi$, $\eta$, $S$, $\omega$); method, solvent (or "gas phase"), charge, multiplicity; and the JSON path. For MOPAC, also surface heat of formation and dipole from `code_specific`. xtb and MOPAC orbital zeros differ — compare orbital energies only within the same method — and Koopmans values are first-order estimates (for quantitative IP/EA use ΔSCF with DFT). To relax the geometry first, run [geometry-optimize](../geometry-optimize/SKILL.md) beforehand; for a single-point energy see [single-point-energy](../single-point-energy/SKILL.md).

## Examples
```bash
# Env: anl_env
python skills/frontier-orbitals/scripts/frontier-orbitals.py --method xtb --nfrontier 3 mol.xyz
```
See [`examples/`](examples/) for a validated example with literature comparison.

## Constraints
- **Environment**: `# Env: anl_env` required for all calls.
- **Fixed geometry**: no optimization is performed; pass an already-relaxed structure.
- **Method required**: `--method` must be supplied (ask the user if absent).
- **Open-shell systems**: results are spin-restricted; flag `multiplicity > 1` in the report.
- **Reporting policy**: Never automatically provide experimental or literature data for comparison. Report only the values this calculation produced; compare to experiment only if the user explicitly asks.
- **Availability**: xtb / mopac via `conda install -c conda-forge xtb mopac`; xtb-python (orbital eigenvalues for the xtb path) via `conda install -c conda-forge xtb-python` or `pip install xtb`; pyscf via `pip install pyscf` (required for `--method dft` or `--method hf`). For a malformed `.xyz`, report which line failed.

## References
- Bannwarth, Ehlert, Grimme. "GFN2-xTB." *J. Chem. Theory Comput.* 2019, 15, 1652-1671. https://doi.org/10.1021/acs.jctc.8b01176
- Stewart. "Optimization of parameters for semiempirical methods VI: PM7." *J. Mol. Model.* 2013, 19, 1-32. https://doi.org/10.1007/s00894-012-1667-x
- Sun et al. "Recent developments in the PySCF program package." *J. Chem. Phys.* 2020, 153, 024109. https://doi.org/10.1063/5.0006074
- National Institute of Standards and Technology. *CODATA Internationally Recommended 2022 Values of the Fundamental Physical Constants*; NIST. https://physics.nist.gov/cuu/Constants/ (accessed 2026-06-15). Source of the Hartree↔eV conversion (1 Eh = 27.211386245981 eV) used to report orbital energies.

---

**Author:** Evan S. Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
