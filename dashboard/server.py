#!/usr/bin/env python3
"""
dashboard/server.py — project-claude workflow dashboard server.

Serves (ADR-0080 D1 — reduced to the run-board + a thin health strip;
Architecture/Live/Health/Firing tabs and their 15 UI-only routes deleted):
        GET /               -> dashboard/index.html
        GET /api/health       -> JSON {auditMeta, auditSubagents} — feeds the health strip
        GET /api/status           -> JSON aggregated liveness snapshot: sha/branch, hooks_live, last_event, main_green, health_summary, open_work (slice #859)
        GET /api/meta             -> JSON {sha, started_at, stale} server-identity endpoint (ADR-0056/0057/0058)
        GET /api/trace-runs[?limit=N] -> JSON recorded pipeline-span chains from the v3 trace store — the Run-board's recorded-runs panel (slice #1082, PRD #1075 criterion 9; relocated into the Run-board by ADR-0080 D1)
        GET /api/runboard             -> JSON {now, recent, stale_threshold_seconds, ledger, fetched_at} — the Run-board tab's landing-view renderer (PRD #1170, slice #1172 + #1173 provenance/staleness; `next` panel retired per ADR-0080 D2)

Start: python dashboard/server.py
Config: DASH_PORT env var (default 8765)
Requires: Python 3 stdlib only — no pip install needed.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Repo root — server.py lives at <repo>/dashboard/server.py
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"

# ---------------------------------------------------------------------------
# sys.path injection keeps imports working both when:
#   (a) server.py is run as __main__ (dashboard/ is cwd or on path)
#   (b) server.py is imported by CHECK 9 (cwd is repo root)
# ---------------------------------------------------------------------------
_DASHBOARD_DIR_STR = str(Path(__file__).resolve().parent)
if _DASHBOARD_DIR_STR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR_STR)

from telemetry_root import _telemetry_log_root  # noqa: E402

# Sibling module imports (facade re-exports)
import health as _health_mod  # noqa: E402
from health import (  # noqa: E402
    check_docs1_adr_index_forward,
    check_docs2_adr_index_reverse,
    check_docs3_claude_md_agents,
    check_docs4_claude_md_skills,
    check_docs5_n3_literal,
    check_docs6_glossary_md_refs,
    check_docs7_adr_citations,
    check_docs8_supersession_notes,
    check_docs9_glossary_cap,
    check_docs10_backlog_surfacing,
    audit_subagents, audit_meta,
    serve_health as _serve_health_cached,
)
from workitems import fetch_workitems  # noqa: E402
from readme_gen import generate_readme, render_pipeline_mermaid  # noqa: E402
import tracestore as _tracestore_mod  # noqa: E402

# ---------------------------------------------------------------------------
# Server identity — captured once at import/startup time (ADR-0056/0057/0058).
# /api/meta returns {sha, started_at, stale}; stale is recomputed per-request
# by comparing the current HEAD to the sha captured at startup.
# ---------------------------------------------------------------------------
def _capture_startup_sha() -> str:
    """Return git HEAD sha at server startup; empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _current_head_sha() -> str:
    """Return current git HEAD sha; empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


_SERVER_SHA: str = _capture_startup_sha()
_SERVER_STARTED_AT: str = datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# /api/status — aggregated liveness snapshot (slice #859)
# ---------------------------------------------------------------------------

def _build_status() -> dict:
    """Build the /api/status payload synchronously.

    Returns real aggregated liveness; honest nulls allowed for fields that
    depend on unavailable data (no fixtures, no mock data).

    Fields:
      head_sha, short_sha, branch   — git HEAD at call time
      server_sha, stale             — server startup identity (mirrors /api/meta)
      hooks_live                    — {alive, newest_beacon_ts, age_minutes}
      last_event                    — {ts, age_minutes} from workflow-events.jsonl
      last_activity                 — {ts, age_minutes, source} = newer of
                                       hooks_live vs last_event, source is
                                       "hook-beacon" | "workflow-event" | None
                                       (slice #1054 — honest freshness; events
                                       log can go stale for days while beacons
                                       keep flowing on resumed sessions)
      main_green                    — {sha, lag, age_hours} from GREEN-MAIN check
      health_summary                — {pass, warn, fail} counts across all checks
      open_work                     — {prs, slices, captured, backlog} open counts
    """
    import json as _json

    # --- git HEAD ---
    head_sha = ""
    short_sha = ""
    branch = ""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=5,
        )
        head_sha = r.stdout.strip() if r.returncode == 0 else ""
        short_sha = head_sha[:7] if head_sha else ""
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=5,
        )
        branch = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        pass

    # --- server identity (mirrors /api/meta) ---
    current_sha = head_sha
    stale = bool(_SERVER_SHA and current_sha and current_sha != _SERVER_SHA)

    # --- hooks_live: newest beacon in hook-fires.jsonl ---
    fires_log = _telemetry_log_root() / ".claude" / "logs" / "hook-fires.jsonl"
    hooks_live = {"alive": False, "newest_beacon_ts": None, "age_minutes": None}
    if fires_log.exists():
        beacon_ts: float = 0.0
        newest_ts_str: str = ""
        try:
            with fires_log.open(encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = _json.loads(raw)
                    except Exception:
                        continue
                    ts_str = obj.get("ts", "")
                    if ts_str:
                        try:
                            candidate = datetime.fromisoformat(
                                ts_str.replace("Z", "+00:00")
                            ).timestamp()
                            if candidate > beacon_ts:
                                beacon_ts = candidate
                                newest_ts_str = ts_str
                        except Exception:
                            pass
        except Exception:
            pass
        if beacon_ts > 0.0:
            age_min = round((time.time() - beacon_ts) / 60.0, 1)
            alive = age_min < 60.0  # mirrors _HOOK_LIVENESS_DARK_MINUTES
            hooks_live = {
                "alive": alive,
                "newest_beacon_ts": newest_ts_str,
                "age_minutes": age_min,
            }

    # --- last_event: newest entry in workflow-events.jsonl ---
    events_log = _telemetry_log_root() / ".claude" / "logs" / "workflow-events.jsonl"
    last_event = {"ts": None, "age_minutes": None}
    if events_log.exists():
        newest_event_ts: float = 0.0
        newest_event_str: str = ""
        try:
            with events_log.open(encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = _json.loads(raw)
                    except Exception:
                        continue
                    ts_str = obj.get("ts", "")
                    if ts_str:
                        try:
                            candidate = datetime.fromisoformat(
                                ts_str.replace("Z", "+00:00")
                            ).timestamp()
                            if candidate > newest_event_ts:
                                newest_event_ts = candidate
                                newest_event_str = ts_str
                        except Exception:
                            pass
        except Exception:
            pass
        if newest_event_ts > 0.0:
            age_min = round((time.time() - newest_event_ts) / 60.0, 1)
            last_event = {"ts": newest_event_str, "age_minutes": age_min}

    # --- last_activity: honest freshness = newer of hook-beacon vs workflow-event ---
    # Rationale (slice #1054): workflow-events.jsonl can go stale for days
    # (SessionStart doesn't fire on resumed sessions) while hook-fires.jsonl
    # beacons keep flowing during active work. Consulting last_event alone
    # produces a false "no events" / "event 150h ago" reading even when the
    # system is actively running. last_activity picks whichever log has the
    # newer timestamp and labels its source so the UI can be honest about
    # which signal it's showing. Additive — last_event's shape is unchanged
    # for any other consumer relying on it.
    beacon_ts_for_activity = hooks_live.get("newest_beacon_ts")
    event_ts_for_activity = last_event.get("ts")
    last_activity = {"ts": None, "age_minutes": None, "source": None}
    _beacon_epoch = None
    _event_epoch = None
    if beacon_ts_for_activity:
        try:
            _beacon_epoch = datetime.fromisoformat(
                beacon_ts_for_activity.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            _beacon_epoch = None
    if event_ts_for_activity:
        try:
            _event_epoch = datetime.fromisoformat(
                event_ts_for_activity.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            _event_epoch = None
    if _beacon_epoch is not None and (_event_epoch is None or _beacon_epoch >= _event_epoch):
        last_activity = {
            "ts": beacon_ts_for_activity,
            "age_minutes": round((time.time() - _beacon_epoch) / 60.0, 1),
            "source": "hook-beacon",
        }
    elif _event_epoch is not None:
        last_activity = {
            "ts": event_ts_for_activity,
            "age_minutes": round((time.time() - _event_epoch) / 60.0, 1),
            "source": "workflow-event",
        }

    # --- main_green: reuse GREEN-MAIN check from health.py ---
    from health import check_green_main as _check_green_main
    gm = _check_green_main()
    main_green = {
        "sha": gm.get("sha", None),
        "lag": gm.get("lag", None),
        "age_hours": gm.get("age_hours", None),
    }

    # --- health_summary: count PASS/WARN/FAIL from cached health payload ---
    # Use serve_health() which is TTL-cached; avoids redundant computation.
    # Guard: when health is still computing the sentinel is {"status": "computing"}.
    # In that case return nulls rather than silent 0/0/0 (which would imply 0 checks
    # ran — dishonest).  Nulls signal "data not yet available" to the consumer.
    health_data, _ = _serve_health_cached()
    if health_data.get("status") == "computing":
        health_summary = {"pass": None, "warn": None, "fail": None}
    else:
        pass_count = 0
        warn_count = 0
        fail_count = 0
        # Walk each top-level group that is a dict with a 'checks' list.
        # Skip groups without a checks list (auditSubagents).
        # auditSubagents values are per-agent dicts — not counted here.
        for group_key, group_val in health_data.items():
            if not isinstance(group_val, dict):
                continue
            checks_list = group_val.get("checks")
            if not isinstance(checks_list, list):
                continue
            for chk in checks_list:
                r = chk.get("result", "")
                if r == "PASS":
                    pass_count += 1
                elif r == "WARN":
                    warn_count += 1
                elif r == "FAIL":
                    fail_count += 1
        health_summary = {"pass": pass_count, "warn": warn_count, "fail": fail_count}

    # --- open_work: counts from fetch_workitems() (gh_cache-backed, 30s outer TTL) ---
    # fetch_workitems() routes gh calls through gh_cache (slice #995).
    # We run it in a background thread with a 1.5s budget so a cold-cache + slow gh
    # degrades only this field (computing sentinel) without blocking the whole response.
    # Normal path (warm outer cache): fetch_workitems() returns in <1ms; thread joins
    # immediately.  Cold path with slow gh: thread times out → computing sentinel.
    _wi_result: list = []  # mutable container for thread result

    def _fetch_wi_bg():
        try:
            _wi_result.append(fetch_workitems())
        except Exception:
            pass

    _WI_BUDGET = 1.5  # seconds; generous enough for cache hits, tight enough for budget
    _wi_thread = threading.Thread(target=_fetch_wi_bg, daemon=True)
    _wi_thread.start()
    _wi_thread.join(timeout=_WI_BUDGET)

    if _wi_result:
        wi = _wi_result[0]
    else:
        # Timed out — return a degraded open_work sentinel
        wi = {}

    # Count only OPEN items for prs/slices/captured/backlog
    open_prs = sum(1 for p in wi.get("prs", []) if p.get("state", "").upper() == "OPEN")
    open_slices = sum(
        1 for s in wi.get("slices", []) if s.get("state", "").upper() == "OPEN"
    )
    open_captured = len(wi.get("captures", []))  # already filtered --state open
    open_backlog = len(wi.get("backlog", []))    # already filtered --state open
    open_work = {
        "prs": open_prs,
        "slices": open_slices,
        "captured": open_captured,
        "backlog": open_backlog,
        # Honest freshness metadata (cr.8 — alongside existing fields, not replacing)
        "fetched_at": wi.get("_fetched_at"),
        "source": wi.get("_source") if wi else "computing",
    }

    return {
        "head_sha": head_sha,
        "short_sha": short_sha,
        "branch": branch,
        "server_sha": _SERVER_SHA,
        "stale": stale,
        "hooks_live": hooks_live,
        "last_event": last_event,
        "last_activity": last_activity,
        "main_green": main_green,
        "health_summary": health_summary,
        "open_work": open_work,
    }


# ---------------------------------------------------------------------------
# Known critics (explicit allow-list per implementer note 1).
# 6 critics per ADR-0046 D1 (parsimony principle; roster cut 7 -> 6 by ADR-0081 D2).
# CHECK 7 regexes server.py SOURCE for this literal — it must stay here.
# ---------------------------------------------------------------------------
KNOWN_CRITICS = {
    "reviewer",
    "prd-critic",
    "adr-critic",
    "slicer-critic",
    "backlog-critic",
    "codebase-critic",
}

# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # quiet by default; override for debug
        pass

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, body: bytes, content_type: str = "text/html; charset=utf-8",
                   status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str):
        self._send_json({"error": message}, status)

    # Reader-side fixture-pattern guard — mirrors the writer's FIXTURE_PATTERN in
    # log-tool-event.sh so the server defensively drops synthetic sids even if the
    # writer's routing was bypassed (e.g. direct file writes during testing).
    _FIXTURE_SID_RE = re.compile(
        r"^(demo|test|verify|fixture|manual|sess-|sample-session-id$)", re.IGNORECASE
    )

    @classmethod
    def _is_valid_v2_event(cls, obj: dict) -> bool:
        """Return True iff obj is a schema-v2 event with a non-empty, non-fixture session_id."""
        if obj.get("v") != 2:
            return False
        sid = obj.get("session_id", "")
        if not sid:
            return False
        if cls._FIXTURE_SID_RE.match(sid):
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            index = DASHBOARD_DIR / "index.html"
            if index.exists():
                self._send_text(index.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send_error(404, "index.html not found")

        elif path == "/api/health":
            # TTL-cached; second consecutive call returns <200 ms.
            data, _ = _serve_health_cached()
            self._send_json(data)

        elif path == "/api/status":
            # GET /api/status — aggregated liveness snapshot (slice #859).
            # Returns real-time aggregated state: git identity, hooks liveness,
            # last event age, main_green health, health summary counts, open-work
            # counts. Reuses health.py TTL cache + workitems 30s cache.
            # DEFENSIVE: try/except so a partial failure returns best-effort data.
            try:
                self._send_json(_build_status())
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)

        elif path == "/api/meta":
            # GET /api/meta — server identity: {sha, started_at, stale}
            # sha: HEAD sha captured at startup; stale: HEAD has moved since startup.
            # stale is recomputed per-request (cheap git rev-parse).
            current_sha = _current_head_sha()
            stale = bool(_SERVER_SHA and current_sha and current_sha != _SERVER_SHA)
            self._send_json({
                "sha": _SERVER_SHA,
                "started_at": _SERVER_STARTED_AT,
                "stale": stale,
            })

        elif path == "/api/trace-runs":
            # GET /api/trace-runs[?limit=N] — recorded pipeline-span chains
            # from the v3 trace store: the Run-board's recorded-runs panel
            # (slice #1082, PRD #1075 criterion 9; relocated from the
            # deleted Firing tab into the Run-board by ADR-0080 D1).
            # Non-blocking background-warm serve — zero gh calls, sqlite+JSONL only.
            limit_raw = (query.get("limit") or ["20"])[0]
            try:
                limit = int(limit_raw) if str(limit_raw).isdigit() else 20
            except (ValueError, AttributeError):
                limit = 20
            limit = max(1, min(limit, 100))
            try:
                self._send_json(_tracestore_mod.serve_trace_runs(limit=limit))
            except Exception as exc:
                self._send_json({"error": str(exc), "runs": [], "run_count": 0}, 500)

        elif path == "/api/runboard":
            # GET /api/runboard — now/next/recent from the recorded v3
            # ledger: the Run-board tab's landing-view renderer (PRD #1170,
            # slice #1172 + slice #1173's staleness flag + row-level
            # provenance). Non-blocking background-warm serve, mirrors
            # /api/trace-runs's house pattern — zero gh calls, sqlite+JSONL
            # only, strictly a reader (ADR-0078 D1).
            try:
                self._send_json(_tracestore_mod.serve_runboard())
            except Exception as exc:
                self._send_json(
                    {"error": str(exc), "now": [], "recent": [],
                     "stale_threshold_seconds": _tracestore_mod.RUNBOARD_STALE_THRESHOLD_SECONDS,
                     "ledger": _tracestore_mod._LEDGER_DISPLAY_NAME}, 500
                )

        else:
            self._send_error(404, f"Not found: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _should_open_browser(env):
    """Pure decision: should main() attempt to open a browser on startup?

    Opt-in via DASH_OPEN_BROWSER (any non-empty value enables it) — no
    automated spawner leaks a browser tab by omission (PRD #1432).
    """
    return bool(env.get("DASH_OPEN_BROWSER"))


def main():
    port = int(os.environ.get("DASH_PORT", "8765"))
    server = ThreadingHTTPServer(("localhost", port), DashboardHandler)
    server.daemon_threads = True
    print(f"Dashboard running at http://localhost:{port}", flush=True)
    print(f"Repo root: {REPO_ROOT}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    if _should_open_browser(os.environ):
        try:
            webbrowser.open(f"http://localhost:{port}")
        except Exception as e:
            print(f"(could not open browser: {e})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.", flush=True)
        server.server_close()


if __name__ == "__main__":
    if "--generate-readme" in sys.argv:
        generate_readme()
    else:
        main()
