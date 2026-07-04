"""Interface-hardening tests for the chemkit engine CLI.

These cover the agent-/user-proofing added so callers hit the two dominant
fidelity-benchmark failure modes less often:

  * subcommand ALIASES (descriptive skill names resolve to terse subcommands),
  * did-you-mean on an unknown subcommand,
  * did-you-mean on an unknown/invented flag (per-subcommand, e.g. --phase ->
    --solvent), while genuine wrong choices still fail,
  * discovery via `--list-skills` / `<sub> --help-json`.

All of this is pure argparse/introspection — no chemistry backend is invoked, so
the tests run anywhere (no xtb/pyscf needed).
"""
from __future__ import annotations
import io
import json
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest

# Import the engine CLI module directly.
_MCP = Path(__file__).parent.parent / "mcp_server"
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))
from chemkit_engine import cli  # noqa: E402


# --------------------------------------------------------------------------- #
# Aliases
# --------------------------------------------------------------------------- #
def test_every_alias_resolves_to_a_real_subcommand():
    canon_set = set(cli.subcommand_names())
    for canon, aliases in cli.SUBCOMMAND_ALIASES.items():
        assert canon in canon_set, f"alias key {canon!r} is not a real subcommand"
        for a in aliases:
            assert cli._alias_to_canonical()[a] == canon


def test_canonical_maps_to_itself():
    for canon in cli.subcommand_names():
        assert cli._alias_to_canonical().get(canon) == canon


def test_subcommand_names_excludes_aliases():
    names = cli.subcommand_names()
    all_aliases = {a for al in cli.SUBCOMMAND_ALIASES.values() for a in al}
    # canonical names only; no descriptive alias leaks in
    assert "frontier" in names and "frontier-orbitals" not in names
    assert "sp" in names and "single-point-energy" not in names
    assert not (set(names) & all_aliases)


def test_alias_subcommand_parses_to_canonical_task():
    # frontier-orbitals (alias) should route to canonical 'frontier'; use --help
    # to exit before any chemistry.
    for name in ("frontier", "frontier-orbitals", "fmo"):
        with pytest.raises(SystemExit) as e:
            with redirect_stdout(io.StringIO()):
                cli.main([name, "--method", "xtb", "--help"])
        assert e.value.code == 0


# --------------------------------------------------------------------------- #
# did-you-mean
# --------------------------------------------------------------------------- #
def test_unknown_subcommand_suggests_closest():
    assert cli._suggest_subcommand("orbitalz") == "orbitals"
    assert cli._suggest_subcommand("fukuii") == "fukui"
    # a real near-miss of a descriptive alias resolves too
    assert cli._suggest_subcommand("frontier-orbitalss") in (
        "frontier-orbitals", "frontier")


def test_unknown_subcommand_error_contains_hint(capsys):
    with pytest.raises(SystemExit):
        cli.main(["orbitalz", "mol.xyz"])
    err = capsys.readouterr().err
    assert "did you mean" in err and "orbitals" in err


def test_invented_flag_suggests_real_flag():
    # --phase / --environment -> --solvent (when the subcommand has --solvent)
    assert cli._suggest_flag("--phase", ["--solvent", "--method"]) == "--solvent"
    assert cli._suggest_flag("--environment", ["--solvent"]) == "--solvent"
    # geometry-style invented flags -> "pass as positional" hint
    assert cli._suggest_flag("--geometry", ["--method"]).startswith("(pass")
    # a genuine typo of a real flag still fuzzy-matches
    assert cli._suggest_flag("--charg", ["--charge", "--method"]) == "--charge"
    # --phase where --solvent is NOT valid -> no misleading suggestion
    assert cli._suggest_flag("--phase", ["--method", "--charge"]) is None


def test_invented_flag_on_fukui_names_solvent(capsys):
    with pytest.raises(SystemExit):
        cli.main(["fukui", "--method", "xtb", "--phase", "gas", "mol.xyz"])
    err = capsys.readouterr().err
    assert "--solvent" in err


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def test_list_skills_covers_all_subcommands():
    js = json.loads(cli.list_skills(as_json=True))
    listed = {r["subcommand"] for r in js["subcommands"]}
    assert listed == set(cli.subcommand_names())
    # aliases are shown for the descriptive ones
    fo = next(r for r in js["subcommands"] if r["subcommand"] == "frontier")
    assert "frontier-orbitals" in fo["aliases"]


def test_list_skills_returns_zero_directly():
    with redirect_stdout(io.StringIO()) as out:
        rc = cli.main(["--list-skills"])
    assert rc == 0
    assert "frontier" in out.getvalue()


def test_help_json_returns_arg_spec():
    with redirect_stdout(io.StringIO()) as out:
        rc = cli.main(["frontier-orbitals", "--help-json"])
    assert rc == 0
    spec = json.loads(out.getvalue())
    assert spec["subcommand"] == "frontier"        # alias resolved to canonical
    flags = [a.get("flag") for a in spec["arguments"] if a.get("flag")]
    assert "--solvent" in flags and "--method" in flags
    assert "--phase" not in flags                  # the invented flag is not real


def test_help_json_unknown_skill_suggests(capsys):
    rc = cli.main(["orbitalz", "--help-json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown subcommand" in err


# --------------------------------------------------------------------------- #
# Regression: a valid canonical call still parses, consistency check clean
# --------------------------------------------------------------------------- #
def test_valid_call_still_parses():
    with pytest.raises(SystemExit) as e:
        with redirect_stdout(io.StringIO()):
            cli.main(["fukui", "--method", "xtb", "--help"])
    assert e.value.code == 0


def test_tools_cli_consistency_still_clean():
    # the canonical subcommand set must still match the server TOOLS mapping
    tools_subs = [
        "sp", "opt", "freq", "binding", "redox", "confsearch", "frontier",
        "electrostatics", "solvation", "logp", "profile", "pka", "build",
        "resolve", "fukui", "ts", "irc", "rxn-energy", "scan", "orbitals",
    ]
    assert cli.check_tools_cli_consistency(tools_subs) == []
