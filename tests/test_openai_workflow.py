"""Contract tests only: all records/repos/logs are disposable synthetic fixtures."""
from contextlib import redirect_stderr, redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from tools import openai_workflow as workflow
from tools.workflow_branch import KINDS, classify, requires_regression

ROOT = Path(__file__).resolve().parent.parent


class TestBranchGates(unittest.TestCase):
    def test_truth_table_and_labels(self):
        for prefix in ("", "codex/"):
            for kind in KINDS:
                branch = prefix + kind + "/42-example"
                result = classify(branch)
                self.assertEqual((result.kind, result.issue, result.valid), (kind, 42, True))
                for labels in ([], ["root-cause"], ["unrelated"]):
                    with self.subTest(branch=branch, labels=labels):
                        self.assertEqual(requires_regression(branch, labels),
                                         kind == "fix" or "root-cause" in labels)
        for branch in ("codex/fix/x", "codex/fix/", "codex/42-example",
                       "codex/unknown/42-example", "codex//42-example",
                       "codex/hotfix/no-issue", "codex/fix/42-BAD", "codex/codex/fix/42-x",
                       "main", "HEAD", "", None):
            with self.subTest(branch=branch):
                self.assertFalse(classify(branch).valid)
                self.assertIsNone(classify(branch).kind)
                self.assertFalse(requires_regression(branch))
                self.assertTrue(requires_regression(branch, ["root-cause"]))
        self.assertEqual(classify("hotfix/legacy").kind, "hotfix")
        self.assertFalse(classify("hotfix/legacy").valid)
        self.assertTrue(requires_regression("fix/legacy"))

    def test_actual_precommit_branch_section_preserves_legacy_cases(self):
        source = (ROOT / ".githooks/pre-commit").read_text(encoding="utf-8")
        section = source[source.index("if [ -z"):source.index(
            "# ---------------------------------------------------------------------------")]
        cases = {"main": 1, "": 0, "hotfix/legacy": 1, "HEAD": 1, "develop": 1}
        for prefix in ("", "codex/"):
            for kind in KINDS:
                cases[prefix + kind + "/42-example"] = 0
        for branch in ("codex/fix/no-issue", "codex/42-example", "codex/new/42-x",
                       "codex/fix/42-BAD", "codex/hotfix/summary"):
            cases[branch] = 1
        for branch, expected in cases.items():
            with self.subTest(branch=branch):
                env = workflow.child_env()
                env["branch"] = branch
                result = subprocess.run(["bash", "-c", section], env=env,
                                        capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_ship_and_reviewer_execute_shared_label_free_fix_selector(self):
        for path in (".claude/skills/ship/SKILL.md", ".claude/agents/reviewer.md"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("python tools/workflow_branch.py", text)
            self.assertIn("requires_regression", text)
            self.assertIn("root-cause", text)
            result = subprocess.run([sys.executable, str(ROOT / "tools/workflow_branch.py"),
                                     "codex/fix/1440-example"], capture_output=True, text=True,
                                    encoding="utf-8", env=workflow.child_env())
            self.assertEqual(result.returncode, 0)
            self.assertTrue(json.loads(result.stdout)["requires_regression"])

    def test_label_free_codex_fix_enters_health_ordering(self):
        from dashboard import health
        for branch, total in (("fix/1440-example", 1), ("codex/fix/1440-example", 1),
                              ("codex/feat/1440-example", 0), ("codex/fix/bad", 0)):
            for labels in ([], [{"name": "root-cause"}]):
                prs = [{"number": 1500, "headRefName": branch, "labels": labels,
                        "closingIssuesReferences": [{"number": 1440}], "mergeCommit": {}}]
                with self.subTest(branch=branch, labels=labels), patch.object(
                        health, "_health_gh_fetch", return_value=(0, json.dumps(prs))):
                    self.assertEqual(health.check_test_ordering()["total"], total)
        with patch.object(health, "_health_gh_fetch", return_value=(0, json.dumps([
                {"number": 800, "headRefName": "fix/800-example", "mergeCommit": {}}]))):
            result = health.check_test_ordering()
            self.assertEqual(result["grandfathered"], 1)
            self.assertEqual(result["total"], 0)

    def test_both_health_hotfix_selectors_preserve_labels(self):
        from dashboard import health
        for branch, skip in (("hotfix/legacy", True), ("hotfix/42-example", True),
                             ("codex/hotfix/42-example", True),
                             ("codex/hotfix/missing-issue", False),
                             ("codex/feat/42-example", False)):
            for labels in ([], [{"name": "trivial"}]):
                pr = {"number": 1500, "headRefName": branch, "labels": labels,
                      "files": [{"path": "dashboard/health.py"}], "body": "", "comments": []}
                with patch.dict(os.environ, {"_PROOF_PRESENCE_PR_OVERRIDE": json.dumps([pr]),
                                             "_PROOF_INTEGRITY_PR_OVERRIDE": json.dumps([pr])}):
                    presence = health.check_proof_presence()
                    integrity = health.check_proof_integrity()
                expected_skip = skip or bool(labels)
                self.assertEqual(presence["window"], 0 if expected_skip else 1)
                self.assertEqual(bool(presence.get("missing_prs")), not expected_skip)
                self.assertEqual(integrity["result"], "WARN" if expected_skip else "FAIL")


class TestInstructions(unittest.TestCase):
    def test_global_and_area_union(self):
        actual = workflow.instructions(ROOT, [
            "tools/openai_workflow.py", "docs/deleted.md", ".agents\\skills\\ship\\SKILL.md",
            "tools/openai_workflow.py"])
        self.assertEqual(len(actual), len(set(actual)))
        for source in workflow.GLOBAL_SOURCES:
            self.assertIn(source, actual)
        for scope in ("docs", "isolation", "slicing"):
            self.assertIn(f".claude/rules/{scope}.md", actual)
        self.assertIn(".claude/rules/docs.md", workflow.instructions(ROOT, ["AGENTS.md"]))

    def test_path_refusals(self):
        for path in ("", "../x", "a/../x", "/tmp/x", "C:/x", "a//b", "a/./b", "a\0b"):
            with self.subTest(path=path), self.assertRaises(workflow.Refusal):
                workflow.instructions(ROOT, [path])

    def test_missing_source_and_malformed_generated_header(self):
        with tempfile.TemporaryDirectory(prefix="openai-resolver-") as tmp:
            root = Path(tmp)
            with self.assertRaises((workflow.Refusal, FileNotFoundError)):
                workflow.instructions(root, [])
            rules = root / ".claude/rules/isolation.md"
            rules.parent.mkdir(parents=True)
            rules.write_text("---\npaths: wrong\n---\n", encoding="utf-8")
            with self.assertRaisesRegex(workflow.Refusal, "malformed"):
                workflow.instructions(root, ["tools/openai_workflow.py"])

    def test_authoritative_route_union_and_health_new_path_parity(self):
        from dashboard import health
        for paths in (["AGENTS.md"], [".agents/skills/ship/SKILL.md"],
                      ["AGENTS.md", "tools/openai_workflow.py", "dashboard/health.py"]):
            self.assertEqual(workflow.routes(ROOT, paths), health._classify_route(paths))
        self.assertEqual(workflow.routes(ROOT, ["tools/ci-checks.sh"]),
                         {"command-run", "failing-canary"})
        self.assertEqual(workflow.routes(ROOT, ["AGENTS.md", "dashboard/health.py"]),
                         {"static", "command-run", "browser"})

    def test_environment_is_utf8_and_has_exactly_one_path_key(self):
        with patch.dict(os.environ, {"PATH": "first", "Path": "second"}, clear=True):
            before = dict(os.environ)
            env = workflow.child_env(str(uuid.uuid4()))
            self.assertEqual([k for k in env if k.upper() == "PATH"], ["PATH"])
            self.assertEqual(env["PYTHONUTF8"], "1")
            self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
            self.assertTrue(env["CLAUDE_SESSION_ID"].startswith("codex:"))
            self.assertEqual(dict(os.environ), before)
        with self.assertRaises(workflow.Refusal):
            workflow.child_env("orchestrator")

    def test_preflight_without_native_observations_is_not_ready(self):
        with patch.object(workflow.shutil, "which", return_value="/available"), patch.object(
                workflow, "run", return_value="authenticated") as command:
            result = workflow.preflight(ROOT)
        self.assertFalse(result["ready"])
        self.assertFalse(result["full_prd_ready"])
        self.assertIn("unverified", result["capabilities"]["native-hooks"])
        self.assertEqual(command.call_args.args[0], ["gh", "auth", "status"])


class ProofFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openai-proof-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        source = self.base / "source"
        source.mkdir()
        self.root = self.base / "worker"
        self.proof = self.base / "proof"
        self.proof.mkdir()
        env = workflow.child_env()
        env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                   GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        def git(cwd, *args):
            return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                                  capture_output=True, text=True, encoding="utf-8").stdout.strip()
        git(source, "init", "-b", "develop")
        route = source / workflow.ROUTE_SOURCE
        route.parent.mkdir(parents=True)
        route.write_bytes((ROOT / workflow.ROUTE_SOURCE).read_bytes())
        git(source, "add", workflow.ROUTE_SOURCE)
        git(source, "commit", "-m", "test: create disposable contract fixture")
        git(source, "worktree", "add", "-b", "codex/feat/42-example", str(self.root))
        self.actual = workflow.snapshot(self.root)
        self.sha = self.actual["head"]
        self.ids = {role: str(uuid.uuid4()) for role in
                    ("controller", "implementer", "reviewer", "qa-tester")}
        self.now = datetime.now(timezone.utc)
        self.start = (self.now - timedelta(seconds=20)).isoformat()
        self.records = {sid: {"schemaVersion": 1, "thread": {"id": sid, "kind": "codex"},
                             "turns": [{"id": "turn", "status": "completed",
                                        "startedAt": int((self.now - timedelta(seconds=15)).timestamp()),
                                        "completedAt": int((self.now - timedelta(seconds=5)).timestamp()),
                                        "items": []}]} for sid in self.ids.values()}
        self.obs = {"schema": 1, "platform": "codex", "provenance": "controller-observed-native",
                    "controller_id": self.ids["controller"], "repository": "owner/example",
                    "started_at": self.start, "shared_roots": [str(source)],
                    "expected": {"worktree": str(self.root), "common": self.actual["common"],
                                 "branch": self.actual["branch"], "start_sha": self.sha,
                                 "head": self.sha, "slice": 42}, "git": {}, "workers": []}
        for key, query in workflow.GIT_QUERIES.items():
            self.obs["git"][key] = self.command("controller", "git " + " ".join(query),
                                               self.actual[key] + "\n")
        for role in ("implementer", "reviewer", "qa-tester"):
            dispatch = self.artifact(role + "-dispatch.json",
                                    json.dumps({"controller_id": self.ids["controller"],
                                                "native_tool_result": {"agent_id": self.ids[role]}}))
            self.obs["workers"].append({"id": self.ids[role], "role": role,
                                        "dispatch": dispatch,
                                        "dispatch_witness": self.command(
                                            "controller", "hash captured dispatch",
                                            dispatch["sha256"] + "\n")})
        receipt = {"platform": "codex", "controller_id": self.ids["controller"],
                   "worker_id": self.ids["qa-tester"], "task_id": self.ids["qa-tester"],
                   "repository": "owner/example", "head": self.sha, "worktree": str(self.root),
                   "started_at": self.start, "native_hooks": "unverified"}
        self.bundle = {"scope": "slice", "repository": "owner/example", "slice": 42,
                       "pr": 43, "prd": 40, "tested_sha": self.sha,
                       "reviewed_sha": self.sha,
                       "review_head": self.command("reviewer", "git rev-parse HEAD", self.sha + "\n"),
                       "qa_id": self.ids["qa-tester"], "reviewer_id": self.ids["reviewer"],
                       "changed_paths": ["AGENTS.md", "tools/openai_workflow.py", "dashboard/health.py"],
                       "receipt": self.artifact("receipt.json", json.dumps(receipt)), "proofs": []}
        head = self.command("qa-tester", "git rev-parse HEAD", self.sha + "\n")
        for kind, output in (("static", "grep count=3\n"), ("command-run", "checked output\n")):
            ref = self.artifact(kind + ".txt", output)
            self.bundle["proofs"].append({"class": kind, "head": head, "artifacts": {"output": ref},
                                          "command": self.command("qa-tester", "verify " + kind, output)})
        browser = {
            "screenshot": self.artifact("screen.png", b"\x89PNG\r\n\x1a\nimage-fixture"),
            "rendered": self.artifact("rendered.txt", "Run-board slice 42 PR 43\nHealth FAIL: unrelated"),
            "console": self.artifact("console.json", "[]"),
            "meta": self.artifact("meta.json", json.dumps(
                {"sha": self.sha, "stale": False, "started_at": self.start}))}
        manifest = {"tested_sha": self.sha, "interaction": "real-browser", "declared_behavior": "PASS",
                    "actions": ["runboard-refresh", "health-strip-refresh"],
                    "selectors": ["#health-strip-content"],
                    "artifacts": {k: v["sha256"] for k, v in browser.items()}}
        self.bundle["proofs"].append({"class": "browser", "head": head, "artifacts": browser,
                                      "controller_witness": self.command(
                                          "controller", "observe native browser artifacts/actions",
                                          json.dumps(manifest)),
                                      "command": self.command("qa-tester", "retain browser evidence",
                                                              json.dumps(manifest))})
        review = "VERDICT: APPROVE\nROUND: 1\n"
        self.bundle["review"] = self.artifact("review.md", review)
        self.bundle["review_result"] = self.final("reviewer", review)
        artifact_names = [r["path"] for p in self.bundle["proofs"] for r in p["artifacts"].values()]
        fence = chr(96) * 3
        qa = "\n".join([fence, "RESULT: SUCCESS", "REASON: criteria exercised",
                        "ARTIFACTS: " + ", ".join(artifact_names), "PRODUCTION_VERIFY: PASS",
                        "ROUTE: browser+command-run+static", "PROOF: captured assertions",
                        "ASSERTIONS_CHECKED: renders=PASS, exit_code=PASS, static=PASS",
                        "PROOF_SOURCE: codex:" + self.ids["qa-tester"] + "@" + self.start,
                        "ENV: " + self.sha + "@" + self.start, "DIDNT_TOUCH: none", "CONCERNS: none",
                        fence, ""])
        self.bundle["verdict"] = self.artifact("verdict.md", qa)
        self.bundle["qa_result"] = self.final("qa-tester", qa)
        self.save_observations()
        self.e = workflow.Evidence(self.proof, self.obs_path, self.ids["controller"],
                                   self.root, self.now)
        self.logs = {name: self.base / name for name in ("events.jsonl", "trace.jsonl", "closure")}
        for path in self.logs.values():
            path.write_bytes(b"unchanged\n")

    def artifact(self, name, value):
        raw = value.encode("utf-8") if isinstance(value, str) else value
        (self.proof / name).write_bytes(raw)
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest()}

    def item(self, role, value):
        sid = self.ids[role]
        items = self.records[sid]["turns"][0]["items"]
        value["id"] = "item-" + str(len(items))
        items.append(value)
        return {"thread_id": sid, "turn_id": "turn", "item_id": value["id"]}

    def command(self, role, command, output):
        return self.item(role, {"type": "commandExecution", "status": "completed",
                                "exitCode": 0, "cwd": str(self.root), "command": command,
                                "output": {"text": output, "truncated": False}})

    def final(self, role, text):
        return self.item(role, {"type": "agentMessage", "phase": "final_answer", "text": text})

    def save_observations(self):
        self.obs["host_records"] = [self.artifact(role + "-host.json", json.dumps(
            self.records[sid])) for role, sid in self.ids.items()]
        self.obs_path = self.proof / "observations.json"
        self.obs_path.write_text(json.dumps(self.obs), encoding="utf-8")

    def assert_refuses_without_downstream(self, bundle=None):
        bundle = bundle or self.bundle
        path = self.proof / "bundle.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        for verb in ("qa-verify", "prd-close"):
            candidate = copy.deepcopy(bundle)
            candidate["scope"] = "prd" if verb == "prd-close" else "slice"
            path.write_text(json.dumps(candidate), encoding="utf-8")
            with self.subTest(verb=verb), patch.object(workflow, "root_at", return_value=self.root), \
                    patch.object(workflow, "Evidence", return_value=self.e), \
                    patch.object(workflow, "snapshot", return_value=self.actual), \
                    patch.object(workflow, "live_coordinates") as live, \
                    patch.object(workflow.subprocess, "run") as downstream, \
                    redirect_stderr(io.StringIO()):
                rc = workflow.main([verb, "--bundle", str(path), "--observations", str(self.obs_path),
                                    "--controller-id", self.ids["controller"],
                                    "--proof-root", str(self.proof)])
            self.assertNotEqual(rc, 0)
            live.assert_not_called()
            downstream.assert_not_called()
            for log in self.logs.values():
                self.assertEqual(log.read_bytes(), b"unchanged\n")


class TestProofAndIsolation(ProofFixture):
    def test_valid_contract_and_positive_delegation(self):
        self.assertEqual(workflow.validate_proof(self.e, self.bundle),
                         {"browser", "command-run", "static"})
        with patch.object(workflow, "snapshot", return_value=self.actual), patch.object(
                workflow, "live_coordinates") as live, patch.object(
                workflow.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)) as call:
            self.assertEqual(workflow.delegate(self.e, self.bundle), 7)
        live.assert_called_once()
        argv = call.call_args.args[0]
        self.assertTrue(Path(argv[1]).as_posix().endswith("tools/pipe/qa-verify"))
        self.assertNotIn("--prd", argv)
        self.assertEqual(call.call_args.kwargs["env"]["CLAUDE_SESSION_ID"],
                         "codex:" + self.ids["controller"])
        self.assertEqual(call.call_args.kwargs["env"]["PYTHONUTF8"], "1")

    def test_isolation_negative_matrix(self):
        cases = [("top", str(self.base / "source")), ("common", str(self.base)),
                 ("branch", "codex/feat/99-wrong"), ("head", "0" * 40),
                 ("registry", ""), ("status", "?? stray"),
                 ("registry", self.actual["registry"] + "\n\n" + self.actual["registry"])]
        for field, value in cases:
            actual = {**self.actual, field: value}
            with self.subTest(field=field), self.assertRaises(workflow.Refusal):
                workflow.assert_isolation(actual, self.obs["expected"],
                                          self.obs["shared_roots"], before=True)
        with self.assertRaisesRegex(workflow.Refusal, "shared-root"):
            workflow.assert_isolation(self.actual, self.obs["expected"], [str(self.root)])
        expected = {**self.obs["expected"], "start_sha": "0" * 40}
        with self.assertRaisesRegex(workflow.Refusal, "starting"):
            workflow.assert_isolation(self.actual, expected, self.obs["shared_roots"], before=True)

    def test_detached_verification_requires_matching_registration(self):
        actual = {**self.actual, "branch": "",
                  "registry": "worktree " + str(self.root) + "\nHEAD " + self.sha + "\ndetached"}
        expected = {**self.obs["expected"], "branch": ""}
        workflow.assert_isolation(actual, expected, self.obs["shared_roots"])
        with self.assertRaises(workflow.Refusal):
            workflow.assert_isolation(actual, expected, self.obs["shared_roots"], before=True)

    def test_missing_receipt_zero_downstream(self):
        (self.proof / self.bundle["receipt"]["path"]).unlink()
        self.assert_refuses_without_downstream()

    def test_receipt_fields_and_freshness_refuse(self):
        original = json.loads(self.e.artifact(self.bundle["receipt"]))
        for key, value in (("worker_id", self.ids["controller"]), ("head", "0" * 40),
                           ("repository", "wrong/repo"), ("native_hooks", "PASS"),
                           ("started_at", (self.now - timedelta(minutes=31)).isoformat())):
            with self.subTest(field=key):
                self.bundle["receipt"] = self.artifact("receipt.json", json.dumps(
                    {**original, key: value}))
                self.assert_refuses_without_downstream()

    def test_artifact_traversal_and_fixture_refuse(self):
        ref = self.bundle["receipt"]
        for update in ({"path": "../outside"}, {"fixture": True}, {"sha256": "0" * 64}):
            with self.subTest(update=update), self.assertRaises(workflow.Refusal):
                self.e.artifact({**ref, **update})

    def test_browser_without_independent_controller_witness_refuses(self):
        del self.bundle["proofs"][-1]["controller_witness"]
        self.assert_refuses_without_downstream()

    def test_wrong_review_revision_refuses(self):
        self.bundle["reviewed_sha"] = "0" * 40
        self.assert_refuses_without_downstream()

    def test_observed_native_final_answer_phase_is_accepted(self):
        for key, role in (("review_result", "reviewer"), ("qa_result", "qa-tester")):
            self.e.item(self.bundle[key], self.ids[role])["phase"] = "final_answer"
        self.assertEqual(workflow.validate_proof(self.e, self.bundle),
                         {"browser", "command-run", "static"})

    def test_dirty_verified_checkout_refuses_even_if_controller_observed_it(self):
        self.actual["status"] = "M tracked.py"
        self.e.item(self.e.data["git"]["status"], self.ids["controller"])["output"]["text"] = (
            self.actual["status"] + "\n")
        with patch.object(workflow, "snapshot", return_value=self.actual):
            with self.assertRaisesRegex(workflow.Refusal, "dirty"):
                workflow.validate_proof(self.e, self.bundle)
        self.assert_refuses_without_downstream()

    def test_completed_controller_command_in_active_turn_is_observable(self):
        host = self.e.records[self.ids["controller"]]["turns"][0]
        host.update(status="inProgress", completedAt=None)
        self.assertEqual(workflow.validate_proof(self.e, self.bundle),
                         {"browser", "command-run", "static"})
        host["items"][0]["status"] = "inProgress"
        self.assert_refuses_without_downstream()

    def test_incomplete_native_final_result_refuses(self):
        host = self.e.records[self.ids["qa-tester"]]["turns"][0]
        host.update(status="inProgress", completedAt=None)
        self.assert_refuses_without_downstream()

    def test_long_controller_turn_requires_preceding_fresh_native_clock(self):
        host = self.e.records[self.ids["controller"]]["turns"][0]
        host.update(startedAt=int((self.now - timedelta(hours=1)).timestamp()))
        self.assert_refuses_without_downstream()
        host["items"].insert(0, {"id": "clock", "type": "commandExecution",
                                  "status": "completed", "exitCode": 0,
                                  "output": {"text": self.start, "truncated": False}})
        for ref in self.e.data["git"].values():
            ref["clock_item_id"] = "clock"
        self.bundle["proofs"][-1]["controller_witness"]["clock_item_id"] = "clock"
        self.assertEqual(workflow.validate_proof(self.e, self.bundle),
                         {"browser", "command-run", "static"})
        host["items"].append(host["items"].pop(0))
        self.assert_refuses_without_downstream()

    def test_missing_each_required_proof_class_zero_downstream(self):
        for kind in ("static", "command-run", "browser"):
            candidate = copy.deepcopy(self.bundle)
            candidate["proofs"] = [p for p in candidate["proofs"] if p["class"] != kind]
            with self.subTest(kind=kind):
                self.assert_refuses_without_downstream(candidate)

    def test_changed_artifact_zero_downstream(self):
        (self.proof / "screen.png").write_bytes(b"changed")
        self.assert_refuses_without_downstream()

    def test_wrong_identity_revision_repository_zero_downstream(self):
        for key, value in (("qa_id", self.ids["implementer"]), ("tested_sha", "0" * 40),
                           ("repository", "wrong/repo"), ("reviewer_id", self.ids["qa-tester"])):
            with self.subTest(key=key):
                self.assert_refuses_without_downstream({**self.bundle, key: value})

    def test_stale_future_preboundary_and_truncated_host_zero_downstream(self):
        host = self.e.records[self.ids["qa-tester"]]["turns"][0]
        original = copy.deepcopy(host)
        for change in ({"startedAt": int((self.now - timedelta(minutes=31)).timestamp())},
                       {"completedAt": int((self.now + timedelta(minutes=1)).timestamp())},
                       {"startedAt": int((self.now - timedelta(seconds=30)).timestamp())}):
            host.update(change)
            self.assert_refuses_without_downstream()
            host.update(copy.deepcopy(original))
        host["items"][0]["output"]["truncated"] = True
        self.assert_refuses_without_downstream()

    def test_self_declared_or_fixture_observations_fail(self):
        for field, value in (("host_records", []), ("fixture", True),
                             ("controller_id", self.ids["implementer"])):
            original = self.obs[field] if field in self.obs else None
            self.obs[field] = value
            self.obs_path.write_text(json.dumps(self.obs), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(workflow.Refusal):
                workflow.Evidence(self.proof, self.obs_path, self.ids["controller"], self.root)
            if original is None:
                del self.obs[field]
            else:
                self.obs[field] = original

    def test_disposable_proof_root_fails(self):
        with self.assertRaisesRegex(workflow.Refusal, "disposable"):
            workflow.Evidence(self.root, self.obs_path, self.ids["controller"], self.root)

    def test_missing_controller_correspondence_fails(self):
        ref = self.obs["git"]["head"]
        item = self.e.item(ref, self.ids["controller"])
        item["output"]["text"] = "0" * 40
        self.assert_refuses_without_downstream()

    def test_verdict_mismatch_and_nonpass_refuse(self):
        ref = self.bundle["qa_result"]
        item = self.e.item(ref, self.ids["qa-tester"])
        item["text"] = item["text"].replace("PRODUCTION_VERIFY: PASS", "PRODUCTION_VERIFY: FAIL")
        self.bundle["verdict"] = self.artifact("verdict.md", item["text"])
        self.assert_refuses_without_downstream()

    def test_closed_prerequisite_unfinished_children_zero_delegate(self):
        bundle = {**self.bundle, "scope": "prd", "completed_children": [42]}
        pr = {"merged": True, "base": {"ref": "develop"}, "merge_commit_sha": self.sha,
              "head": {"ref": "codex/feat/42-example", "sha": self.sha}, "body": "Closes #42"}
        answers = [pr, [{"filename": p} for p in bundle["changed_paths"]],
                   {"state": "open", "labels": [{"name": "prd"}]},
                   [{"number": 42, "state": "open"}]]
        for close in (False, True):
            with patch.object(workflow, "snapshot", return_value=self.actual), patch.object(
                    workflow, "run", return_value='{"nameWithOwner":"owner/example"}'), patch.object(
                    workflow, "gh_json", side_effect=answers), patch.object(
                    workflow.subprocess, "run") as downstream:
                with self.assertRaisesRegex(workflow.Refusal, "unfinished"):
                    workflow.delegate(self.e, bundle, close=close)
                downstream.assert_not_called()
        for log in self.logs.values():
            self.assertEqual(log.read_bytes(), b"unchanged\n")


class TestEntryRefusals(unittest.TestCase):
    def test_real_entrypoints_missing_proof_have_no_pass_or_close_state_change(self):
        with tempfile.TemporaryDirectory(prefix="openai-entry-refusal-") as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            trace = root / "trace.jsonl"
            events.write_text("", encoding="utf-8")
            trace.write_text("", encoding="utf-8")
            env = workflow.child_env()
            env.update(QA_VERIFY_EVENTS_LOG_OVERRIDE=str(events), TRACE_LOG_OVERRIDE=str(trace))
            for verb in ("qa-verify", "prd-close"):
                result = subprocess.run([sys.executable, str(ROOT / "tools/openai_workflow.py"), verb],
                                        cwd=ROOT, env=env, capture_output=True, text=True,
                                        encoding="utf-8")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("current independent controller observations required", result.stderr)
                self.assertEqual(events.read_bytes(), b"")
                self.assertEqual(trace.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
