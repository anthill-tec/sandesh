"""test_axi_notify.py — RED tests for CR-SAN-047 cycle 208 (§S5 `notify` final
envelope), against:
  docs/changes/CR-SAN-047-axi-toon-cli-envelope.md  §S5, AC8, AC9
  docs/research/PRD-axi-toon.md  §4.5

Target contract: `notify.run(project_id, address, timeout, fmt="human")`. When
`fmt` is `toon`/`json`, progress lines go to stderr and exactly one envelope
`{verb:"notify", ok, exit, address, project, unread[N]}` (+ `context.project`,
`context.address`, `warnings`) is written to stdout on EVERY exit path (0, 2,
3, 4, 5, 1, and a signal). `ok` is true for 0/2/5, false for 1/3/4/signal with
`error`. `unread` lists the triggering ids ascending on exit 0, else `[]`.
Today `notify.run()` has no `fmt` parameter at all — every machine-mode call
in section A/B below raises `TypeError` immediately (a valid RED: the kwarg
doesn't exist yet).

DESIGN DECISION — the exit-5 (dedup) representation: the dispatch prompt left
open whether the dedup reason lives in a `reason` field or in `warnings`,
recommending `warnings[1]`. This suite asserts `warnings == [<reason>]`
(single-element list, INDEX 0) — there is no other pre-existing warning to
occupy index 0 in the notify envelope, so a one-element list is the simplest
faithful representation of "one dedup warning, no reason to reserve index 0
for something else". GREEN must either match this or the maintainer must
adjust this test with a documented reason.

GOLDEN CAPTURE (AC9 — human mode byte-identical; two pins captured from the
CURRENT tree, `notify.run()` unchanged wrt fmt="human"):
  Fixture: XDG_DATA_HOME=<temp>; sdb.setup("Demo");
  sdb.register(con, "Track 1 - Demo", kind="track", project="Demo").
  Deterministic mocks (module-level, since notify.py does `import time`/`os`/
  `socket` and calls `time.strftime`/`os.getpid`/`socket.gethostname` as bound
  module attributes):
    - notify.sdb.notifier_acquire -> (True, "acquired")
    - notify.sdb.notifier_check   -> "ok"
    - notify.sdb.notifier_heartbeat -> no-op
    - notify.sdb.unread_to        -> [12, 13]  (exit0)  /  []  (exit2)
    - notify.time.strftime        -> "12:00:00" (any args)
    - notify.os.getpid            -> 4242
    - notify.socket.gethostname   -> "test-host"
    - notify.time.monotonic       -> side_effect [0, 5]      (exit2 only, forces
      the deadline check to fire on the very first poll with timeout=0)
    - env SANDESH_POLL_SECONDS=3 (floor; deterministic 'interval')
  Call: notify.run("Demo", "Track 1 - Demo", 60)   -> exit0 golden
        notify.run("Demo", "Track 1 - Demo", 0)    -> exit2 golden
  (no `fmt` kwarg passed at all — this is what lets the two golden tests PASS
  BOTH before AND after GREEN: pre-GREEN `run()` has no `fmt` parameter, so
  omitting it is the only way to call it; post-GREEN the default is
  `fmt="human"`, so the call and its stdout stay identical.)
  Captured stdout written verbatim to tests/golden/notify.exit0.human.txt and
  tests/golden/notify.exit2.human.txt.

Run targeted (Crucible client resolves the venv interpreter):
  PYTHONPATH=. .venv/bin/python tests/test_axi_notify.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import _toon  # noqa: E402
from sandesh import cli  # noqa: E402
from sandesh import notify  # noqa: E402
from sandesh import sandesh_db as sdb  # noqa: E402

_VENV_PYTHON = os.path.join(_REPO_ROOT, ".venv", "bin", "python")
_SUBPROCESS_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

_GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

_NO_FMT = object()  # sentinel: call notify.run() with NO fmt kwarg at all


def _load_golden(name):
    with open(os.path.join(_GOLDEN_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _build_env(tmp_xdg, extra=None):
    env = {**os.environ}
    env["XDG_DATA_HOME"] = tmp_xdg
    env["PYTHONPATH"] = _REPO_ROOT
    if extra:
        env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# Section A — in-process fast paths, notify.run(..., fmt=...) with sdb
# monkeypatched exactly like tests/test_notifier_lock_resilience.py.
# --------------------------------------------------------------------------- #

class _NotifyFastPathFixture(unittest.TestCase):
    PROJ = "Demo"
    ADDR = "Track 1 - Demo"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-axi-notify-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        self._prev_poll = os.environ.get("SANDESH_POLL_SECONDS")
        sdb.setup(self.PROJ)
        con = sdb.connect()
        sdb.register(con, self.ADDR, kind="track", project=self.PROJ)
        con.close()

    def tearDown(self):
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        if self._prev_poll is None:
            os.environ.pop("SANDESH_POLL_SECONDS", None)
        else:
            os.environ["SANDESH_POLL_SECONDS"] = self._prev_poll
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, timeout, fmt=_NO_FMT, sdb_overrides=None, time_overrides=None,
              os_overrides=None, socket_overrides=None):
        """Call notify.run() with sdb/time/os/socket attrs monkeypatched.

        `fmt=_NO_FMT` (default) omits the kwarg entirely (needed for the
        golden pins, which must work against the pre-GREEN signature too).
        """
        sdb_overrides = sdb_overrides or {}
        time_overrides = time_overrides or {}
        os_overrides = os_overrides or {}
        socket_overrides = socket_overrides or {}
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(notify.atexit, "register", lambda *a, **k: None))
            stack.enter_context(mock.patch.object(notify.signal, "signal", lambda *a, **k: None))
            for name, value in sdb_overrides.items():
                stack.enter_context(mock.patch.object(notify.sdb, name, value))
            for name, value in time_overrides.items():
                stack.enter_context(mock.patch.object(notify.time, name, value))
            for name, value in os_overrides.items():
                stack.enter_context(mock.patch.object(notify.os, name, value))
            for name, value in socket_overrides.items():
                stack.enter_context(mock.patch.object(notify.socket, name, value))
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                if fmt is _NO_FMT:
                    rc = notify.run(self.PROJ, self.ADDR, timeout)
                else:
                    # deliberately dynamic: notify.run() has NO `fmt` parameter
                    # yet — this is the RED signal (TypeError at call time).
                    kwargs = {"fmt": fmt}
                    rc = notify.run(self.PROJ, self.ADDR, timeout, **kwargs)
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def _decode_one_envelope(self, out):
        self.assertEqual(out.count("axi:"), 1, f"exactly one envelope; got out={out!r}")
        return _toon.decode(out)["axi"]


class NotifyExit0Test(_NotifyFastPathFixture):
    """exit 0 — mail: ok true, unread ascending, address/project/context set,
    progress lines on stderr only."""

    def test_toon_exit0_envelope_shape_and_stderr_separation(self):
        rc, out, err = self._run(
            timeout=60, fmt="toon",
            sdb_overrides={
                "notifier_acquire": lambda *a: (True, "acquired"),
                "notifier_check": lambda *a: "ok",
                "notifier_heartbeat": lambda *a: None,
                "unread_to": lambda *a: [12, 13],
            },
        )
        self.assertEqual(rc, 0)
        axi = self._decode_one_envelope(out)
        self.assertEqual(axi["verb"], "notify")
        self.assertEqual(axi["exit"], 0)
        self.assertIs(axi["ok"], True)
        self.assertEqual(axi["unread"], [12, 13])
        self.assertNotIn("error", axi)
        self.assertEqual(axi["address"], self.ADDR)
        self.assertEqual(axi["project"], self.PROJ)
        self.assertEqual(axi["context"]["project"], self.PROJ)
        self.assertEqual(axi["context"]["address"], self.ADDR)
        self.assertIn("warnings", axi)
        self.assertNotIn("[notify]", out, f"no progress text on stdout; out={out!r}")
        self.assertIn("[notify]", err, f"progress text must be on stderr; err={err!r}")

    def test_fmt_json_exit0_decodes_to_the_same_dict_as_toon(self):
        overrides = {
            "notifier_acquire": lambda *a: (True, "acquired"),
            "notifier_check": lambda *a: "ok",
            "notifier_heartbeat": lambda *a: None,
            "unread_to": lambda *a: [12, 13],
        }
        rc_toon, out_toon, _ = self._run(timeout=60, fmt="toon", sdb_overrides=overrides)
        rc_json, out_json, _ = self._run(timeout=60, fmt="json", sdb_overrides=overrides)
        self.assertEqual(rc_toon, 0)
        self.assertEqual(rc_json, 0)
        self.assertEqual(json.loads(out_json), _toon.decode(out_toon))


class NotifyExit2Test(_NotifyFastPathFixture):
    """exit 2 — timeout: ok true, unread empty (wire form `unread: []`)."""

    def test_toon_exit2_envelope_ok_true_empty_unread(self):
        rc, out, err = self._run(
            timeout=0, fmt="toon",
            sdb_overrides={
                "notifier_acquire": lambda *a: (True, "acquired"),
                "notifier_check": lambda *a: "ok",
                "notifier_heartbeat": lambda *a: None,
                "unread_to": lambda *a: [],
            },
            time_overrides={"monotonic": mock.Mock(side_effect=[0, 5])},
        )
        self.assertEqual(rc, 2)
        axi = self._decode_one_envelope(out)
        self.assertEqual(axi["exit"], 2)
        self.assertIs(axi["ok"], True)
        self.assertEqual(axi["unread"], [])
        self.assertNotIn("error", axi)
        self.assertNotIn("[notify]", out)
        self.assertIn("[notify]", err)


class NotifyExit5Test(_NotifyFastPathFixture):
    """exit 5 — dedup: a normal outcome, ok true, empty unread, the dedup
    reason surfaced as a single-element `warnings` list (see module docstring
    DESIGN DECISION)."""

    def test_toon_exit5_dedup_ok_true_reason_in_warnings(self):
        reason = "another notifier already live for 'Track 1 - Demo' (pid 999)"
        rc, out, err = self._run(
            timeout=60, fmt="toon",
            sdb_overrides={"notifier_acquire": lambda *a: (False, reason)},
        )
        self.assertEqual(rc, 5)
        axi = self._decode_one_envelope(out)
        self.assertEqual(axi["exit"], 5)
        self.assertIs(axi["ok"], True)
        self.assertEqual(axi["unread"], [])
        self.assertNotIn("error", axi)
        self.assertEqual(axi["warnings"], [reason],
                          "dedup reason must be the sole warnings[] element (design decision)")


class NotifyExit3And4Test(_NotifyFastPathFixture):
    """exit 3 (tombstoned) / exit 4 (evicted): ok false, non-empty error
    naming the reason, empty unread."""

    def test_toon_exit3_tombstoned_ok_false_error_names_tombstoned(self):
        rc, out, err = self._run(
            timeout=60, fmt="toon",
            sdb_overrides={
                "notifier_acquire": lambda *a: (True, "acquired"),
                "notifier_check": lambda *a: "tombstoned",
            },
        )
        self.assertEqual(rc, 3)
        axi = self._decode_one_envelope(out)
        self.assertEqual(axi["exit"], 3)
        self.assertIs(axi["ok"], False)
        self.assertTrue(axi["error"], "error must be non-empty")
        self.assertIn("tombstoned", axi["error"].lower())
        self.assertTrue(axi.get("help"))
        self.assertEqual(axi["unread"], [])

    def test_toon_exit4_evicted_ok_false_error_names_evicted(self):
        rc, out, err = self._run(
            timeout=60, fmt="toon",
            sdb_overrides={
                "notifier_acquire": lambda *a: (True, "acquired"),
                "notifier_check": lambda *a: "evicted",
            },
        )
        self.assertEqual(rc, 4)
        axi = self._decode_one_envelope(out)
        self.assertEqual(axi["exit"], 4)
        self.assertIs(axi["ok"], False)
        self.assertTrue(axi["error"], "error must be non-empty")
        self.assertIn("evicted", axi["error"].lower())
        self.assertTrue(axi.get("help"))
        self.assertEqual(axi["unread"], [])


class NotifyExit1Test(_NotifyFastPathFixture):
    """exit 1 — usage/config error: ok false, error = the message, empty
    unread. Two distinct causes: validate_address raising, and is_active
    returning False."""

    def test_toon_exit1_validate_address_raises_error_equals_message(self):
        rc, out, err = self._run(
            timeout=60, fmt="toon",
            sdb_overrides={
                "validate_address": mock.Mock(side_effect=ValueError("bad address format")),
            },
        )
        self.assertEqual(rc, 1)
        axi = self._decode_one_envelope(out)
        self.assertEqual(axi["exit"], 1)
        self.assertIs(axi["ok"], False)
        self.assertEqual(axi["error"], "bad address format")
        self.assertTrue(axi.get("help"))
        self.assertEqual(axi["unread"], [])

    def test_toon_exit1_is_active_false_error_names_not_registered(self):
        rc, out, err = self._run(
            timeout=60, fmt="toon",
            sdb_overrides={"is_active": lambda *a: False},
        )
        self.assertEqual(rc, 1)
        axi = self._decode_one_envelope(out)
        self.assertEqual(axi["exit"], 1)
        self.assertIs(axi["ok"], False)
        self.assertIn("not registered", axi["error"].lower())
        self.assertTrue(axi.get("help"))
        self.assertEqual(axi["unread"], [])


class NotifyStartupMigrationEnvelopeTest(unittest.TestCase):
    def test_migration_required_before_notify_start_emits_one_error_envelope(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sdb, "connect", side_effect=sdb.MigrationRequired("store schema is behind")), \
                redirect_stdout(out), redirect_stderr(err):
            rc = cli.main([
                "--format", "toon", "notify", "--project", "Demo",
                "--to", "Track 1 - Demo",
            ])
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue().count("axi:"), 1)
        axi = _toon.decode(out.getvalue())["axi"]
        self.assertEqual(axi["verb"], "notify")
        self.assertIs(axi["ok"], False)
        self.assertEqual(axi["error"], "store schema is behind")
        self.assertTrue(axi.get("help"))


class NotifyHumanGoldenPinTest(_NotifyFastPathFixture):
    """AC9 pins — fmt="human" (the default; no kwarg passed at all) must stay
    byte-identical to today's output for the exit0 and exit2 fast paths.
    These two tests PASS today and must keep passing after GREEN."""

    _DETERMINISTIC_KW = {
        "os_overrides": {"getpid": lambda: 4242},
        "socket_overrides": {"gethostname": lambda: "test-host"},
        "time_overrides": {"strftime": lambda *a, **k: "12:00:00"},
    }

    def setUp(self):
        super().setUp()
        os.environ["SANDESH_POLL_SECONDS"] = "3"

    def test_human_exit0_output_matches_golden(self):
        rc, out, err = self._run(
            timeout=60,
            sdb_overrides={
                "notifier_acquire": lambda *a: (True, "acquired"),
                "notifier_check": lambda *a: "ok",
                "notifier_heartbeat": lambda *a: None,
                "unread_to": lambda *a: [12, 13],
            },
            **self._DETERMINISTIC_KW,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out, _load_golden("notify.exit0.human.txt"))
        self.assertEqual(err, "")

    def test_human_exit2_output_matches_golden(self):
        rc, out, err = self._run(
            timeout=0,
            sdb_overrides={
                "notifier_acquire": lambda *a: (True, "acquired"),
                "notifier_check": lambda *a: "ok",
                "notifier_heartbeat": lambda *a: None,
                "unread_to": lambda *a: [],
            },
            time_overrides={
                **self._DETERMINISTIC_KW["time_overrides"],
                "monotonic": mock.Mock(side_effect=[0, 5]),
            },
            os_overrides=self._DETERMINISTIC_KW["os_overrides"],
            socket_overrides=self._DETERMINISTIC_KW["socket_overrides"],
        )
        self.assertEqual(rc, 2)
        self.assertEqual(out, _load_golden("notify.exit2.human.txt"))
        self.assertEqual(err, "")


# --------------------------------------------------------------------------- #
# Section B — real subprocess: python -m sandesh.cli --format toon notify ...
# --------------------------------------------------------------------------- #

class NotifySubprocessTest(unittest.TestCase):
    PROJ = "Demo"
    TO = "Mainline - Demo"
    FROM = "Track 1 - Demo"

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(_SUBPROCESS_PYTHON):
            raise unittest.SkipTest(f"subprocess python not found at {_SUBPROCESS_PYTHON!r}")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-axi-notify-sub-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        sdb.setup(self.PROJ)
        con = sdb.connect()
        sdb.register(con, self.TO, kind="mainline", project=self.PROJ)
        sdb.register(con, self.FROM, kind="track", project=self.PROJ)
        con.close()
        self.env = _build_env(self.tmp, {"SANDESH_POLL_SECONDS": "3"})
        self._procs = []

    def tearDown(self):
        for p in self._procs:
            if p.poll() is None:
                p.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    p.wait(timeout=5)
        self._procs.clear()
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_cli(self, argv, timeout):
        cmd = [_SUBPROCESS_PYTHON, "-m", "sandesh.cli"] + list(argv)
        proc = subprocess.run(cmd, env=self.env, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr

    def _poll_acquired(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            c = sdb.connect()
            try:
                if sdb.notifier_live(c, self.TO) is not None:
                    return
            finally:
                c.close()
            if time.monotonic() >= deadline:
                raise AssertionError(f"notifier row for {self.TO!r} never became live")
            time.sleep(0.2)

    def test_toon_exit2_short_timeout_single_envelope_stderr_progress(self):
        rc, out, err = self._run_cli(
            ["--format", "toon", "notify", "--project", self.PROJ,
             "--to", self.TO, "--timeout", "1"],
            timeout=15,
        )
        self.assertEqual(rc, 2, f"out={out!r} err={err!r}")
        self.assertEqual(out.count("axi:"), 1, f"exactly one envelope; out={out!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["exit"], 2)
        self.assertNotIn("help", axi)
        self.assertIn("[notify]", err)

    def test_toon_exit0_wakes_on_send_unread_lists_triggering_id(self):
        # self.env carries XDG_DATA_HOME=<temp store> (built in setUp via _build_env) — §S3 subprocess discipline
        proc = subprocess.Popen(
            [_SUBPROCESS_PYTHON, "-m", "sandesh.cli", "--format", "toon",
             "notify", "--project", self.PROJ, "--to", self.TO, "--timeout", "30"],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self._procs.append(proc)
        self._poll_acquired(timeout=10)

        con = sdb.connect()
        store = sdb.store_dir(self.PROJ)
        mid = sdb.send(con, store, self.FROM, to=[self.TO], subject="ping", project=self.PROJ)
        con.close()

        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate(timeout=5)
            self.fail(f"watcher did not exit after send; stderr={err!r}")
        self._procs.remove(proc)
        self.assertEqual(proc.returncode, 0, f"out={out!r} err={err!r}")
        self.assertEqual(out.count("axi:"), 1, f"exactly one envelope; out={out!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["exit"], 0)
        self.assertIs(axi["ok"], True)
        self.assertEqual(axi["unread"], [mid])

    def test_sigterm_exit_143_single_envelope_error_names_signal_notifier_released(self):
        # self.env carries XDG_DATA_HOME=<temp store> (built in setUp via _build_env) — §S3 subprocess discipline
        proc = subprocess.Popen(
            [_SUBPROCESS_PYTHON, "-m", "sandesh.cli", "--format", "toon",
             "notify", "--project", self.PROJ, "--to", self.TO, "--timeout", "60"],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self._procs.append(proc)
        self._poll_acquired(timeout=10)

        proc.send_signal(signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate(timeout=5)
            self.fail(f"watcher did not exit after SIGTERM; stderr={err!r}")
        self._procs.remove(proc)

        self.assertEqual(proc.returncode, 143, f"out={out!r} err={err!r}")
        self.assertEqual(out.count("axi:"), 1, f"exactly one envelope; out={out!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi["exit"], 143)
        self.assertIs(axi["ok"], False)
        self.assertTrue(
            "sigterm" in axi["error"].lower() or "signal" in axi["error"].lower(),
            f"error must name SIGTERM/signal; got {axi['error']!r}",
        )
        self.assertTrue(axi.get("help"))
        self.assertEqual(axi["unread"], [])

        con = sdb.connect()
        try:
            self.assertIsNone(
                sdb.notifier_live(con, self.TO),
                "notifier row must be released (atexit) after SIGTERM",
            )
        finally:
            con.close()

    def test_human_mode_short_timeout_unchanged_no_envelope(self):
        rc, out, err = self._run_cli(
            ["notify", "--project", self.PROJ, "--to", self.TO, "--timeout", "1"],
            timeout=15,
        )
        self.assertEqual(rc, 2, f"out={out!r} err={err!r}")
        self.assertIn("timed out", out)
        self.assertNotIn("axi:", out)


# --------------------------------------------------------------------------- #
# Section C — notify.main() standalone entry: no fmt plumbing, always human.
# --------------------------------------------------------------------------- #

class NotifyMainStandaloneEntryTest(unittest.TestCase):
    PROJ = "Demo"
    ADDR = "Track 1 - Demo"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-axi-notify-main-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        sdb.setup(self.PROJ)
        con = sdb.connect()
        sdb.register(con, self.ADDR, kind="track", project=self.PROJ)
        con.close()

    def tearDown(self):
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_main_with_no_format_flag_defaults_human_returns_2_no_envelope(self):
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(notify.atexit, "register", lambda *a, **k: None))
            stack.enter_context(mock.patch.object(notify.signal, "signal", lambda *a, **k: None))
            stack.enter_context(mock.patch.object(
                notify.sdb, "notifier_acquire", lambda *a: (True, "acquired")))
            stack.enter_context(mock.patch.object(notify.sdb, "notifier_check", lambda *a: "ok"))
            stack.enter_context(mock.patch.object(notify.sdb, "notifier_heartbeat", lambda *a: None))
            stack.enter_context(mock.patch.object(notify.sdb, "unread_to", lambda *a: []))
            stack.enter_context(mock.patch.object(
                notify.time, "monotonic", mock.Mock(side_effect=[0, 5])))
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                rc = notify.main(["--project", self.PROJ, "--to", self.ADDR, "--timeout", "0"])
        self.assertEqual(rc, 2)
        self.assertNotIn("axi:", out_buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
