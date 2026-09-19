#!/usr/bin/env python3
"""Bounded OpenAI workflow adapter (ADR-0086 D6); stdlib, no pipeline engine."""
import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import gen_rules
from tools.workflow_branch import classify, requires_regression

GLOBAL_SOURCES = ("CLAUDE.md", ".claude/generated/_global.md",
                  ".claude/generated/_repo-map.md", "docs/openai-workflow.md")
SCOPE_ADDITIONS = {"isolation": ("tools/openai_workflow.py", "AGENTS.md", ".agents/**"),
                   "slicing": (".agents/skills/*/SKILL.md",)}
GIT_QUERIES = {"top": ("rev-parse", "--show-toplevel"),
               "common": ("rev-parse", "--path-format=absolute", "--git-common-dir"),
               "registry": ("worktree", "list", "--porcelain"),
               "branch": ("branch", "--show-current"), "head": ("rev-parse", "HEAD"),
               "status": ("status", "--porcelain=v1")}
HEX = re.compile(r"[0-9a-f]{40}\Z")
ID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z")
ROUTE_SOURCE = ".claude/agents/qa-tester.md"


class Refusal(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def child_env(identity=None):
    """One PATH spelling, UTF-8 for Python children and captured gh JSON on Windows."""
    path = next((v for k, v in os.environ.items() if k.upper() == "PATH"), "")
    env = {k: v for k, v in os.environ.items() if k.upper() != "PATH"}
    env.update(PATH=path, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if identity is not None:
        require(ID.fullmatch(identity), "missing or invalid observed host identity")
        env["CLAUDE_SESSION_ID"] = "codex:" + identity
    return env


def run(args, root, *, identity=None):
    result = subprocess.run(args, cwd=root, env=child_env(identity), capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    require(result.returncode == 0,
            f"command failed ({result.returncode}): {args[0]}: {result.stderr.strip()}")
    return result.stdout.strip()


def git(root, *args):
    return run(["git", *args], root)


def root_at(cwd=None):
    value = git(cwd or Path.cwd(), "rev-parse", "--show-toplevel")
    root = Path(value).resolve(strict=True)
    require(root.is_dir(), "repository root unavailable")
    return root


def relative_path(value):
    require(isinstance(value, str) and value and "\0" not in value, "invalid path")
    value = value.replace("\\", "/")
    require(not re.match(r"^[A-Za-z]:", value) and not value.startswith("/"),
            "path must be repository-relative")
    require(all(p not in ("", ".", "..") for p in value.split("/")), "traversing path")
    return str(PurePosixPath(value))


def matches(path, pattern):
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]))


def instructions(root, paths):
    paths = sorted({relative_path(p) for p in paths})
    sources = list(GLOBAL_SOURCES)
    for scope, target in gen_rules.SCOPE_TARGET.items():
        if target != "area":
            continue
        patterns = [p.strip() for p in gen_rules.SCOPE_PATHS[scope].split(",")]
        patterns += list(SCOPE_ADDITIONS.get(scope, ()))
        if any(matches(p, pattern) for p in paths for pattern in patterns):
            source = f".claude/rules/{scope}.md"
            text = (root / source).read_text(encoding="utf-8")
            lines = text.splitlines()
            require(len(lines) > 1 and lines[0] == gen_rules.GENERATED_HEADER.strip()
                    and lines[1] == "paths: " + gen_rules.SCOPE_PATHS[scope],
                    f"malformed generated rule header: {source}")
            sources.append(source)
    for source in sources:
        require((root / source).is_file() and (root / source).stat().st_size > 0,
                f"missing required source: {source}")
    require((root / ".claude/generated/_global.md").read_text(encoding="utf-8").splitlines()[0]
            == gen_rules.GENERATED_HEADER.strip(), "malformed generated global header")
    return sources


def routes(root, paths):
    """Read the authoritative Markdown table, including every matching row."""
    text = (root / ROUTE_SOURCE).read_text(encoding="utf-8")
    start = text.index("| Changed-path glob | Proof class | Required proof |")
    table = text[start:].split("\n\n", 1)[0]
    result = set()
    for row in table.splitlines()[2:]:
        cells = row.split("|")
        require(len(cells) == 5, "malformed authoritative route row")
        patterns = re.findall(chr(96) + "([^" + chr(96) + "]+)" + chr(96), cells[1])
        classes = cells[2].strip().replace("*", "").split(" + ")
        require(patterns and all(classes), "malformed authoritative route metadata")
        if any(matches(relative_path(p), pattern) for p in paths for pattern in patterns):
            result.update(classes)
    require(result, "no authoritative proof route matches")
    return result


def utc(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(date.tzinfo is not None and date.utcoffset().total_seconds() == 0,
            "timestamp must be UTC")
    return date


def fresh(value, boundary, now):
    date = utc(value)
    require(boundary <= date <= now and (now - date).total_seconds() <= 1800,
            "evidence is stale, future, or before the exercise boundary")
    return date


def snapshot(root):
    return {key: git(root, *args) for key, args in GIT_QUERIES.items()}


def assert_isolation(actual, expected, shared_roots, *, before=False):
    top = Path(actual["top"]).resolve(strict=True)
    common = Path(actual["common"]).resolve(strict=True)
    require(top == Path(expected["worktree"]).resolve(strict=True), "wrong worktree")
    require(top not in {Path(p).resolve(strict=True) for p in shared_roots}
            and top != common.parent, "shared-root isolation refusal")
    require(common == Path(expected["common"]).resolve(strict=True), "wrong repository")
    branch = classify(actual["branch"])
    if before:
        require(branch.valid and branch.namespaced and branch.issue == expected["slice"],
                "invalid typed issue branch")
    require(actual["branch"] == expected["branch"], "wrong branch")
    require(HEX.fullmatch(expected["head"]) and actual["head"] == expected["head"],
            "wrong tested revision")
    require(HEX.fullmatch(expected["start_sha"]), "missing starting revision")
    if before:
        require(actual["head"] == expected["start_sha"], "wrong starting revision")
        require(not actual["status"], "worktree is not clean before dispatch")
    entries = []
    for block in actual["registry"].split("\n\n"):
        lines = block.splitlines()
        if lines and lines[0].startswith("worktree "):
            if Path(lines[0][9:]).resolve() == top:
                entries.append(lines)
    require(len(entries) == 1, "unregistered or ambiguous worktree")
    branch_line = "branch refs/heads/" + actual["branch"] if actual["branch"] else "detached"
    require("HEAD " + actual["head"] in entries[0] and branch_line in entries[0],
            "registration does not match branch/revision")
    return actual


class Evidence:
    """Controller-owned references to real, independently exported host records.

    This checks correspondence, not authenticity of arbitrary local JSON. The
    controller must acquire records from the native host and pass its own ID.
    """
    def __init__(self, proof_root, observations, controller_id, root, now=None):
        self.root = root
        self.now = now or datetime.now(timezone.utc)
        self.proof_root = Path(proof_root).resolve(strict=True)
        require(self.proof_root.is_dir(), "durable proof root missing")
        self.data = json.loads(Path(observations).read_text(encoding="utf-8"))
        d = self.data
        require(d["schema"] == 1 and d["platform"] == "codex", "unsupported observation schema")
        require(d.get("provenance") == "controller-observed-native"
                and not d.get("fixture"), "fixture or unobserved provenance")
        require(ID.fullmatch(controller_id) and d["controller_id"] == controller_id,
                "controller identity mismatch")
        self.controller = controller_id
        self.boundary = fresh(d["started_at"], utc(d["started_at"]), self.now)
        self.expected = d["expected"]
        require(not self.proof_root.is_relative_to(Path(self.expected["worktree"]).resolve()),
                "proof root is inside disposable worktree")
        registry = git(root, "worktree", "list", "--porcelain")
        shared = {Path(p).resolve() for p in d["shared_roots"]}
        require(shared, "missing controller/shared roots")
        for line in registry.splitlines():
            if line.startswith("worktree "):
                tree = Path(line[9:]).resolve()
                if tree not in shared:
                    require(not self.proof_root.is_relative_to(tree),
                            "proof root is inside a registered disposable worktree")
        self.records = {}
        for record in d["host_records"]:
            raw = self.artifact(record)
            host = json.loads(raw)
            require(host.get("schemaVersion") == 1 and host["thread"]["kind"] == "codex",
                    "not a native read_thread record")
            sid = host["thread"]["id"]
            require(ID.fullmatch(sid) and sid not in self.records, "ambiguous host identity")
            self.records[sid] = host
        require(controller_id in self.records, "missing independent controller host record")
        workers = d["workers"]
        require(workers and len({w["id"] for w in workers}) == len(workers),
                "missing or ambiguous workers")
        for worker in workers:
            sid = worker["id"]
            require(sid != controller_id and sid in self.records, "worker identity mismatch")
            dispatch_bytes = self.artifact(worker["dispatch"])
            dispatch = json.loads(dispatch_bytes)
            native = dispatch.get("native_tool_result", dispatch.get("result", {}))
            require(dispatch["controller_id"] == controller_id
                    and native.get("agent_id") == sid, "native dispatch identity mismatch")
            witness = self.command(worker["dispatch_witness"], controller_id)
            require(witness["text"].strip() == hashlib.sha256(dispatch_bytes).hexdigest(),
                    "dispatch bytes lack independent controller observation")

    def artifact(self, ref):
        require(not ref.get("fixture"), "fixture artifact is not live evidence")
        path = self.proof_root / relative_path(ref["path"])
        resolved = path.resolve(strict=True)
        require(resolved.is_relative_to(self.proof_root) and resolved.is_file(),
                "artifact escaped durable proof root")
        raw = resolved.read_bytes()
        require(raw and hashlib.sha256(raw).hexdigest() == ref["sha256"],
                "artifact missing, empty, or changed")
        return raw

    def item(self, ref, sid):
        require(ref["thread_id"] == sid and sid in self.records, "wrong evidence identity")
        found = [(turn, item) for turn in self.records[sid]["turns"]
                 if turn["id"] == ref["turn_id"] for item in turn["items"]
                 if item["id"] == ref["item_id"]]
        require(len(found) == 1, "missing or ambiguous native host item")
        turn, item = found[0]
        start = utc(turn["startedAt"])
        clock_id = ref.get("clock_item_id")
        if clock_id:
            clocks = [i for i in turn["items"] if i["id"] == clock_id]
            require(len(clocks) == 1, "missing or ambiguous native clock command")
            clock = clocks[0]
            require(clock["type"] == "commandExecution" and clock["status"] == "completed"
                    and clock["exitCode"] == 0 and not clock["output"]["truncated"],
                    "native clock command incomplete")
            require(turn["items"].index(clock) < turn["items"].index(item),
                    "native clock must precede the evidenced action")
            captured = fresh(clock["output"]["text"].strip(), self.boundary, self.now)
            require(start <= captured, "clock precedes native turn")
        else:
            captured = fresh(turn["startedAt"], self.boundary, self.now)
        if turn["status"] == "completed":
            end = fresh(turn["completedAt"], self.boundary, self.now)
        else:
            require(turn["status"] == "inProgress" and item["type"] == "commandExecution"
                    and item["status"] == "completed", "incomplete native host item")
            end = self.now
        require(start <= captured <= end, "invalid host turn interval")
        return item

    def command(self, ref, sid, expected_exit=0):
        item = self.item(ref, sid)
        require(item["type"] == "commandExecution" and item["status"] == "completed"
                and item["exitCode"] == expected_exit, "host command did not complete as required")
        output = item["output"]
        require(not output["truncated"], "truncated host command evidence")
        return {"text": output["text"], "cwd": item["cwd"], "command": item["command"]}

    def worker(self, sid, role=None):
        matches_ = [w for w in self.data["workers"] if w["id"] == sid]
        require(len(matches_) == 1, "worker absent from observed dispatch")
        worker = matches_[0]
        require(role is None or worker["role"] == role, "wrong worker role")
        return worker

    def isolation(self, *, before=False):
        actual = snapshot(self.root)
        assert_isolation(actual, self.expected, self.data["shared_roots"], before=before)
        for key, query in GIT_QUERIES.items():
            observed = self.command(self.data["git"][key], self.controller)
            require(Path(observed["cwd"]).resolve() == self.root, "controller observed wrong cwd")
            require(all(arg in observed["command"] for arg in query),
                    f"controller command does not observe {key}")
            require(observed["text"].strip() == actual[key], f"controller {key} mismatch")
        return actual

    def receipt(self, ref, worker_id):
        receipt = json.loads(self.artifact(ref))
        self.worker(worker_id)
        expected = {"platform": "codex", "controller_id": self.controller,
                    "worker_id": worker_id, "task_id": worker_id,
                    "repository": self.data["repository"], "head": self.expected["head"],
                    "worktree": self.expected["worktree"]}
        require(all(receipt.get(k) == v for k, v in expected.items()),
                "startup receipt identity/repository/revision mismatch")
        fresh(receipt["started_at"], self.boundary, self.now)
        require(receipt["native_hooks"] == "unverified", "D6 receipt is not native hook evidence")
        return receipt

    def result(self, ref, sid, artifact):
        item = self.item(ref, sid)
        require(item["type"] == "agentMessage" and item.get("phase") == "final",
                "missing independent native final result")
        text = self.artifact(artifact).decode("utf-8")
        require(item["text"] == text, "result differs from native host output")
        return text


def session(evidence, worker_id):
    worker = evidence.worker(worker_id)
    evidence.isolation(before=worker["role"] == "implementer")
    return {"platform": "codex", "controller_id": evidence.controller,
            "worker_id": worker_id, "task_id": worker_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "repository": evidence.data["repository"], "worktree": evidence.expected["worktree"],
            "head": evidence.expected["head"], "native_hooks": "unverified"}


def gh_json(root, repository, endpoint):
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository),
            "invalid repository identity")
    return json.loads(run(["gh", "api", f"repos/{repository}/{endpoint}"], root))


def spec_check(root, branch, issue):
    classified = classify(branch)
    require(classified.valid and classified.namespaced and classified.issue == issue["number"],
            "branch/spec issue mismatch")
    labels = [label["name"] for label in issue["labels"]]
    require("slice" in labels and issue["state"].lower() == "open", "slice not open/labeled")
    for section in ("Parent", "What ships", "Acceptance criteria", "Branch + commit conventions"):
        require("## " + section in issue["body"], f"slice missing section: {section}")
    require("Slicer-provenance:" in issue["body"], "missing approved slice provenance")
    return {"kind": classified.kind, "issue": classified.issue,
            "requires_regression": requires_regression(branch, labels)}


def trailer(text):
    blocks = re.findall(r"^" + chr(96) * 3 + r"[^\n]*\n(.*?)^" + chr(96) * 3,
                        text, re.M | re.S)
    matches_ = [b for b in blocks if re.search(r"^PRODUCTION_VERIFY:", b, re.M)]
    require(len(matches_) == 1, "complete independent QA trailer missing or ambiguous")
    fields = {}
    for line in matches_[0].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            require(key not in fields, "duplicate QA field")
            fields[key] = value.strip()
    for key in ("RESULT", "REASON", "ARTIFACTS", "PRODUCTION_VERIFY", "ROUTE", "PROOF",
                "ASSERTIONS_CHECKED", "PROOF_SOURCE", "ENV", "DIDNT_TOUCH", "CONCERNS"):
        require(key in fields, f"incomplete independent QA verdict: {key}")
    require(fields["RESULT"] == "SUCCESS" and fields["PRODUCTION_VERIFY"] == "PASS",
            "independent QA did not pass")
    require(all(fields[k] for k in ("REASON", "PROOF", "ASSERTIONS_CHECKED", "ARTIFACTS")),
            "empty required QA proof")
    require(not re.search(r"\b(FAIL|PROVISIONAL|N/A)\b", fields["ASSERTIONS_CHECKED"]),
            "QA assertions contain unresolved outcomes")
    return fields


def validate_proof(e, bundle):
    """Validate all local evidence before making even a read-only GitHub query."""
    require(not e.isolation()["status"], "dirty verified checkout cannot prove the tested SHA")
    qa = bundle["qa_id"]
    e.worker(qa, "qa-tester")
    reviewer = bundle["reviewer_id"]
    e.worker(reviewer, "reviewer")
    implementers = {w["id"] for w in e.data["workers"] if w["role"] == "implementer"}
    require(implementers and qa != reviewer and qa not in implementers
            and reviewer not in implementers, "review/QA are not independent")
    receipt = e.receipt(bundle["receipt"], qa)
    review = e.result(bundle["review_result"], reviewer, bundle["review"])
    require(re.search(r"^VERDICT: APPROVE\s*$", review, re.M), "reviewer did not approve")
    reviewed = e.command(bundle["review_head"], reviewer)
    require(HEX.fullmatch(bundle["reviewed_sha"])
            and "rev-parse" in reviewed["command"] and "HEAD" in reviewed["command"]
            and reviewed["text"].strip() == bundle["reviewed_sha"],
            "review revision lacks independent host observation")
    result = e.result(bundle["qa_result"], qa, bundle["verdict"])
    fields = trailer(result)
    require(fields["PROOF_SOURCE"] == "codex:" + qa + "@" + receipt["started_at"],
            "QA proof source mismatch")
    sha, sep, started = fields["ENV"].partition("@")
    require(sep and sha == e.expected["head"], "QA ENV revision mismatch")
    fresh(started, e.boundary, e.now)
    require(bundle["repository"] == e.data["repository"]
            and bundle["slice"] == e.expected["slice"]
            and bundle["tested_sha"] == sha, "proof exercise coordinates mismatch")
    required = routes(e.root, bundle["changed_paths"])
    declared = set(fields["ROUTE"].replace("static-check", "static").split("+"))
    require(declared == required, "QA route differs from authoritative union")
    supplied = {p["class"] for p in bundle["proofs"]}
    require(supplied == required, "missing required proof class: " + ",".join(required - supplied))
    artifact_paths = set()
    for proof in bundle["proofs"]:
        kind = proof["class"]
        require(kind in {"command-run", "static", "browser"},
                f"{kind} is unverified in the D6 inventory")
        head = e.command(proof["head"], qa)
        require("rev-parse" in head["command"] and "HEAD" in head["command"]
                and head["text"].strip() == sha, "proof revision lacks host observation")
        command = e.command(proof["command"], qa)
        require(Path(command["cwd"]).resolve() == e.root
                and Path(head["cwd"]).resolve() == e.root, "proof command wrong worktree")
        refs = proof["artifacts"]
        raw = {name: e.artifact(ref) for name, ref in refs.items()}
        artifact_paths.update(ref["path"] for ref in refs.values())
        if kind in {"command-run", "static"}:
            require(command["text"].encode("utf-8") == raw["output"],
                    "proof output differs from host command bytes")
            text = raw["output"].decode("utf-8")
            require(text.strip() and not re.search(r"\b(fixture|synthetic|sess-test)-", text, re.I),
                    "fixture or empty live evidence")
            if kind == "static":
                require(re.search(r"\bgrep count=\d+\b", text), "static assertion count missing")
        else:
            witness = e.command(proof["controller_witness"], e.controller)
            require(witness["text"] == command["text"],
                    "browser bytes/actions lack independent controller correspondence")
            manifest = json.loads(command["text"])
            require(manifest["tested_sha"] == sha and manifest["interaction"] == "real-browser"
                    and manifest["declared_behavior"] == "PASS", "browser interaction unproven")
            require(set(("screenshot", "rendered", "meta", "console")) <= raw.keys(),
                    "browser screenshot/rendered/meta/console proof missing")
            require(manifest["artifacts"] == {k: v["sha256"] for k, v in refs.items()},
                    "browser artifact bytes lack native command correspondence")
            require(raw["screenshot"].startswith(b"\x89PNG\r\n\x1a\n")
                    or raw["screenshot"].startswith(b"\xff\xd8\xff"), "invalid screenshot bytes")
            meta = json.loads(raw["meta"])
            require(meta["sha"] == sha and meta["stale"] is False, "stale browser meta revision")
            fresh(meta["started_at"], e.boundary, e.now)
            require(json.loads(raw["console"]) == [], "browser console errors remain")
            rendered = raw["rendered"].decode("utf-8")
            require(rendered.strip() and "SERVER STALE" not in rendered
                    and not re.search(r"\b(fixture|synthetic|test-data)\b", rendered, re.I),
                    "browser rendered proof stale/fixture/empty")
            require(all(str(bundle[key]) in rendered for key in ("slice", "pr")),
                    "actual slice/PR data absent from rendered proof")
            require({"runboard-refresh", "health-strip-refresh"} <= set(manifest["actions"])
                    and "#health-strip-content" in manifest["selectors"],
                    "required dashboard refresh interactions missing")
    require(set(x.strip() for x in fields["ARTIFACTS"].split(",")) == artifact_paths,
            "QA trailer artifact inventory mismatch")
    return required


def live_coordinates(e, bundle, close=False):
    repo = run(["gh", "repo", "view", "--json", "nameWithOwner"], e.root)
    require(json.loads(repo)["nameWithOwner"] == e.data["repository"], "wrong live repository")
    repository = e.data["repository"]
    pr = gh_json(e.root, repository, f"pulls/{bundle['pr']}")
    require(pr["merged"] and pr["base"]["ref"] == "develop"
            and pr["merge_commit_sha"] == bundle["tested_sha"], "PR not delivered on tested SHA")
    branch = classify(pr["head"]["ref"])
    require(branch.valid and branch.namespaced and branch.issue == bundle["slice"],
            "wrong PR branch")
    require(pr["head"]["sha"] == bundle["reviewed_sha"], "reviewed PR head changed")
    require(re.search(rf"(?i)\bCloses\s+#{bundle['slice']}\b", pr["body"] or ""),
            "PR does not bind the slice")
    files = []
    page = 1
    while True:
        batch = gh_json(e.root, repository, f"pulls/{bundle['pr']}/files?per_page=100&page={page}")
        files.extend(f["filename"] for f in batch)
        if len(batch) < 100:
            break
        page += 1
    if bundle["scope"] == "slice":
        require(set(files) == set(bundle["changed_paths"]), "proof paths differ from actual PR")
    else:
        prd = gh_json(e.root, repository, f"issues/{bundle['prd']}")
        require(prd["state"] == "open" and "prd" in [l["name"] for l in prd["labels"]],
                "parent PRD not open/labeled")
        children = []
        page = 1
        while True:
            batch = gh_json(e.root, repository,
                            f"issues/{bundle['prd']}/sub_issues?per_page=100&page={page}")
            children.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        require(children and all(c["state"] == "closed" for c in children),
                "unfinished or missing child slices")
        require(set(bundle["completed_children"]) == {c["number"] for c in children},
                "child completion inventory changed")
        require(bundle["slice"] in bundle["completed_children"], "slice is not a PRD child")
        base = e.expected["prd_base"]
        require(HEX.fullmatch(base), "missing independently recorded PRD base")
        comparison = gh_json(e.root, repository, f"compare/{base}...{bundle['tested_sha']}")
        require(comparison["merge_base_commit"]["sha"] == base,
                "PRD base is not an ancestor of tested revision")
        files = comparison["files"]
        require(len(files) < 300, "cumulative comparison may be truncated")
        require({f["filename"] for f in files} == set(bundle["changed_paths"]),
                "proof paths differ from cumulative PRD diff")
    if close:
        path = e.root / "tools" / "pipe" / "prd-close"
        loader = importlib.machinery.SourceFileLoader("openai_closure_guard", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        require(module._qa_verified_pass_exists(module._load_trace(), bundle["prd"]),
                "existing closure prerequisite: recorded QA PASS missing")


def delegate(e, bundle, close=False):
    require(bundle["scope"] in {"slice", "prd"}, "unknown verification scope")
    if close:
        require(bundle["scope"] == "prd", "bounded slice proof cannot close the parent PRD")
    required = validate_proof(e, bundle)
    live_coordinates(e, bundle, close)
    if close:
        argv = [sys.executable, str(e.root / "tools/pipe/prd-close"), str(bundle["prd"])]
    else:
        argv = [sys.executable, str(e.root / "tools/pipe/qa-verify"), "--verdict", "PASS",
                "--route", "+".join(sorted(required)), "--pr", str(bundle["pr"]),
                "--slice", str(bundle["slice"]), "--proof-source",
                "codex:" + bundle["qa_id"] + "@" +
                e.receipt(bundle["receipt"], bundle["qa_id"])["started_at"]]
        if bundle["scope"] == "prd":
            argv += ["--prd", str(bundle["prd"])]
    result = subprocess.run(argv, cwd=e.root, env=child_env(e.controller))
    return result.returncode


def preflight(root, evidence=None):
    statuses = {}
    for binary in ("git", "gh", "bash"):
        statuses[binary] = "missing-tool"
        if shutil.which(binary):
            try:
                run([binary, "--version"], root)
                statuses[binary] = "observed"
            except (Refusal, OSError, subprocess.SubprocessError):
                statuses[binary] = "command-failure"
    statuses["python"] = "observed"
    statuses["repository"] = "observed"
    if statuses["gh"] == "observed":
        try:
            run(["gh", "auth", "status"], root)
            statuses["authentication"] = "observed"
        except Refusal:
            statuses["authentication"] = "command-failure"
    if evidence is None:
        statuses["native-workers-and-isolation"] = "unobserved"
    else:
        evidence.isolation()
        statuses["native-workers-and-isolation"] = "observed"
    statuses["native-hooks"] = "unverified; inventory pending slice #1441"
    statuses["complete-discovery"] = "pending slice #1442"
    ready = all(v == "observed" for k, v in statuses.items()
                if k not in {"native-hooks", "complete-discovery"})
    return {"scope": "D6-slice", "ready": ready, "full_prd_ready": False,
            "capabilities": statuses,
            "handoff": "" if ready else "Controller: supply current native records and missing prerequisites."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("instructions", "routes", "preflight", "isolation",
                                          "session", "spec", "qa-verify", "prd-close"))
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--proof-root")
    parser.add_argument("--observations")
    parser.add_argument("--controller-id")
    parser.add_argument("--worker-id")
    parser.add_argument("--bundle")
    parser.add_argument("--before", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = root_at()
        if args.command in {"instructions", "routes"}:
            function = instructions if args.command == "instructions" else routes
            print(json.dumps(sorted(function(root, args.path)), indent=2))
            return 0
        evidence = None
        if args.observations:
            require(args.proof_root and args.controller_id, "proof root/controller ID required")
            evidence = Evidence(args.proof_root, args.observations, args.controller_id, root)
        if args.command == "preflight":
            result = preflight(root, evidence)
            print(json.dumps(result, indent=2))
            return 0 if result["ready"] else 2
        require(evidence is not None, "current independent controller observations required")
        if args.command == "isolation":
            result = evidence.isolation(before=args.before)
        elif args.command == "session":
            result = session(evidence, args.worker_id)
        elif args.command == "spec":
            evidence.isolation(before=True)
            issue = gh_json(root, evidence.data["repository"],
                            f"issues/{evidence.expected['slice']}")
            result = spec_check(root, evidence.expected["branch"], issue)
        else:
            require(args.bundle, "independent QA proof bundle required")
            bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
            return delegate(evidence, bundle, args.command == "prd-close")
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            subprocess.SubprocessError) as exc:
        print(f"openai-workflow: refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
