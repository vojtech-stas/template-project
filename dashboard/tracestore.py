#!/usr/bin/env python3
"""
dashboard/tracestore.py — disposable SQLite derived read-model for the
canonical v3 trace-span log (tools/trace.py).

Slice #1080 (PRD #1075 criteria 2a/2b): folds trace-v3.jsonl into an indexed
SQLite read-model at `.claude/state/trace.db` (gitignored, disposable) and
answers the acid-path causal-chain query from an indexed lookup instead of
tools/trace.py's linear scan. Per ADR-0075 D2, the db is explicitly
REFOLDABLE-FROM-LOG, NEVER a second source of truth: all writes happen via
`fold()` only — no ad hoc INSERT anywhere else in this module or its callers
(SPIDR-R guard, see PRD #1075 §6 rabbit-hole). Any divergence from
tools/trace.py's linear-scan answer is a fold bug, fixed in `fold()`, never
patched by writing to the db directly.

Canonical JSONL log path: reused verbatim from tools/trace.py's
`trace_log_path()` (git-common-dir-parent resolution, TRACE_LOG_OVERRIDE env
seam) — no independent git path-resolution logic lives here (DRY, and it
avoids re-introducing the #1091 MSYS_NO_PATHCONV `git -C <pwd-derived>`
trap: this module never calls `git -C` itself; it delegates entirely to
tools/trace.py's already-safe plain `git rev-parse` resolution, which relies
on the process's own cwd rather than a path argument).

DB path: `<git-common-dir-parent>/.claude/state/trace.db` (sibling of
`.claude/logs/`), gitignored; `TRACE_DB_OVERRIDE` env var is the test seam
(mirrors TRACE_LOG_OVERRIDE).

Refold semantics: `fold()` builds the next generation of `spans` into a
scratch table (`spans_new`), fully populated from a full re-read of the
JSONL (via `trace.read_spans()` — the IDENTICAL parser tools/trace.py's own
linear scan uses, guaranteeing parity), then atomically swaps it in under
ONE explicit transaction (DROP old `spans` + `ALTER TABLE spans_new RENAME
TO spans`, both inside the same BEGIN/COMMIT). There is no row-level
incremental/delta fold anywhere in this module — every actual rebuild is a
full regenerate-and-swap (disposability requirement: delete db, refold,
identical answer; `force=True` always rebuilds). The one optimization is a
freshness short-circuit: `fold(force=False)` (the default used by every
query entrypoint below) SKIPS the rebuild entirely when the JSONL's
size+mtime fingerprint has not changed since the fingerprint recorded in
the db's `_meta` table from the last fold.

Concurrency hardening (slice #1082 step 1, issue #1101 — reproduced against
PR #1095's tracestore before this slice made it live):
  1. **Commit-gap fix:** the old `_create_schema()` used `executescript()`
     with `DROP TABLE`+`CREATE TABLE`, which auto-commits before the insert
     batch runs — a concurrent reader querying `spans` in that window sees
     COUNT(*)=0 (spurious "no recorded trace"). Fixed by building fully
     into `spans_new` and swapping it in atomically: the pre-existing
     `spans` table (and every other connection's view of it) is untouched
     until the final DROP+RENAME commits, so a concurrent reader sees
     either the full previous generation or the full new one, never a
     transiently-empty/missing table.
  2. **WAL + busy_timeout:** every connection enables `journal_mode=WAL`
     and a `busy_timeout` so two concurrent folds serialize on the write
     lock (waiting up to the timeout) instead of raising "database is
     locked".
  3. **INSERT OR IGNORE on span_id:** a duplicate span_id (e.g. a retried
     wrapper call) is silently deduplicated instead of raising
     `IntegrityError` and breaking every subsequent query against that
     fold.
  4. **`ORDER BY ts, rowid` tie-break:** same-timestamp spans sort by
     insertion order (rowid), matching `tools/trace.py`'s stable
     linear-scan sort exactly (both preserve JSONL line order for ties).
  5. **size+mtime composite freshness key:** a pure-mtime freshness check
     misses same-second rewrites on low-resolution filesystems (the
     "stale-tick" edge); the fingerprint is now `f"{mtime}:{size}"`.

Python API (for dashboard reuse):
  db_path(override=None) -> str
  fold(log_path=None, db_path_=None, force=False) -> int  (spans folded)
  acid_path(pr_number, log_path=None, db_path_=None) -> list[dict] | None
  span_tree(trace_id, log_path=None, db_path_=None) -> list[dict]
  running_dispatches(log_path=None, db_path_=None) -> list[dict]
    "Running now" query (PRD #1127 §2 criterion 2b / slice #1129): every
    trace_id whose chronologically LAST dispatch/dispatch_end event is a
    `dispatch` (per-trace_id event-order semantics, not trace_id-membership
    — see the function's own docstring for the round-1 fix rationale) —
    the duplicate-dispatch mutex's read side and the (since-retired,
    ADR-0088 D1) run-board's data model, both for free.
  serve_trace_runs(limit=30, log_path=None, db_path_=None) -> dict
    Background-warmed payload builder (slice #1082, PRD #1075 criterion 9
    — formerly the Firing tab's PRIMARY renderer) — mirrors
    prd_firing.py's serve_prd_firing() house pattern (issue #962):
    stale-while-revalidate cache + daemon thread. Kept as a library shim
    with no HTTP caller after ADR-0088 D1's server deletion (captured:
    #1491); the blocking builder (`_build_recorded_runs`) stays reachable
    only via this function, never called directly.
  serve_runboard(log_path=None, db_path_=None) -> dict
    Background-warmed payload builder (PRD #1170, slice #1172 walking
    skeleton + slice #1173 provenance/staleness) — mirrors
    serve_trace_runs()'s stale-while-revalidate pattern. Returns {now,
    recent, stale_threshold_seconds, ledger, fetched_at}: `now` =
    running_dispatches() enriched with elapsed seconds, a `stale` bool
    (elapsed >= stale_threshold_seconds) and a `source` label; `recent`
    = at most 20 newest-first terminated chains (dispatch_end /
    pr_merged) with outcome, duration and a `source` label.
    `stale_threshold_seconds` and `ledger` are echoed so the caller never
    hardcodes either (row-level provenance — a pattern carried over from
    the retired run-board, ADR-0078 D1 history, superseded by ADR-0088).
    Strict reader — never infers state the ledger does not hold. Kept as
    a library shim with no HTTP caller after ADR-0088 D1's server
    deletion (captured: #1491). The `next` panel (the retired ready-set
    marker) is retired per ADR-0080 D2 — the payload reflects only what
    is recorded, not what is promised.

CLI (parity with `tools/trace.py path --pr <n>` — trace.py's linear scan
remains the fallback/cross-check per the slice's instruction):
  python dashboard/tracestore.py fold
  python dashboard/tracestore.py path --pr <n>
  python dashboard/tracestore.py running
  python dashboard/tracestore.py runboard

utf-8: the JSONL is always read via trace.read_spans() (utf-8), and SQLite
TEXT columns store native Python str (unicode) without any extra encoding
step.
"""
import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
_TRACE_PY = os.path.join(_REPO_ROOT, "tools", "trace.py")


def _load_trace():
    # Loaded under a distinct module name — never "trace" (stdlib collision,
    # same defensive pattern as tools/pipe/pr-open's _load_trace()).
    spec = importlib.util.spec_from_file_location("trace_v3", _TRACE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def db_path(override=None):
    """Resolve the canonical trace.db path. TRACE_DB_OVERRIDE env var is the
    test seam (mirrors tools/trace.py's TRACE_LOG_OVERRIDE)."""
    if override:
        return override
    env = os.environ.get("TRACE_DB_OVERRIDE")
    if env:
        return env
    trace = _load_trace()
    log_path = trace.trace_log_path()
    # log_path == <root>/.claude/logs/trace-v3.jsonl -> <root>/.claude/state/trace.db
    claude_dir = os.path.dirname(os.path.dirname(log_path))
    return os.path.join(claude_dir, "state", "trace.db")


def _retry_locked(fn, attempts=20, base_delay=0.05):
    """Retry `fn` on sqlite3.OperationalError('database is locked') with
    linear backoff — a belt-and-suspenders layer alongside busy_timeout
    (#1101 prereq 2). The very first WAL-mode-enable on a brand-new db file
    (or a contended `BEGIN IMMEDIATE`) can raise immediately on some
    platforms (Windows' stricter mandatory file locking) rather than
    honoring the busy handler's own retry loop — this closes that gap."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            last_exc = e
            time.sleep(base_delay * (attempt + 1))
    raise last_exc


def _connect(path):
    """Open a trace.db connection with WAL mode + busy_timeout (#1101 prereq
    2): two concurrent folds serialize on the write lock (waiting up to the
    timeout) instead of raising "database is locked". `isolation_level=None`
    (autocommit) gives `fold()` full manual control over an explicit
    BEGIN/COMMIT spanning both DDL and DML — required for the atomic
    schema-swap below (Python's legacy implicit-transaction wrapper cannot
    safely mix DDL into one transaction with preceding DML)."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _retry_locked(lambda: conn.execute("PRAGMA busy_timeout=5000"))
    _retry_locked(lambda: conn.execute("PRAGMA journal_mode=WAL"))
    return conn


def _create_schema(conn, table_name="spans"):
    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    conn.execute(
        f"""
        CREATE TABLE {table_name} (
            span_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            kind TEXT,
            name TEXT,
            parent_span_id TEXT,
            pr TEXT,
            dur_ms INTEGER,
            attrs_json TEXT NOT NULL
        )
        """
    )


def _log_fingerprint(path):
    """size+mtime composite freshness key (#1101 prereq 5) — closes the
    stale-tick edge where a pure-mtime check misses a same-second rewrite
    on low-resolution filesystems. Empty string when the log is absent."""
    if not os.path.exists(path):
        return ""
    st = os.stat(path)
    return f"{st.st_mtime}:{st.st_size}"


def fold(log_path=None, db_path_=None, force=False):
    """(Re)build the trace.db read-model from the canonical JSONL log.

    See module docstring "Refold semantics" + "Concurrency hardening" for
    the full contract. Returns the number of spans currently represented in
    the db (either freshly folded, or the pre-existing count when a
    same-fingerprint fold is skipped).
    """
    trace = _load_trace()
    resolved_log = log_path or trace.trace_log_path()
    resolved_db = db_path_ or db_path()

    fingerprint = _log_fingerprint(resolved_log)

    conn = _connect(resolved_db)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        if not force:
            row = conn.execute(
                "SELECT value FROM _meta WHERE key = 'log_fingerprint'"
            ).fetchone()
            if row is not None and fingerprint and row["value"] == fingerprint:
                try:
                    count_row = conn.execute(
                        "SELECT COUNT(*) AS c FROM spans"
                    ).fetchone()
                    if count_row is not None:
                        return count_row["c"]
                except sqlite3.OperationalError:
                    pass  # spans table missing/corrupt -> fall through, rebuild

        spans = trace.read_spans(resolved_log)

        # Build the full next generation into a scratch table, then swap it
        # in atomically under ONE explicit transaction (#1101 prereq 1): the
        # live `spans` table is untouched until DROP+RENAME commits, so a
        # concurrent reader never observes a transiently-empty/missing table.
        _retry_locked(lambda: conn.execute("BEGIN IMMEDIATE"))
        try:
            conn.execute("DROP TABLE IF EXISTS spans_new")
            _create_schema(conn, table_name="spans_new")
            for s in spans:
                attrs = s.get("attrs", {}) or {}
                pr_val = attrs.get("pr")
                # INSERT OR IGNORE (#1101 prereq 3): a duplicate span_id is
                # silently deduplicated rather than raising IntegrityError
                # and breaking every subsequent query against this fold.
                conn.execute(
                    "INSERT OR IGNORE INTO spans_new (span_id, trace_id, ts, "
                    "kind, name, parent_span_id, pr, dur_ms, attrs_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        s.get("span_id"),
                        s.get("trace_id"),
                        s.get("ts"),
                        s.get("kind"),
                        s.get("name"),
                        s.get("parent_span_id"),
                        str(pr_val) if pr_val is not None else None,
                        s.get("dur_ms"),
                        json.dumps(attrs, sort_keys=True),
                    ),
                )
            conn.execute("DROP TABLE IF EXISTS spans")
            conn.execute("ALTER TABLE spans_new RENAME TO spans")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_pr ON spans(pr)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO _meta (key, value) VALUES "
                "('log_fingerprint', ?)",
                (fingerprint,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return len(spans)
    finally:
        conn.close()


def _row_to_span(row):
    """Reconstruct the exact v3 span shape emit_span() writes: optional
    fields (parent_span_id, name, dur_ms) are OMITTED when falsy/absent,
    never written as null — mirrors tools/trace.py's emit_span()."""
    span = {
        "v": 3,
        "ts": row["ts"],
        "trace_id": row["trace_id"],
        "span_id": row["span_id"],
        "kind": row["kind"],
        "attrs": json.loads(row["attrs_json"]) if row["attrs_json"] else {},
    }
    if row["parent_span_id"]:
        span["parent_span_id"] = row["parent_span_id"]
    if row["name"]:
        span["name"] = row["name"]
    if row["dur_ms"] is not None:
        span["dur_ms"] = row["dur_ms"]
    return span


def acid_path(pr_number, log_path=None, db_path_=None):
    """Indexed acid-path causal-chain query — same semantics as
    tools/trace.py's acid_path (the linear-scan cross-check): every span
    whose attrs.pr == pr_number, PLUS every span sharing a trace_id with one
    of those matches, ts-ordered (with a `rowid` tie-break — #1101 prereq 4
    — matching trace.py's stable-sort insertion-order tie-break exactly).
    Returns None when the PR has no recorded spans at all (pre-v3 /
    untraced — the loud-fail contract preserved)."""
    resolved_db = db_path_ or db_path()
    fold(log_path=log_path, db_path_=resolved_db, force=False)
    conn = _connect(resolved_db)
    try:
        pr_str = str(pr_number)
        matched = conn.execute(
            "SELECT DISTINCT trace_id FROM spans WHERE pr = ?", (pr_str,)
        ).fetchall()
        if not matched:
            return None
        trace_ids = [r["trace_id"] for r in matched]
        placeholders = ",".join("?" for _ in trace_ids)
        rows = conn.execute(
            f"SELECT * FROM spans WHERE trace_id IN ({placeholders}) "
            "ORDER BY ts, rowid",
            trace_ids,
        ).fetchall()
        return [_row_to_span(r) for r in rows]
    finally:
        conn.close()


def running_dispatches(log_path=None, db_path_=None):
    """"Running now" query (PRD #1127 §2 criterion 2b / slice #1129): every
    trace_id whose chronologically LAST dispatch/dispatch_end event (ordered
    by `ts`, then `rowid` as the tiebreak — the same ordering `acid_path`/
    `span_tree` already use) is a `dispatch` — i.e. every dispatch still in
    flight, once per trace_id. Generic over trace_id (not slice-specific
    parsing): mirrors `tools/pipe/dispatch`'s own trace_id convention
    (`slice-<n>`) without hardcoding it here.

    Per-trace_id EVENT-ORDER semantics, NOT a trace_id-membership check
    (reviewer round-1 BLOCK on PR #1137, rule #19 fix — the SAME model as
    `tools/pipe/dispatch`'s `_running_dispatch_exists` mutex): "exclude any
    trace_id that ever has a dispatch_end" is permanently wrong because
    trace_id is constant per slice — a slice's first-ever `dispatch_end`
    would hide EVERY later round's dispatch, including a genuinely
    unterminated one. Taking each trace_id's chronological LAST event
    instead correctly surfaces round 2+ as running and is immune to a
    stray leading `dispatch_end` with no prior `dispatch`."""
    resolved_db = db_path_ or db_path()
    fold(log_path=log_path, db_path_=resolved_db, force=False)
    conn = _connect(resolved_db)
    try:
        rows = conn.execute(
            "SELECT * FROM spans WHERE kind IN ('dispatch', 'dispatch_end') "
            "ORDER BY ts, rowid"
        ).fetchall()
    finally:
        conn.close()
    last_by_trace = {}
    for r in rows:
        last_by_trace[r["trace_id"]] = r  # ascending scan -> last write wins
    running = [
        _row_to_span(r) for r in last_by_trace.values() if r["kind"] == "dispatch"
    ]
    running.sort(key=lambda s: s.get("ts", ""))
    return running


def span_tree(trace_id, log_path=None, db_path_=None):
    """Span-tree API: every span for one trace_id, ts-ordered with a
    `rowid` tie-break (#1101 prereq 4). Returns [] when the trace_id is
    unknown (a caller probing existence gets an empty list — distinct from
    acid_path's loud-fail None for an untraced PR)."""
    resolved_db = db_path_ or db_path()
    fold(log_path=log_path, db_path_=resolved_db, force=False)
    conn = _connect(resolved_db)
    try:
        rows = conn.execute(
            "SELECT * FROM spans WHERE trace_id = ? ORDER BY ts, rowid",
            (trace_id,),
        ).fetchall()
        return [_row_to_span(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# "Recorded runs" query + background-warm serve (slice #1082, PRD #1075
# criterion 9 — formerly the Firing tab's PRIMARY renderer; now a library
# shim with no HTTP caller after ADR-0088 D1's server deletion, kept for
# its surviving test consumers). Mirrors prd_firing.py's serve_prd_firing()
# house pattern (issue #962): stale-while-revalidate cache + daemon
# background thread; callers must call serve_trace_runs() only — never
# the blocking builder directly.
# ---------------------------------------------------------------------------
_runs_cache: dict = {}
_runs_cache_lock = threading.Lock()
_RUNS_CACHE_TTL = 30  # seconds — sqlite read-model is cheap; short TTL is fine
_runs_computing = False


def _build_recorded_runs(limit=30, log_path=None, db_path_=None):
    """Blocking builder: group every folded span by trace_id into ordered
    PR-shaped chains (pr_opened -> ... -> pr_merged), newest-opened-first,
    capped at `limit`. Pure sqlite+JSONL read (no gh calls — v3 spans ARE
    the source of truth). Exposed as its own function so tests can call it
    directly without going through the background thread."""
    resolved_db = db_path_ or db_path()
    fold(log_path=log_path, db_path_=resolved_db, force=False)
    conn = _connect(resolved_db)
    try:
        trace_rows = conn.execute(
            "SELECT DISTINCT trace_id FROM spans WHERE pr IS NOT NULL "
            "ORDER BY trace_id"
        ).fetchall()
        trace_ids = [r["trace_id"] for r in trace_rows]
    finally:
        conn.close()

    runs = []
    for tid in trace_ids:
        spans = span_tree(tid, log_path=log_path, db_path_=resolved_db)
        if not spans:
            continue
        pr_num, opened_ts, merged_ts, dur_ms = None, None, None, None
        for s in spans:
            attrs = s.get("attrs", {}) or {}
            if attrs.get("pr"):
                pr_num = attrs.get("pr")
            if s.get("kind") == "pr_opened":
                opened_ts = s.get("ts")
            if s.get("kind") == "pr_merged":
                merged_ts = s.get("ts")
                dur_ms = s.get("dur_ms")
        runs.append({
            "trace_id": tid,
            "pr": pr_num,
            "opened_ts": opened_ts,
            "merged_ts": merged_ts,
            "dur_ms": dur_ms,
            "spans": spans,
        })

    runs.sort(key=lambda r: r.get("opened_ts") or "", reverse=True)
    return runs[:limit]


def _trace_runs_background(limit, log_path, db_path_):
    global _runs_computing
    try:
        runs = _build_recorded_runs(limit=limit, log_path=log_path, db_path_=db_path_)
        payload = {
            "runs": runs,
            "run_count": len(runs),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        payload = {
            "runs": [],
            "run_count": 0,
            "error": str(exc),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    with _runs_cache_lock:
        _runs_cache[limit] = {"data": payload, "ts": time.time()}
        _runs_computing = False


def serve_trace_runs(limit=30, log_path=None, db_path_=None):
    """Stale-while-revalidate serve path (no HTTP caller after ADR-0088 D1's
    server deletion; kept as a library shim, captured: #1491). ALWAYS returns
    immediately: cached data if warm, {"status":"computing"} on true cold
    start (kicks a daemon background thread), or last-known data with
    "refreshing":true while a stale-TTL recompute runs in the background."""
    global _runs_computing
    with _runs_cache_lock:
        cached_entry = _runs_cache.get(limit)
        now = time.time()

        if cached_entry is not None:
            expired = (now - cached_entry.get("ts", 0)) >= _RUNS_CACHE_TTL
            if not expired:
                return cached_entry["data"]
            payload = dict(cached_entry["data"])
            payload["refreshing"] = True
            if not _runs_computing:
                _runs_computing = True
                threading.Thread(
                    target=_trace_runs_background,
                    args=(limit, log_path, db_path_),
                    daemon=True,
                ).start()
            return payload

        if _runs_computing:
            return {"status": "computing"}
        _runs_computing = True

    threading.Thread(
        target=_trace_runs_background, args=(limit, log_path, db_path_), daemon=True
    ).start()
    return {"status": "computing"}


# ---------------------------------------------------------------------------
# Run-board query (PRD #1170 walking skeleton, slice #1172): now/recent from
# the recorded ledger — strictly a reader, a pattern carried over from the
# retired run-board (ADR-0078 D1 history, superseded by ADR-0088). `now` reuses
# running_dispatches() (also the duplicate-dispatch mutex's read side);
# `recent` is a new query this slice authors. The `next` query (the retired
# checkpoint verb's ready-set) is retired per ADR-0080 D2 (slice #1219).
#
# Staleness threshold + provenance (slice #1173, §2 criteria 1d/2c/2d):
# RUNBOARD_STALE_THRESHOLD_SECONDS is evidence-based, not guessed. Querying
# the canonical ledger's 10 recorded dispatch/dispatch_end pairs (as of
# 2026-08-04) gave a MEDIAN duration of ~3472s (~58min; range 1277s-12866s
# across 4 working sessions). 5400s (90min) sits meaningfully above that
# median while still catching a genuinely stuck dispatch well before the
# historical long tail. Echoed in the API response
# (`stale_threshold_seconds`) and rendered in the UI panel header, per
# the retired run-board's provenance intent (ADR-0078 D1 history,
# superseded by ADR-0088) — a threshold that only lives in code is
# not visible. `_LEDGER_DISPLAY_NAME` is the human-facing relative path
# named in the honest empty state (§2 criterion 2d); the resolved absolute
# path is `tools.trace.trace_log_path()`, unchanged here.
# ---------------------------------------------------------------------------
RUNBOARD_STALE_THRESHOLD_SECONDS = 5400
_LEDGER_DISPLAY_NAME = ".claude/logs/trace-v3.jsonl"


def _parse_ts(ts):
    """Parse a v3 span's UTC ISO ts (`_now_iso()`'s exact format) into an
    aware datetime. Returns None on any malformed/absent input — callers
    treat that as "duration unknown" rather than raising."""
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _now_entries(log_path=None, db_path_=None):
    """`now` array (§2 criteria 1a/1d): running_dispatches() enriched with
    elapsed_seconds (now - dispatch ts), a `stale` bool once elapsed
    exceeds RUNBOARD_STALE_THRESHOLD_SECONDS, a `source` provenance label,
    and slice/prd/session_id lifted out of attrs for a flat, board-
    friendly shape. `elapsed is None` (malformed/absent ts) never flags
    stale — unknown duration is not evidence of staleness."""
    running = running_dispatches(log_path=log_path, db_path_=db_path_)
    now_utc = datetime.now(timezone.utc)
    entries = []
    for s in running:
        attrs = s.get("attrs", {}) or {}
        started = _parse_ts(s.get("ts"))
        elapsed = int((now_utc - started).total_seconds()) if started else None
        stale = elapsed is not None and elapsed >= RUNBOARD_STALE_THRESHOLD_SECONDS
        entries.append({
            "trace_id": s.get("trace_id"),
            "slice": attrs.get("slice"),
            "prd": attrs.get("prd"),
            "session_id": attrs.get("session_id"),
            "ts": s.get("ts"),
            "elapsed_seconds": elapsed,
            "stale": stale,
            "source": "dispatch span (open, no dispatch_end)",
        })
    return entries


def _recent_terminations(limit=20, log_path=None, db_path_=None):
    """`recent` array (§2 criteria 1c/2c): at most `limit` newest-first
    terminated chains — a `dispatch` paired with its matching `dispatch_end`
    (outcome=attrs.result, duration=dispatch_end.ts - dispatch.ts, source=
    "dispatch_end span"), or a `pr_merged` span (outcome="merged",
    duration=its own recorded dur_ms, source="pr_merged span"). Single
    ascending pass over ts,rowid order (matching every other query's
    tie-break); terminations are appended in the SAME order their terminal
    row is visited, so reversing the finished list yields newest-first
    without a second sort."""
    resolved_db = db_path_ or db_path()
    fold(log_path=log_path, db_path_=resolved_db, force=False)
    conn = _connect(resolved_db)
    try:
        rows = conn.execute(
            "SELECT * FROM spans WHERE kind IN ('dispatch', 'dispatch_end', 'pr_merged') "
            "ORDER BY ts, rowid"
        ).fetchall()
    finally:
        conn.close()

    active_start = {}  # trace_id -> most recent open dispatch's ts
    terminations = []
    for r in rows:
        attrs = json.loads(r["attrs_json"]) if r["attrs_json"] else {}
        if r["kind"] == "dispatch":
            active_start[r["trace_id"]] = r["ts"]
        elif r["kind"] == "dispatch_end":
            start_ts = active_start.pop(r["trace_id"], None)
            start_dt, end_dt = _parse_ts(start_ts), _parse_ts(r["ts"])
            dur_ms = int((end_dt - start_dt).total_seconds() * 1000) if start_dt and end_dt else None
            terminations.append({
                "trace_id": r["trace_id"], "kind": "dispatch_end",
                "slice": attrs.get("slice"), "outcome": attrs.get("result"),
                "ts": r["ts"], "dur_ms": dur_ms,
                "source": "dispatch_end span",
            })
        elif r["kind"] == "pr_merged":
            terminations.append({
                "trace_id": r["trace_id"], "kind": "pr_merged",
                "pr": attrs.get("pr"), "outcome": "merged",
                "ts": r["ts"], "dur_ms": r["dur_ms"],
                "source": "pr_merged span",
            })
    terminations.reverse()
    return terminations[:limit]


def _build_runboard(log_path=None, db_path_=None, recent_limit=20):
    """Blocking builder — pure sqlite+JSONL read, no gh calls. Exposed
    directly so tests can call it without going through the background
    thread (mirrors _build_recorded_runs's own house pattern). Echoes
    `stale_threshold_seconds` and `ledger` (slice #1173, §2 criteria
    1d/2d) so the UI reads both from the response rather than hardcoding
    either."""
    resolved_db = db_path_ or db_path()
    fold(log_path=log_path, db_path_=resolved_db, force=False)
    now = _now_entries(log_path=log_path, db_path_=resolved_db)
    recent = _recent_terminations(limit=recent_limit, log_path=log_path, db_path_=resolved_db)
    return {
        "now": now,
        "recent": recent,
        "stale_threshold_seconds": RUNBOARD_STALE_THRESHOLD_SECONDS,
        "ledger": _LEDGER_DISPLAY_NAME,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


_runboard_cache: dict = {}
_runboard_cache_lock = threading.Lock()
_RUNBOARD_CACHE_TTL = 15  # seconds — shorter than trace-runs: "now" wants fresher reads
_runboard_computing = False


def _runboard_background(log_path, db_path_):
    global _runboard_computing
    try:
        payload = _build_runboard(log_path=log_path, db_path_=db_path_)
    except Exception as exc:
        payload = {
            "now": [], "recent": [],
            "stale_threshold_seconds": RUNBOARD_STALE_THRESHOLD_SECONDS,
            "ledger": _LEDGER_DISPLAY_NAME,
            "error": str(exc), "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    with _runboard_cache_lock:
        _runboard_cache["default"] = {"data": payload, "ts": time.time()}
        _runboard_computing = False


def serve_runboard(log_path=None, db_path_=None):
    """Stale-while-revalidate serve path (no HTTP caller after ADR-0088 D1's
    server deletion; kept as a library shim, captured: #1491) — mirrors
    serve_trace_runs()'s contract exactly (cached / computing / stale+
    refreshing-in-background), applied to a single unkeyed cache slot since
    the run-board takes no limit parameter."""
    global _runboard_computing
    with _runboard_cache_lock:
        cached_entry = _runboard_cache.get("default")
        now_ts = time.time()

        if cached_entry is not None:
            expired = (now_ts - cached_entry.get("ts", 0)) >= _RUNBOARD_CACHE_TTL
            if not expired:
                return cached_entry["data"]
            payload = dict(cached_entry["data"])
            payload["refreshing"] = True
            if not _runboard_computing:
                _runboard_computing = True
                threading.Thread(
                    target=_runboard_background, args=(log_path, db_path_), daemon=True
                ).start()
            return payload

        if _runboard_computing:
            return {"status": "computing"}
        _runboard_computing = True

    threading.Thread(
        target=_runboard_background, args=(log_path, db_path_), daemon=True
    ).start()
    return {"status": "computing"}


def _cmd_fold(args):
    count = fold(force=True)
    print(f"folded {count} spans into {db_path()}")
    return 0


def _cmd_running(args):
    running = running_dispatches()
    if not running:
        print("no running dispatches")
        return 0
    for s in running:
        attrs = s.get("attrs", {})
        print(
            f"{s.get('ts')} trace_id={s.get('trace_id')} slice={attrs.get('slice')} "
            f"prd={attrs.get('prd', '?')} session_id={attrs.get('session_id', '?')}"
        )
    return 0


def _cmd_path(args):
    chain = acid_path(args.pr)
    if chain is None:
        print(f"no recorded trace for PR #{args.pr}", file=sys.stderr)
        return 1
    for s in chain:
        dur = s.get("dur_ms")
        dur_str = f" dur_ms={dur}" if dur is not None else ""
        parent = s.get("parent_span_id")
        parent_str = f" parent={parent}" if parent else ""
        print(
            f"{s.get('ts')} kind={s.get('kind')} span={s.get('span_id')}"
            f"{parent_str} attrs={json.dumps(s.get('attrs', {}), sort_keys=True)}"
            f"{dur_str}"
        )
    return 0


def _cmd_runboard(args):
    board = _build_runboard()
    print(f"now ({len(board['now'])}, stale-threshold={board['stale_threshold_seconds']}s):")
    for e in board["now"]:
        stale_marker = " STALE" if e.get("stale") else ""
        print(f"  slice=#{e['slice']} prd={e.get('prd', '?')} elapsed={e['elapsed_seconds']}s{stale_marker}")
    print(f"recent ({len(board['recent'])}):")
    for e in board["recent"]:
        label = f"pr=#{e['pr']}" if e["kind"] == "pr_merged" else f"slice=#{e['slice']}"
        print(f"  {label} outcome={e['outcome']} dur_ms={e['dur_ms']}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tracestore.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fold = sub.add_parser("fold", help="force-refold the sqlite read-model")
    p_fold.set_defaults(func=_cmd_fold)

    p_path = sub.add_parser("path", help="indexed acid-test causal-path query")
    p_path.add_argument("--pr", required=True, type=int)
    p_path.set_defaults(func=_cmd_path)

    p_running = sub.add_parser("running", help='"running now" query: dispatch spans lacking a terminal dispatch_end')
    p_running.set_defaults(func=_cmd_running)

    p_runboard = sub.add_parser("runboard", help="now/recent run-board query (PRD #1170)")
    p_runboard.set_defaults(func=_cmd_runboard)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
