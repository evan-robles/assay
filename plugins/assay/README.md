# assay plugin

Registers the ASSAY MCP server (the 20 computational-chemistry tools) in Claude
Code.

**Prerequisite:** the `assay-mcp` command must be on your `PATH`. Installing the
plugin only wires up the MCP server — it does not install the package or its
conda-only backends (xtb, MOPAC, openbabel). Install ASSAY first:

```bash
pixi install                          # or: conda env create -f environment.yml
# then, with that env active, `assay-mcp` is on PATH
```

See the [repo README](https://github.com/evan-robles/assay) for full install
options. Once `assay-mcp` resolves, the plugin's MCP server starts automatically.
