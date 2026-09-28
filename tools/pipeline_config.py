#!/usr/bin/env python3
"""
tools/pipeline_config.py — a pure delegating stub over the package resolver
(ADR-0092 D1, CONCERN 4 option (i)).

The real parser now lives at `.claude/pipeline/tools/pipeline_config.py`
(moved there in slice 1 of PRD "Make the pipeline an installable,
upgradeable package"). This file exists so the ~35 root callers that reach
it at `tools/pipeline_config.py` keep working unchanged until they move
into the package themselves (slices 3 and 4) — rewriting every call site
twice would cost more than one stub.

This stub does NO key parsing and NEVER opens the per-repo config file
itself. It:
  - locates the package module from ITS OWN directory (never from
    `$REPO_ROOT/tools/...` of the cwd repo — S1-c, unchanged);
  - loads it under a distinct module name (never plain "pipeline_config",
    to avoid a same-name double-load if some caller also imports the
    package module directly);
  - forwards the module's public attributes into this module's own
    namespace, so `PipelineConfigError`, `integration_branch()`,
    `release_branch()`, `mode()` and so on ARE the package module's own
    objects (`is`, not equal-but-copied);
  - as a script, forwards `sys.argv` to the package module's CLI via
    `runpy.run_path(..., run_name="__main__")`, which carries over stdout,
    stderr and the exit code unchanged.

It has no default branch names and no fallback literal of its own. A
load failure (the package module missing or raising at import time)
propagates as an ordinary ImportError/exception — each caller's existing
fail-open or fail-closed posture around that applies exactly as it always
has, unchanged by this stub.

This stub is deleted in the slice that empties `tools/` of every other
pre-move root (ADR-0092 D1 W4); until then it is itself a CHECK 29 arm (a)
subject (ADR-0092 D1), since it is product code that could smuggle a
branch-name literal back in.
"""
import runpy
import sys
from pathlib import Path

_PACKAGE_MODULE_PATH = (
    Path(__file__).resolve().parent.parent / ".claude" / "pipeline" / "tools" / "pipeline_config.py"
)


def _load_package_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pipeline_config_package_1603", _PACKAGE_MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_pkg = _load_package_module()

# Forward every attribute except Python's own dunders (functions, the
# exception class, internal helpers and constants alike) — these ARE the
# package module's own objects, not copies, so an existing caller that
# reaches into what used to be this module's own internals (e.g. its
# `_DEFAULTS` mapping) keeps working exactly as it did before the move.
for _name in dir(_pkg):
    if not (_name.startswith("__") and _name.endswith("__")):
        globals()[_name] = getattr(_pkg, _name)


if __name__ == "__main__":
    # Forward argv to the package module's own CLI. `run_name="__main__"`
    # fires its own module-level entry-point guard, so stdout, stderr and
    # the exit code all come from the package CLI unchanged.
    runpy.run_path(str(_PACKAGE_MODULE_PATH), run_name="__main__")
