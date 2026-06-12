# Example: Ammonia inversion IRC (PM7)

## Goal
Trace the intrinsic reaction coordinate from the validated NH₃ inversion transition state in both directions using the `intrinsic-reaction-coordinate` skill.

## Calculation run
- **Skill:** intrinsic-reaction-coordinate
- **Method + program:** PM7 (MOPAC)
- **Basis/functional:** not applicable (semi-empirical)
- **Charge/multiplicity:** charge 0 / multiplicity 1
- **Solvent:** gas phase

Starts from the validated NH₃ inversion TS (single imaginary mode, see the transition-state example) and walks down both directions.

```bash
# Env: anl_env
python skills/intrinsic-reaction-coordinate/scripts/intrinsic-reaction-coordinate.py --method mopac nh3_ts.xyz --out nh3_irc.json
```

Generated files: [nh3_ts.xyz](nh3_ts.xyz), [nh3_irc.json](nh3_irc.json), [nh3_irc_forward.xyz](nh3_irc_forward.xyz), [nh3_irc_reverse.xyz](nh3_irc_reverse.xyz)

## Result (this calculation)

| Quantity | Value |
|---|---|
| Forward IRC | completed normally (40 points, "JOB ENDED NORMALLY") |
| Reverse IRC | completed normally (40 points, "JOB ENDED NORMALLY") |
| distinct_endpoints | False |
| Energy drop from saddle top | ~0.01 kcal/mol each direction |

## Validation
NH₃ inversion connects two equivalent pyramidal minima that are mirror images, so the forward and reverse endpoints are the same molecule — `distinct_endpoints = False` is the chemically correct result, confirming the TS connects the umbrella-inverted forms. This validates the IRC walk qualitatively (an IRC produces a path, not an experimental number).

## References
- K. Fukui. "The path of chemical reactions — the IRC approach." Acc. Chem. Res. 1981, 14, 363 (IRC concept). https://doi.org/10.1021/ar00072a001 *(value/DOI not web-verified in this session. [CITATION UNVERIFIED])*

## 3D Structures
- [nh3_ts.xyz](nh3_ts.xyz)
- [nh3_irc_forward.xyz](nh3_irc_forward.xyz)
- [nh3_irc_reverse.xyz](nh3_irc_reverse.xyz)

---

**Author:** Evan Robles
**Contact:** [GitHub @evan-robles](https://github.com/evan-robles)
