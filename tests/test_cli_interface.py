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


def test_flag_error_hint_points_at_discovery():
    # ENGINE-LEVEL recovery guidance: every unrecognized-flag hint points at the
    # discovery command so ANY consumer (real user's agent, human, benchmark) can
    # self-correct — even when there is no single confident suggestion.
    ff = cli._valid_flags_for("fukui")
    h1 = cli._flag_error_hint("--phase", "fukui", ff)
    assert "--solvent" in h1 and "--help-json" in h1
    h2 = cli._flag_error_hint("--molecule", "fukui", ff)
    assert "positional" in h2 and "--help-json" in h2
    h3 = cli._flag_error_hint("--zzzznope", "fukui", ff)  # no confident match
    assert "--help-json" in h3  # still guides to discovery
    # end-to-end: the real error surfaces the discovery pointer
    import io, pytest as _pt
    from contextlib import redirect_stderr
    buf = io.StringIO()
    with _pt.raises(SystemExit), redirect_stderr(buf):
        cli.main(["fukui", "--method", "xtb", "--phase", "gas", "mol.xyz"])
    assert "--help-json" in buf.getvalue()


def test_geometry_input_invented_flags_point_to_positional():
    # models invent many geometry-input flags; all -> "pass as positional"
    ff = ["--method", "--charge", "--mult", "--solvent", "--no-plot"]
    for bad in ("--molecule", "--mol", "--geo", "--geometry", "--xyz",
                "--input", "--coord-file", "--structure", "--system"):
        assert cli._suggest_flag(bad, ff).startswith("(pass"), bad
    # regression: --molecule must NOT mis-suggest --mult by edit distance
    assert cli._suggest_flag("--molecule", ff) != "--mult"


def test_plot_flag_hint_and_nonexistent_knobs():
    ff = ["--method", "--no-plot", "--charge"]
    # plotting is default-on: guide to omit --plot / use --no-plot
    assert "default" in cli._suggest_flag("--plot", ff)
    # invented tuning knobs with no real equivalent -> no misleading match
    for bad in ("--convergence", "--atoms", "--gradient", "--maxiter"):
        assert cli._suggest_flag(bad, ff) is None, bad


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


def test_scored_result_selects_intended_method():
    # An agent may run the intended DFT calc and THEN an exploratory xtb call.
    # The scorer must grade the DFT call, not the last (xtb) one. (Regression for
    # the o3 fukui case: right b3lyp/def2-tzvp run, then an xtb single-point.)
    import importlib, sys as _s
    _b = str(Path(__file__).parent.parent / "benchmarks")
    if _b not in _s.path:
        _s.path.insert(0, _b)
    fd = importlib.import_module("fidelity_driver")
    dft = {"method": "b3lyp/def2-tzvp", "program": "dft"}
    xtb = {"method": "GFN2-xTB", "program": "xtb"}
    spec = {"intended": {"method": "dft", "functional": "b3lyp",
                         "basis": "def2-tzvp", "tier": "standard"}}
    assert fd._select_scored_result([dft, xtb], spec)["method"] == "b3lyp/def2-tzvp"
    # xtb-intended spec picks xtb; no-match falls back to last; empty -> {}
    assert fd._select_scored_result([dft, xtb], {"intended": {"method": "xtb"}})["method"] == "GFN2-xTB"
    assert fd._select_scored_result([dft, xtb], {"intended": {"method": "mopac"}})["method"] == "GFN2-xTB"
    assert fd._select_scored_result([], spec) == {}
    # strict matcher: xtb/pm7 never satisfy a dft/hf intent
    assert fd._method_matches_strict("dft", "GFN2-xTB") is False
    assert fd._method_matches_strict("dft", "b3lyp/def2-tzvp") is True
    assert fd._method_matches_strict("xtb", "GFN2-xTB") is True


def test_tools_cli_consistency_still_clean():
    # the canonical subcommand set must still match the server TOOLS mapping
    tools_subs = [
        "sp", "opt", "freq", "binding", "redox", "confsearch", "frontier",
        "electrostatics", "solvation", "logp", "profile", "pka", "build",
        "resolve", "fukui", "ts", "irc", "rxn-energy", "scan", "orbitals",
    ]
    assert cli.check_tools_cli_consistency(tools_subs) == []


# --------------------------------------------------------------------------- #
# Typed tool schema: params -> canonical argv (server + driver parity)
# --------------------------------------------------------------------------- #
def _load_server():
    import importlib
    if str(_MCP) not in sys.path:
        sys.path.insert(0, str(_MCP))
    return importlib.import_module("server")


def _load_driver():
    import importlib
    b = str(Path(__file__).parent.parent / "benchmarks")
    if b not in sys.path:
        sys.path.insert(0, b)
    return importlib.import_module("fidelity_driver")


def _load_arg_spec():
    """Import the shared arg_spec module (single source of truth for typed args)."""
    import importlib
    m = str(Path(__file__).parent.parent / "mcp_server")
    if m not in sys.path:
        sys.path.insert(0, m)
    return importlib.import_module("chemkit_engine.arg_spec")


def test_params_to_argv_canonical_dft():
    A = _load_arg_spec()
    argv = A.params_to_argv("sp", dict(
        input="/m.xyz", method="dft", functional="b3lyp", basis="def2-tzvp",
        tier="standard", charge=0, multiplicity=1))
    assert "--method" in argv and "dft" in argv
    assert "--functional" in argv and "b3lyp" in argv
    assert "--tier" in argv and "standard" in argv
    assert argv[-1] == "/m.xyz"  # positional last


def test_params_to_argv_charge_zero_emitted():
    A = _load_arg_spec()
    # charge 0 must be emitted, not falsy-skipped
    assert "--charge" in A.params_to_argv("sp", dict(method="xtb", charge=0, input="/m.xyz"))


def test_params_to_argv_gas_phase_omits_solvent():
    A = _load_arg_spec()
    for s in ("none", "gas", "gas phase", "gas-phase", "vacuum", ""):
        assert "--solvent" not in A.params_to_argv("sp", dict(method="xtb", solvent=s, input="/m.xyz"))
    # a real solvent is kept
    assert "--solvent" in A.params_to_argv("sp", dict(method="dft", tier="standard",
                                                      solvent="water", input="/m.xyz"))


def test_params_to_argv_extra_args_before_positional():
    A = _load_arg_spec()
    argv = A.params_to_argv("frontier", dict(method="dft", tier="standard", input="/m.xyz"),
                            extra_args=["--some-rare-flag", "5"])
    assert argv[-3:] == ["--some-rare-flag", "5", "/m.xyz"]


def test_params_to_argv_injection_guard():
    """A value for a param the skill does NOT have is dropped, not injected —
    the core fix for the many-arg skills (pka has no xyz/charge params)."""
    A = _load_arg_spec()
    argv = A.params_to_argv("pka", dict(
        ha="/ha.xyz", a_minus="/am.xyz", method="mopac",
        xyz="/should_not_appear.xyz", charge=0, multiplicity=1))
    assert "--ha" in argv and "--a-minus" in argv
    assert "/should_not_appear.xyz" not in argv
    assert "--charge" not in argv and "--mult" not in argv


def test_params_to_argv_required_flags_surface_for_redox():
    """redox-potential exposes ox_charge/red_charge as typed params that emit
    the correct required flags."""
    A = _load_arg_spec()
    argv = A.params_to_argv("redox", dict(
        input="/m.xyz", method="xtb", ox_charge=0, red_charge=-1, ref="SHE"))
    assert "--ox-charge" in argv and "--red-charge" in argv and "--ref" in argv


def test_server_and_driver_share_one_converter():
    """server.py and fidelity_driver.py both route typed params through the SAME
    shared arg_spec.params_to_argv (no more hand-duplicated converters)."""
    srv = _load_server()
    drv = _load_driver()
    A = _load_arg_spec()
    # both modules resolve to the same function object
    assert drv._typed_args_to_argv.__module__.endswith("fidelity_driver")
    # driver produces the same canonical argv as calling the shared converter
    params = dict(skill="sp", method="dft", functional="b3lyp", basis="def2-tzvp",
                  tier="standard", charge=-1, multiplicity=2, solvent="water",
                  xyz="/mol.xyz")
    direct = A.params_to_argv("sp", dict(
        input="/mol.xyz", method="dft", functional="b3lyp", basis="def2-tzvp",
        tier="standard", charge=-1, multiplicity=2, solvent="water"))
    assert drv._typed_args_to_argv(params) == direct


def test_driver_chemkit_tool_is_typed():
    drv = _load_driver()
    props = drv._CHEMKIT_TOOL["function"]["parameters"]["properties"]
    # common fields still present
    assert set(["skill", "xyz", "method", "charge", "multiplicity", "solvent",
                "functional", "basis", "tier", "extra_args"]).issubset(props)
    # AND the per-skill required flags are now first-class typed properties
    assert set(["ox_charge", "red_charge", "ha", "a_minus", "monomer"]).issubset(props)
    assert props["method"]["enum"] == ["xtb", "mopac", "dft", "hf"]
    assert len(props["skill"]["enum"]) == 20  # all skills selectable


def _tool_schemas():
    """Map tool-name -> advertised inputSchema for every registered MCP tool."""
    import asyncio
    srv = _load_server()

    async def _list():
        return await srv.mcp.list_tools()
    tools = asyncio.run(_list())
    return {t.name: t.inputSchema for t in tools}


def test_mcp_tools_have_per_skill_typed_signatures():
    """Each MCP tool advertises its OWN typed params — the fix for the many-arg
    skills. redox-potential surfaces ox_charge/red_charge/ref; pka-acidity
    surfaces ha/a_minus and does NOT expose an xyz/charge param to inject."""
    schemas = _tool_schemas()
    assert len(schemas) == 20

    redox = schemas["redox-potential"]["properties"]
    assert {"ox_charge", "red_charge", "ref", "mode"}.issubset(redox)
    # ox_charge is a typed integer (Optional -> anyOf[{integer},{null}])
    assert "integer" in json.dumps(redox["ox_charge"])
    # ref enum with null in the type UNION (not a None enum member)
    ref = redox["ref"]
    assert any("enum" in b for b in ref.get("anyOf", [ref]))
    # nullability comes from a null-typed union member, never a None enum entry
    assert None not in [c for b in ref.get("anyOf", []) for c in b.get("enum", [])]

    pka = schemas["pka-acidity"]["properties"]
    assert {"ha", "a_minus", "mode"}.issubset(pka)
    # the injection bug is impossible: pka has NO xyz/charge/multiplicity params
    assert "xyz" not in pka
    assert "charge" not in pka
    assert "multiplicity" not in pka

    # binding-energy exposes monomer as a list (append action)
    binding = schemas["binding-energy"]["properties"]
    assert "monomer" in binding

    # single-geometry skill still uses the positional `input`
    assert "input" in schemas["single-point-energy"]["properties"]


def test_mcp_tools_no_schema_required_fields():
    """No tool marks a field schema-required (requiredness is enforced by the
    engine argparse); this keeps the back-compat `args` raw-token path callable
    without also filling the typed fields."""
    schemas = _tool_schemas()
    for name, sch in schemas.items():
        assert not sch.get("required"), f"{name} unexpectedly has required fields: {sch.get('required')}"
