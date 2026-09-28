# The pipeline package

This is the pipeline product, delivered as one git subtree at
`.claude/pipeline/` (ADR-0092). It is versioned by `VERSION` and installed
or upgraded with `.claude/pipeline/tools/package.py` — see `install.sh` and
`UPGRADING.md`.

This file grows into the pipeline's own rules, map and glossary as later
slices move the prompt surface, tools and tests into the package (ADR-0092
D1, work-units W2-W5). For now it exists so `.claude/pipeline/` is a real,
importable package from slice 1 onward (the walking skeleton).

The home repository, and every host, reach this package only through
generated shims and this file's own `@.claude/pipeline/CLAUDE.md` import
line in the root `CLAUDE.md` — never by hand-editing a shim.
