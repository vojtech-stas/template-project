# Upgrading the pipeline package

A host upgrades with one command:

```
python3 .claude/pipeline/tools/package.py upgrade vX.Y.Z
```

This runs `git subtree pull --prefix=.claude/pipeline <package_source> vX.Y.Z --squash`,
regenerates shims in a fresh process, and runs `package.py check` in a fresh
process. It never writes the host's own `CLAUDE.md`. `package.py upgrade`
refuses (before any write) with one of: `home-mode`, `dirty-tree`,
`role-branch`, `not-pristine`, `not-newer`.

Once later slices land `tools/pipe/pr-merge`'s `pipeline-upgrade` merge mode
(ADR-0092 D4), an upgrade lands as a reviewed PR merged with a merge commit
(never squashed) — squashing an upgrade PR destroys the subtree topology the
next upgrade needs. Until then, run `package.py upgrade` on a branch of your
own and open a normal PR.

## Recovery from a squash-merged upgrade

If an upgrade PR is ever squash-merged by hand, `package.py check`'s
pristine arm detects it (the newest commit reachable from `HEAD` is no
longer squash-shape, or its tree no longer matches `.claude/pipeline`).
Recover by re-running the upgrade from the last known-pristine commit:
reset the branch to that commit, then re-run
`python3 .claude/pipeline/tools/package.py upgrade vX.Y.Z` and merge the
result with a merge commit.
