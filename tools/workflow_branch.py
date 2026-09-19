"""Shared branch classification; legality is narrower than legacy gate selection."""
import argparse
from dataclasses import asdict, dataclass
import json
import re

KINDS = ("feat", "fix", "chore", "refactor", "docs", "test", "perf", "style",
         "build", "ci", "hotfix")
ISSUE_BRANCH = re.compile(r"(" + "|".join(KINDS) + r")/([0-9]+)-[a-z0-9-]+\Z")


@dataclass(frozen=True)
class Branch:
    kind: str | None
    issue: int | None
    valid: bool
    namespaced: bool


def classify(branch: str | None) -> Branch:
    """Codex requires the complete issue grammar; keep conventional prefix meaning."""
    value = branch or ""
    namespaced = value.startswith("codex/")
    raw = value[6:] if namespaced else value
    match = ISSUE_BRANCH.fullmatch(raw)
    if match:
        return Branch(match[1], int(match[2]), True, namespaced)
    kind = raw.split("/", 1)[0] if "/" in raw else None
    if namespaced or kind not in KINDS:
        kind = None
    return Branch(kind, None, False, namespaced)


def requires_regression(branch: str | None, labels=()) -> bool:
    """Ship and R-PROVE share the fix OR root-cause-label selector."""
    return classify(branch).kind == "fix" or "root-cause" in labels


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("branch")
    parser.add_argument("--label", action="append", default=[])
    args = parser.parse_args(argv)
    result = classify(args.branch)
    print(json.dumps({**asdict(result),
                      "requires_regression": requires_regression(args.branch, args.label)},
                     sort_keys=True))
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
