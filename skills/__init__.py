"""assay skills — self-contained workflow packages.

Each skill folder (underscore-named so it is importable) holds a SKILL.md plus a
scripts/run.py exposing a typed run() + build_parser() + an argkit.run_cli
__main__. Composite skills import a sibling primitive's run() in-process
(DESIGN.md §5). The kebab-case display name lives in each SKILL.md frontmatter.
"""
