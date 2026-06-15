# Reading chemkit results efficiently (shared across all skills)

Every chemkit skill writes its full result JSON to the `--out` path **and** can
echo it to stdout. Reading it back carelessly costs tokens twice. Follow this
pattern for **every** skill invocation (it implements
`calculation-reporting-standards.md` §9.1 and non-negotiable #9).

## 1. Keep the full JSON out of context — write to a file, suppress stdout

Pass `--out <path>` and `--stdout path` so stdout is a one-line pointer instead
of the entire indented JSON:

```bash
# Env: anl_env
python skills/<skill>/scripts/<skill>.py --method <m> --out result.json --stdout path input.xyz
# stdout: {"out":"/abs/result.json","converged":true,"warnings":[...]}
```

`--stdout` choices: `json` (full blob, the legacy default), `path` (compact
one-line pointer — **use this**), `none` (silent; file still written).

## 2. Read back only the fields you need — never `cat` the whole file

Use `jq` to select the skill-specific keys plus the always-required safety
fields. **Always include `warnings` and the convergence flag** so a
non-convergence or caveat is never silently dropped:

```bash
jq '{<skill-specific keys>, converged, warnings}' result.json
# e.g. for sp:    jq '{total_energy_eV, code_specific: .code_specific.homo_eV, warnings}' result.json
# e.g. for freq:  jq '{gibbs_free_energy_eV, n_imaginary_modes, warnings}' result.json
```

## 3. Surface the live `.out` log the moment the run starts

When a calculation launches (especially a long DFT/freq job run in the
background), the engine streams a live `<subcommand>_<timestamp>.out` log to the
caller's cwd. **Tell the user its path immediately — while the run is still
going — and offer `tail -f`**, do not wait for completion:

> Calculation started; logging live to `/abs/path/freq_20260615-101500.out` —
> you can `tail -f` it now to watch SCF cycles / optimizer steps.

Surface the path again with the final result.

## 4. Report honestly

Report every entry in the result's `warnings` array verbatim, the method block
(level of theory, solvent or "gas phase", charge, multiplicity), and the
convergence state. Never volunteer an experimental/literature comparison unless
the user asks (and then only via the verified-citation procedure in
`research-standards.md`).
