---
trigger: model_decision
description: Rules to implement a skill under `.agents/skills/`
---

# Skill Standards

All modular capabilities in this project should be implemented as "Skills" within the `.agents/skills/` directory. This rule ensures consistency, discoverability, and reusability for the agent.

## Directory Structure

Each skill must reside in its own subdirectory with the following structure:
```
.agents/skills/<skill-name>/
├── SKILL.md                  # Required: Main documentation
├── scripts/                  # Optional: Helper scripts
│   ├── script1.py
│   └── script2.py
├── examples/                 # Optional: Reference input/output files
│   └── example-name/
│       ├── README.md         # Required for each example
│       ├── example_input.cif
│       └── example_output.json
└── resources/                # Optional: Config files, templates, data
    ├── config_template.yaml
    └── reference_data.json
```

## SKILL.md Format

The `SKILL.md` file must follow this standardized structure:

### 1. YAML Frontmatter
```yaml
---
name: skill-name-in-kebab-case
description: Concise one-sentence summary of the skill's purpose and outcome.
category: category-name
---
```

**Guidelines:**
- `name`: Use lowercase letters, numbers, and hyphens only (kebab-case). Must be **descriptive** of the skill's purpose. The category prefix (e.g. `chem-`) is **optional** in this repository (see Skill Naming Conventions).
- `description`: Should be clear enough for the agent to decide if this skill is relevant to a user query. **The description must state what the skill is used for, NOT how the skill works.** Avoid mid-sentence colons (`: `) in unquoted values — they break YAML parsing.
- `category`: **Required.** Must be one of the following (use a YAML list like `[materials, chemistry]` if multiple apply). The parenthetical prefixes below are the *optional* name prefixes associated with each category:
  - `materials`: Materials science simulation and analysis skills (optional prefix: `mat-`)
  - `chemistry`: Chemistry and molecular simulation skills (optional prefix: `chem-`)
  - `machine-learning`: MLIP training, model selection, and ML workflows (optional prefix: `ml-`)
  - `drug-discovery`: Drug design, docking, and molecular property prediction (optional prefix: `drug-`)
  - `general`: General-purpose research utilities (optional prefix: `general-`)

### 2. Title and Goal Section
Begin with a level-1 header matching the skill name, followed by a `## Goal` section:

```markdown
# Skill Name

## Goal
Clearly state what this skill achieves. Use precise technical language and, when applicable, include mathematical notation (e.g., "To determine the thermodynamic melting temperature ($T_m$) of a bulk material").
```

### 3. Instructions Section
Provide numbered, step-by-step instructions. Each step should:
- **State the objective clearly** (e.g., "Background Research", "Phase Preparation")
- **Provide specific commands** with environment annotations
- **Include all necessary parameters** with explanations
- **Link to related skills** when appropriate

**Format for code blocks:**
````markdown
```bash
# Env: <conda-environment-name>
python .agents/skills/<skill-name>/scripts/<script>.py [arguments]
```
````

**Format for MCP tool calls:**
````markdown
```bash
mcp_<tool_name>(
    parameter1=value,  # Comment explaining the parameter
    parameter2=value,  # Use realistic values, not placeholders
    output_dir="descriptive_name"
)
```
````

**Key principles:**
- Always specify the conda environment required
- Use absolute paths from project root for script references
- Provide inline comments explaining non-obvious parameters
- Use realistic example values instead of generic placeholders
- Cross-reference other skills using relative links (e.g., `[molecular-dynamics](../molecular-dynamics/SKILL.md)`)

### 4. Examples Section
Provide concrete, runnable examples that demonstrate typical usage.

**CRITICAL RULE:** Each distinct example should be placed in its own dedicated subdirectory within `examples/` (e.g., `examples/my-example/`) and MUST contain its own `README.md` file. This README should comprehensively document the example's goal, step-by-step instructions, and expected outputs.

**LITERATURE VALIDATION RULE:** Whenever possible, choose an example system with known, published literature reported values. In the example's `README.md`, you MUST compare the skill's execution results to the reported literature values to validate the skill's correctness, and explicitly include the literature reference citation.

> [!IMPORTANT]
> **Experimental-source integrity.** When the validation value is described as an
> **experimental** quantity (a measured bond length, frequency, dipole, ΔG,
> redox potential, pKa, logP, etc.), the citation MUST point to a **genuine
> experimental primary source** (the paper that *measured* it). Do **NOT** cite a
> computational/modeling paper, a method-development paper, or a theoretical
> review as if it were the experiment — even if that paper tabulates the
> experimental number. If you can only locate the value in a compilation,
> database, or computational paper, you MUST either (a) trace and cite the
> underlying experimental primary reference, or (b) label the value honestly for
> what it is (e.g. "value as compiled in [database]", "high-level CCSD(T)
> reference value from [ref]") and NOT call it "experiment". Never present
> computational chemists' modeling results as experimental measurements.
> Fabricating, guessing, or misattributing a literature value is prohibited —
> every number must be verifiable from the cited source.

> [!IMPORTANT]
> **Artifact Retention (repository policy).** Each example MUST live in its own
> descriptively-named subdirectory under `examples/` and MUST store the files the
> calculation generated — the result `.json`, the input/output `.xyz`
> geometries, any `.png` plots, and (in this repository) the trajectory `.xyz`
> files (e.g. IRC / relaxed-scan / reaction-profile trajectories) — alongside the
> summary `README.md`. This makes each example fully self-documenting and
> reproducible.
>
> Still avoid committing genuinely heavy binary artifacts that are not needed to
> understand the example, such as ML model checkpoints (`.pth`, `.model`, `.pt`).
> (Note: this repository intentionally relaxes the upstream standard's blanket
> ban on retaining `.xyz` trajectory aggregations — our chemistry examples are
> small, and the trajectories are part of the validated result.)

**3D STRUCTURE RENDERING RULE:** To ensure compatibility with the documentation website's automatic 3D structure viewer (3Dmol.js), any `.cif` or `.xyz` files you want rendered MUST be written as standard markdown links (e.g., `[my_structure.cif](my_structure.cif)`). Do NOT write them merely as backticked code snippets (` `my_structure.cif` `) within tables or lists, as they will not be rendered. It is recommended to add a dedicated "## 3D Structures" section at the end of the `README.md` for these links.


```markdown
## Examples

Creating a solid-liquid interface for Aluminum:
```bash
# Env: base-agent
python .agents/skills/melting-point/scripts/create_interface.py Al_solid.cif Al_liquid.cif --axis 0 --output Al_interface.cif
```
```

### 5. Constraints Section
Document important limitations, safety rules, and requirements:

```markdown
## Constraints
- **Box Dimensions**: The lattice parameters perpendicular to the stacking axis must be identical.
- **Ensemble**: The final production run must be in the **NVE** ensemble.
- **Environments**: Scripts require specific Conda environments (e.g., `mace-agent`, `matgl-agent`). **Each code block MUST specify the environment.**
- **System Size**: Recommended for supercells with >50 atoms to reduce noise.
```

### 6. References Section
Include basic literature citations (with DOIs if available) for the methods, algorithms, or software packages the skill relies on to ensure scientific reproducibility.

```markdown
## References
- Author et al., "Paper Title", *Journal Name*, Year. [DOI](https://doi.org/...)
```

## Script Documentation Standards

All Python scripts in the `scripts/` directory must include:

### 1. Module-Level Docstring
```python
"""
Brief description of what this script does.

Usage:
    python script_name.py input.cif --option value

Requirements:
    - Conda environment: <env-name>
    - Required packages: ase, pymatgen, etc.
"""
```

### 2. Argument Parser with Help Text
```python
parser = argparse.ArgumentParser(
    description="Clear description of the script's purpose"
)
parser.add_argument("input", help="Description of input file/parameter")
parser.add_argument("--option", default=default_value, help="What this option controls")
```

### 3. Type Hints and Docstrings for Functions
```python
def process_structure(atoms: Atoms, threshold: float = 0.5) -> dict:
    """
    Brief description of what the function does.

    Args:
        atoms: ASE Atoms object to process
        threshold: Cutoff value for some criterion

    Returns:
        Dictionary containing results with keys: 'metric1', 'metric2'
    """
```

## Environment Management Standards

### 1. Explicit Specification
Every script execution in `SKILL.md` **MUST** include a `# Env: <conda-environment-name>` annotation in the code block. This ensures the agent and the user know exactly which environment to activate before running the script.

### 2. Environment Mapping
Refer to `mcp-environments.md` for the standard environment mapping:
- `base-agent`: General materials tools, Materials Project API, and parsing.
- `matgl-agent`: MatGL calculations, training, and utilities.
- `mace-agent`: MACE calculations and training.
- `fairchem-agent`: FairChem/OCP/UMA calculations.
- `atomate2-agent`: Atomate2/Jobflow workflows and DB querying.
- `smol-agent`: Cluster expansion and Monte Carlo simulations.
- `mattergen-agent`: MatterGen structure generation.
- `drugdisc-agent`: Drug discovery, docking, and molecular tools.

### 3. Documentation Consistency
The required environment must be consistent across:
- The `# Env:` annotation in `SKILL.md`.
- The `Requirements` section in the script's module-level docstring.
- The `Constraints` section of `SKILL.md`.

## Best Practices

- **Scope & Progressive Disclosure**: Target one well-defined task. Focus `SKILL.md` on workflow logic; move implementation to `scripts/`, datasets to `resources/`, and examples to `examples/`.
- **References**: Always use relative paths from `SKILL.md` when linking to scripts or other skills.
- **Environments**: Always annotate code blocks with `# Env: <env-name>` and specify requirements in the Constraints section.
- **Integration**: Prioritize existing MCP tools over custom code. If writing custom MLIP scripts, ALWAYS use `src.utils.mlips.loader.load_wrapper`.
- **Validation & Documentation**: Embed verification steps, document expected outcomes, and use concrete, reproducible parameters in examples.
- **Parameter Persistence**: Skill scripts that accept input kwargs and hyperparameters **must** save all input parameters to a **separate `input_configs.yaml`** file in the same output directory where results are written. This file must capture both user-specified values **and** the default values of any parameters that were not explicitly provided. Do **not** embed configs inside JSON output files (e.g. as a `"config"` key). This ensures results remain clean and can be fully interpreted and reproduced in the future without re-inspecting the source code or command history.

## Skill Naming Conventions

- **Purpose over Method**: Skill names should be informative of the *function or purpose* of the skill, NOT the specific computational method being used (e.g., `solid-free-energy` is preferred over `frenkel-ladd`).
- Use **kebab-case** for skill directory names (lowercase with hyphens). This is **required**.
- Names **must be descriptive** — a reader should understand the skill's purpose from the name alone:
  - Good: `single-point-energy`, `geometry-optimize`, `reaction-profile`, `build-from-smiles`
  - Avoid: terse or cryptic names like `sp`, `opt`, `prof`, `b`
- **Category prefix is OPTIONAL** (relaxed for this repository). You MAY prefix a
  name with its category for cross-project clarity, but it is not required here:
  - `mat-` for `materials`, `chem-` for `chemistry`, `ml-` for `machine-learning`,
    `drug-` for `drug-discovery`, `general-` for `general`
  - With or without the prefix, the `category` field in the frontmatter is still **required** (see SKILL.md Frontmatter).
- Use **noun forms** for result-oriented skills: `phase-diagram`, `surface-energy`
- Use **action/process names** for workflow skills: `diffusion-analysis`, `mlip-training`
- **Private Skills**: To create a proprietary or private skill that should not be tracked by version control, prefix the entire name with `private-` (e.g., `private-mat-proprietary-workflow`). The repository's `.gitignore` is configured to ignore all directories matching `.agents/skills/private-*/`, ensuring they remain local while still being automatically discovered by the agent.

## Example Skill Structure

See [`.agents/skills/melting-point/`](./../skills/melting-point/) for a comprehensive reference implementation demonstrating workflows, tool integration, validation, and environment handling.

### 7. Author Information

At the very end of every `SKILL.md` file, include a footer separated by a horizontal rule (`---`).

**CRITICAL NOTE:** The author and contact cannot be an AI agent. It must be the human author who initiated and pushed this change.

**GitHub contact (preferred):**
```markdown
---

**Author:** Name
**Contact:** [GitHub @username](https://github.com/username)
```

**Email contact:**
```markdown
---

**Author:** Name
**Contact:** [name@example.com](mailto:name@example.com)
```
