"""
Regression tests for issue #1184 / slice #1189 — identity-verifying dashboard
liveness probe.

Root-cause (#1184): on 2026-08-20 a FOREIGN project squatted port 8765.
It answered /api/architecture with HTTP 200 (satisfying the old
dashboard-autostart.sh idempotency probe, which concluded a healthy server
was running and did nothing) while 404'ing /api/meta (which
STALE-SERVER / check_stale_server() probed, then mis-classified the HTTP
error as "server not reachable" — i.e. benign no-server). Three probes,
three different wrong answers, none of them verifying the responder's
IDENTITY.

ADR-0067 D3 / rule #13 regression rider: this test commit precedes the fix
commit in branch history. These tests intentionally FAIL on the unfixed
codebase and PASS after the fix:

  (a) the shared `dashboard_probe_identity` bash contract (added to
      lib-root.sh by the fix) classifies a fixture server answering the
      exact incident shape (200 on /api/architecture, 404 on /api/meta) as
      "occupied", never "ok" — this test cannot even source the function
      pre-fix (it does not exist yet), so it fails with a non-zero exit.
  (b) dashboard/health.py::check_stale_server() classifies the same
      incident shape as FAIL (occupied by a foreign listener) instead of
      the pre-fix WARN "not reachable" (because HTTPError is a URLError
      subclass and was being caught by the generic unreachable branch).

The fixture is a real `http.server.HTTPServer` bound to an OS-assigned
scratch port (never 8765/8766 — see hard constraint), started per-test-class
and killed in tearDownClass. All HTTP traffic in this file targets ONLY the
fixture's own ephemeral port; the real dashboard port is never touched.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_identity_liveness_probe_1189.py -v
"""

import contextlib
import http.server
import json
import socket
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).parent.parent
LIB_ROOT_SH = ".claude/hooks/lib-root.sh"  # relative to REPO_ROOT, posix-style for bash
HEALTH_PY = REPO_ROOT / "dashboard" / "health.py"

_FORBIDDEN_PORTS = (8765, 8766)  # real dashboard ports — never bind/target these


# ---------------------------------------------------------------------------
# Fixture servers
# ---------------------------------------------------------------------------

class _ForeignIncidentHandler(http.server.BaseHTTPRequestHandler):
    """Reproduces the exact #1184 incident shape: 200 on /api/architecture,
    404 on /api/meta (a foreign project with no such endpoint)."""

    def do_GET(self):
        if self.path.startswith("/api/architecture"):
            body = b'{"foreign_project": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"Not Found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt, *args):  # silence request logging
        pass


class _CompliantHandler(http.server.BaseHTTPRequestHandler):
    """A well-behaved responder: valid JSON with a sha field on /api/meta."""

    sha = "deadbeefcafef00d"

    def do_GET(self):
        body = json.dumps({"sha": self.sha, "ts": "2026-08-20T00:00:00Z"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


@contextlib.contextmanager
def _fixture_server(handler_cls):
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    port = httpd.server_address[1]
    assert port not in _FORBIDDEN_PORTS, (
        f"fixture accidentally bound a real dashboard port ({port}) — refusing"
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _unused_port() -> int:
    """Return a port number nothing is listening on (bind-then-close trick)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert port not in _FORBIDDEN_PORTS
    return port


# ---------------------------------------------------------------------------
# Group A — shared bash identity probe (dashboard-autostart.sh site)
# ---------------------------------------------------------------------------

class TestSharedBashIdentityProbe(unittest.TestCase):
    """dashboard_probe_identity() in lib-root.sh — the shared contract used
    by dashboard-autostart.sh's idempotency check."""

    def _bash_available(self) -> bool:
        try:
            r = subprocess.run(["bash", "--version"], capture_output=True, timeout=5)
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _run_probe(self, base_url: str, timeout: str = "2") -> subprocess.CompletedProcess:
        py = sys.executable.replace("\\", "/")
        script = (
            f'source "{LIB_ROOT_SH}"\n'
            f'dashboard_probe_identity "{py}" "{base_url}" {timeout}\n'
        )
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT), timeout=20,
        )

    def test_incident_shape_classified_occupied_not_ok(self):
        """The exact #1184 shape (200 on /api/architecture, 404 on /api/meta)
        must be classified 'occupied', never 'ok' (autostart must not treat
        this foreign listener as a healthy same-code server)."""
        if not self._bash_available():
            self.skipTest("bash not available in this environment")
        with _fixture_server(_ForeignIncidentHandler) as port:
            proc = self._run_probe(f"http://127.0.0.1:{port}")
        self.assertEqual(
            0, proc.returncode,
            msg=(
                "dashboard_probe_identity() must be defined in lib-root.sh "
                f"(slice #1189). STDOUT: {proc.stdout!r} STDERR: {proc.stderr!r}"
            ),
        )
        out = proc.stdout.strip()
        self.assertTrue(
            out.startswith("occupied"),
            msg=f"Expected 'occupied ...' for the incident shape, got: {out!r}",
        )
        self.assertFalse(
            out.startswith("ok"),
            msg=f"Foreign listener must never be classified 'ok': {out!r}",
        )

    def test_compliant_server_classified_ok(self):
        """A well-behaved dashboard (valid JSON + sha on /api/meta) is 'ok'."""
        if not self._bash_available():
            self.skipTest("bash not available in this environment")
        with _fixture_server(_CompliantHandler) as port:
            proc = self._run_probe(f"http://127.0.0.1:{port}")
        self.assertEqual(0, proc.returncode, msg=proc.stderr)
        out = proc.stdout.strip()
        self.assertTrue(
            out.startswith("ok " + _CompliantHandler.sha),
            msg=f"Expected 'ok {_CompliantHandler.sha}', got: {out!r}",
        )

    def test_genuine_no_listener_classified_no_server(self):
        """Nothing answering at all must stay 'no-server' (never 'occupied')."""
        if not self._bash_available():
            self.skipTest("bash not available in this environment")
        proc = self._run_probe(f"http://127.0.0.1:{_unused_port()}", timeout="1")
        self.assertEqual(0, proc.returncode, msg=proc.stderr)
        self.assertEqual(
            "no-server", proc.stdout.strip(),
            msg="Genuine no-listener must classify as 'no-server'",
        )


# ---------------------------------------------------------------------------
# Group B — check_stale_server() (STALE-SERVER health check)
# ---------------------------------------------------------------------------

def _run_check_stale_server_against(port: int, head_sha: str) -> dict:
    """Invoke check_stale_server() in a subprocess with urllib.request.urlopen
    monkeypatched to redirect ANY URL it constructs to 127.0.0.1:<port> — this
    works regardless of whether the code under test has a URL-override seam,
    and never touches the real dashboard port."""
    script = f'''
import sys
sys.path.insert(0, r"{REPO_ROOT / "dashboard"}")
import os
os.environ["_STALE_SERVER_HEAD_OVERRIDE"] = {head_sha!r}
os.environ.pop("_STALE_SERVER_META_OVERRIDE", None)

import urllib.request as _r
_orig_urlopen = _r.urlopen
def _redirect(url, *a, **kw):
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    new_url = urlunsplit(("http", "127.0.0.1:{port}", parts.path, parts.query, parts.fragment))
    return _orig_urlopen(new_url, *a, **kw)
_r.urlopen = _redirect

from health import check_stale_server
import json
print(json.dumps(check_stale_server()))
'''
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True,
        cwd=str(REPO_ROOT / "dashboard"), timeout=20,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"check_stale_server() subprocess failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
        )
    return json.loads(proc.stdout.strip())


class TestCheckStaleServerIdentityVerifying(unittest.TestCase):
    """STALE-SERVER must classify the #1184 incident shape as FAIL/occupied,
    never the benign 'not reachable' WARN it emitted during the real outage."""

    def test_incident_shape_is_fail_occupied_not_warn(self):
        with _fixture_server(_ForeignIncidentHandler) as port:
            result = _run_check_stale_server_against(port, head_sha="abc1234deadbeef")
        self.assertEqual(
            "FAIL", result["result"],
            msg=(
                f"Expected FAIL (occupied by foreign listener) but got "
                f"{result['result']}: {result.get('detail')}"
            ),
        )
        self.assertTrue(
            result.get("occupied") is True,
            msg=f"Result must carry occupied=True: {result}",
        )
        self.assertIn(
            "occupied", result.get("detail", "").lower(),
            msg=f"FAIL detail must name the wrong-payload responder: {result.get('detail')}",
        )

    def test_compliant_fresh_server_still_passes(self):
        """Sanity: a well-behaved, fresh server still PASSes (no regression)."""
        with _fixture_server(_CompliantHandler) as port:
            result = _run_check_stale_server_against(port, head_sha=_CompliantHandler.sha)
        self.assertEqual(
            "PASS", result["result"],
            msg=f"Expected PASS but got {result['result']}: {result.get('detail')}",
        )

    def test_genuine_no_listener_stays_warn_not_fail(self):
        """A real absence of any listener must stay WARN, never FAIL/occupied."""
        result = _run_check_stale_server_against(_unused_port(), head_sha="abc1234deadbeef")
        self.assertEqual(
            "WARN", result["result"],
            msg=f"Expected WARN (no listener) but got {result['result']}: {result.get('detail')}",
        )
        self.assertIsNot(
            result.get("occupied"), True,
            msg="Genuine no-listener must not be marked occupied",
        )


# ---------------------------------------------------------------------------
# Group C — session-start.sh dashboard-freshness line (static wiring check)
# ---------------------------------------------------------------------------

SESSION_START_SH = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"


class TestSessionStartOccupiedDifferentiation(unittest.TestCase):
    """session-start.sh's dashboard-freshness probe must distinguish
    'occupied by a foreign listener' from a real 'unreachable' (no listener)
    in the injected context line (#1184 site 2).

    Repointed by #1204: this site no longer inlines its own `python3 -c`
    urllib-based HTTPError/missing-sha classification — it now calls the
    shared `dashboard_probe_identity()` contract (lib-root.sh) and maps that
    function's three-way "ok"/"occupied"/"no-server" result onto the same
    banner wording (the inline probe's per-address-family socket timeout
    cost 4.14s on an EMPTY port — issue #1204's root cause). This remains a
    static content check (session-start.sh still has no parameterizable
    base-URL seam of its own); the functional identity-verifying
    classification logic is exercised directly by Group A above
    (TestSharedBashIdentityProbe, against real fixture HTTP servers).
    """

    def test_calls_shared_probe_identity_contract(self):
        content = SESSION_START_SH.read_text(encoding="utf-8")
        self.assertIn(
            "dashboard_probe_identity", content,
            msg="session-start.sh must call the shared dashboard_probe_identity() "
                "contract (lib-root.sh) instead of inlining its own probe (#1204)",
        )

    def test_occupied_case_still_labeled_occupied(self):
        content = SESSION_START_SH.read_text(encoding="utf-8")
        self.assertIn(
            "OCCUPIED", content,
            msg="session-start.sh must still label the shared probe's "
                "'occupied' result OCCUPIED in the banner — the #1184 "
                "incident-class signal is load-bearing and must survive "
                "the #1204 repoint",
        )


if __name__ == "__main__":
    unittest.main()
