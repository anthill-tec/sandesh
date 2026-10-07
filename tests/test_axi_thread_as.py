"""test_axi_thread_as.py — RED tests for CR-SAN-054 cycle 2 (§S2 — `thread --as`,
and withheld bodies are announced), against:
  docs/changes/CR-SAN-054-read-mail-readable.md  §S2, AC3, AC4

Scope for THIS cycle (AC3 + AC4 only):
  1. AC3 — `thread --as "Mainline - Demo"` (a party to the message, no env caller
     set at all) returns the body and carries no `withheld` key.
  2. AC4 — no `--as` and no env caller: `rc 0`, `bodies` empty, `withheld: 1`,
     and `help[]` names `--as`.
  3. AC4 — `--as "Track 2 - Demo"` (registered in the project, but not sender/
     recipient of the message): `rc 0`, `withheld: 1`, no body for the message.
  4. AC4 — `--as "Mainline - Other"` (syntactically valid, wrong project for
     `--project Demo`): `ok:false`, `axi["verb"] == "thread"` (NOT the
     top-level "sandesh" usage-error path), error names the full address.
  5. AC4 — no `--as`, `$SANDESH_ADDRESS="Mainline - Other"` in the env: the
     0.4.0 env-caller rule is KEPT — treated as absent, `rc 0` (not an error),
     `withheld: 1`.
  6. AC4 (edge) — a chain of subject-only messages (no bodies anywhere) with no
     caller at all: `rc 0`, no `withheld` key (nothing was ever withheld).
  7. AC4 — toon mode decodes the same `withheld: 1` as JSON mode.

These tests drive the REAL CLI in a REAL subprocess (`python -m sandesh.cli`,
per the dispatch prompt), NOT in-process `cli.main` like sibling AXI test
modules — the whole point of AC4/AC5 is the environment-caller rule, so the
env dict must be exactly what we construct, never polluted by whatever the
test runner process happens to have set. `SANDESH_ADDRESS`/`WF_TRACK` (plus
`SANDESH_PROJECT`/`SANDESH_FORMAT`, which every CLI driven here sets via an
explicit flag) are stripped from the copied environment baseline; a test that
wants an env caller adds it back explicitly via `env=`.

Fixture (built in-process via `sandesh_db`, read back by the subprocess from
the same on-disk XDG_DATA_HOME store): project `Demo` with `Mainline - Demo`,
`Track 1 - Demo`, `Track 2 - Demo` registered; message `#self.mid` from
`Track 1 - Demo` to `Mainline - Demo` with a body containing "gateway
timeout", already fetched (read). Project `Other` with `Mainline - Other`
registered (syntactically valid address, wrong project for `Demo`). A second,
independent two-message reply chain of SUBJECT-ONLY messages (`#self.
subject_only_leaf` is the reply; `sdb.thread()` walks from a message UP to
its root, so the leaf is what's passed as `--id` to see the whole chain).

Expected RED (confirmed empirically against the current tree before writing
these tests — see the probe transcripts in the cycle notes): `thread` has no
`--as` argument at all yet, so every `--as ...` invocation here fails
argparse's own "unrecognized arguments" check (a TOP-LEVEL usage error, exit
2, `axi["verb"] == "sandesh"`) rather than the feature's own-verb validation
logic (which per `_run_machine` would be `axi["verb"] == "thread"`, exit 1,
ValueError text). And `axi_thread` (`sandesh/cli.py` l.1163-1201) never emits
a `withheld` key or an `--as`-naming `help[]` entry at all today, regardless
of caller — so every `withheld`-count assertion KeyErrors / fails on current
code, even in cases (2, 3, 5) whose `rc 0`/empty-`bodies` half already holds
(the pre-existing env-caller-treated-as-absent rule is NOT new; only the
`withheld` announcement is).

Interpretation note (AC4's "error naming the address", not a literal spec
quote — refined per the dispatch prompt's exact wording): asserted as the
substring "Mainline - Other" appearing in `axi["error"]`, and `axi["verb"]
== "thread"` as the differentiator that actually forces RED today (the
literal "unrecognized arguments: --as Mainline - Other" string coincidentally
already contains "Mainline - Other" as a substring, so that assertion ALONE
would pass today for the wrong reason; the verb check closes that gap).

Conventions follow tests/test_axi_search_fields.py's fixture style (project
`Demo`, `sdb.send`/`sdb.fetch` directly, XDG-temp store per test), but drive
the CLI via a REAL subprocess per the dispatch prompt rather than in-process
`cli.main`.

Run (from the repo root):
  python3 -m unittest -v tests.test_axi_thread_as
  or   PYTHONPATH=. .venv/bin/python tests/test_axi_thread_as.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import _toon  # noqa: E402
from sandesh import sandesh_db as sdb  # noqa: E402

_STRIPPED_ENV_VARS = ("SANDESH_ADDRESS", "WF_TRACK", "SANDESH_PROJECT", "SANDESH_FORMAT")


def run_cli_subprocess(argv, xdg_data_home, env=None):
    """Run `python -m sandesh.cli <argv>` as a REAL subprocess.

    The environment baseline is the CURRENT process's environ with
    SANDESH_ADDRESS/WF_TRACK/SANDESH_PROJECT/SANDESH_FORMAT stripped (every
    call here passes --project/--format explicitly, and the env-caller rule
    under test needs those two unset unless a specific case re-adds one),
    XDG_DATA_HOME pinned to the test's temp store (CR-SAN-049: never the real
    store), and PYTHONPATH set so `-m sandesh.cli` resolves regardless of
    cwd. `env` (if given) is applied on top, so a case can re-add exactly one
    of the stripped vars. Returns (returncode, stdout, stderr).
    """
    base = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV_VARS}
    base["XDG_DATA_HOME"] = xdg_data_home
    base["PYTHONPATH"] = _REPO_ROOT
    if env:
        base.update(env)
    proc = subprocess.run(
        [sys.executable, "-m", "sandesh.cli", *argv],
        cwd=_REPO_ROOT, env=base, capture_output=True, text=True, timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


class _ThreadAsFixture(unittest.TestCase):
    """project 'Demo' (Mainline + Track 1 + Track 2 registered) with message
    #self.mid (Track 1 -> Mainline, body contains 'gateway timeout', already
    fetched); project 'Other' (Mainline - Other registered, wrong project for
    Demo); an independent subject-only reply chain ending at
    #self.subject_only_leaf."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sandesh-axi-thread-as-test-")
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp
        self._prev_env = {var: os.environ.pop(var, None) for var in _STRIPPED_ENV_VARS}

        sdb.setup("Demo")
        con = sdb.connect()
        store = sdb.store_dir("Demo")
        sdb.register(con, "Mainline - Demo", kind="mainline", project="Demo")
        sdb.register(con, "Track 1 - Demo", kind="track", project="Demo")
        sdb.register(con, "Track 2 - Demo", kind="track", project="Demo")
        self.mid = sdb.send(
            con, store, "Track 1 - Demo", to=["Mainline - Demo"],
            subject="incident-54", kind="request",
            body_text="Please investigate the gateway timeout quickly.",
            project="Demo")
        sdb.fetch(con, store, "Mainline - Demo")  # "already fetched (read)" per the fixture prose

        sdb.setup("Other")
        sdb.register(con, "Mainline - Other", kind="mainline", project="Other")

        # independent subject-only reply chain (no bodies anywhere); sdb.thread()
        # walks UP from msg_id to the root, so pass the LEAF to see both nodes.
        root = sdb.send(con, store, "Track 1 - Demo", to=["Mainline - Demo"],
                        subject="status check", project="Demo")
        self.subject_only_leaf = sdb.reply(con, store, root, "Mainline - Demo", project="Demo")

        con.close()

    def tearDown(self):
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        for var, prev in self._prev_env.items():
            if prev is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_thread(self, msg_id, extra_args=(), env=None, fmt="json"):
        return run_cli_subprocess(
            ["--format", fmt, "--project", "Demo", "thread", "--id", str(msg_id), *extra_args],
            self.tmp, env=env)


# =========================================================================== #
# Item 1 — AC3: a valid explicit --as returns the body, no withheld key.
# =========================================================================== #

class ThreadAsAuthorizedCallerTest(_ThreadAsFixture):

    def test_as_flag_with_valid_party_returns_body_and_no_withheld_key(self):
        rc, out, err = self.run_thread(self.mid, extra_args=["--as", "Mainline - Demo"])
        self.assertEqual(rc, 0, f"out={out!r} err={err!r}")
        axi = json.loads(out)["axi"]
        self.assertIn(
            "gateway timeout", axi["bodies"].get(str(self.mid), ""),
            f"--as 'Mainline - Demo' is a recipient of #{self.mid}; body must be shown; "
            f"bodies={axi.get('bodies')!r}")
        self.assertNotIn("withheld", axi, "nothing withheld -> no withheld key (payload shape unchanged)")


# =========================================================================== #
# Item 2 — AC4: no --as, no env caller -> withheld:1 + --as hint.
# =========================================================================== #

class ThreadAsAbsentCallerWithheldTest(_ThreadAsFixture):

    def test_no_as_no_env_caller_withholds_body_and_hints_as_flag(self):
        rc, out, err = self.run_thread(self.mid)
        self.assertEqual(rc, 0, f"metadata must remain readable; out={out!r} err={err!r}")
        axi = json.loads(out)["axi"]
        self.assertEqual(axi["bodies"], {}, f"no caller identity -> no bodies shown; got {axi['bodies']!r}")
        self.assertEqual(axi.get("withheld"), 1, f"exactly 1 message had a withheld body; axi={axi!r}")
        self.assertIn("help", axi, "a withheld body must carry help[] naming --as")
        self.assertTrue(
            any("--as" in h for h in axi["help"]),
            f"help[] must name the --as flag; got {axi.get('help')!r}")


# =========================================================================== #
# Item 3 — AC4: --as a registered non-party -> still withheld:1.
# =========================================================================== #

class ThreadAsNonPartyRegisteredCallerTest(_ThreadAsFixture):

    def test_as_registered_non_party_caller_withholds_body(self):
        rc, out, err = self.run_thread(self.mid, extra_args=["--as", "Track 2 - Demo"])
        self.assertEqual(rc, 0, f"out={out!r} err={err!r}")
        axi = json.loads(out)["axi"]
        self.assertEqual(
            axi.get("withheld"), 1,
            f"'Track 2 - Demo' is valid for the project but not sender/recipient; axi={axi!r}")
        self.assertNotIn(str(self.mid), axi.get("bodies", {}), "a non-party caller must never see the body")


# =========================================================================== #
# Item 4 — AC4: --as invalid for --project -> ok:false, thread's own envelope.
# =========================================================================== #

class ThreadAsInvalidExplicitAddressTest(_ThreadAsFixture):

    def test_as_invalid_address_for_project_fails_with_ok_false_and_names_address(self):
        rc, out, err = self.run_thread(self.mid, extra_args=["--as", "Mainline - Other"])
        axi = json.loads(out)["axi"]
        self.assertIs(axi["ok"], False, f"an --as invalid for --project must fail the verb; axi={axi!r}")
        self.assertNotEqual(rc, 0, f"a failed verb must exit non-zero; out={out!r} err={err!r}")
        self.assertEqual(
            axi["verb"], "thread",
            "the failure must come from thread's OWN validation (axi['verb']=='thread'), "
            f"not the top-level argparse usage-error path ('sandesh'); axi={axi!r}")
        self.assertIn(
            "Mainline - Other", axi["error"],
            f"the error must name the offending address; got {axi['error']!r}")


# =========================================================================== #
# Item 5 — AC4: an invalid env caller is treated as absent, NOT an error.
# =========================================================================== #

class ThreadAsEnvCallerInvalidForProjectTest(_ThreadAsFixture):

    def test_env_address_invalid_for_project_treated_as_absent_not_error(self):
        rc, out, err = self.run_thread(self.mid, env={"SANDESH_ADDRESS": "Mainline - Other"})
        self.assertEqual(
            rc, 0, f"an invalid ENV caller (no --as) must be treated as absent, not an error; "
                   f"out={out!r} err={err!r}")
        axi = json.loads(out)["axi"]
        self.assertIs(axi["ok"], True, f"must not fail the verb; axi={axi!r}")
        self.assertEqual(axi.get("withheld"), 1, f"the body must still count as withheld; axi={axi!r}")
        self.assertNotIn(str(self.mid), axi.get("bodies", {}))


# =========================================================================== #
# Item 6 — edge: an all-subject-only chain with no caller has no withheld key.
# =========================================================================== #

class ThreadAsSubjectOnlyChainNoWithheldTest(_ThreadAsFixture):

    def test_subject_only_chain_without_caller_has_no_withheld_key(self):
        rc, out, err = self.run_thread(self.subject_only_leaf)
        self.assertEqual(rc, 0, f"out={out!r} err={err!r}")
        axi = json.loads(out)["axi"]
        self.assertEqual(len(axi["chain"]), 2, f"expected the 2-node reply chain; chain={axi['chain']!r}")
        self.assertEqual(axi["bodies"], {}, "subject-only messages never have bodies")
        self.assertNotIn(
            "withheld", axi,
            f"nothing in this chain HAS a body, so nothing can be withheld; axi={axi!r}")


# =========================================================================== #
# Item 7 — toon mode decodes the same withheld:1.
# =========================================================================== #

class ThreadAsToonWithheldDecodeTest(_ThreadAsFixture):

    def test_toon_mode_decodes_withheld_count(self):
        rc, out, err = self.run_thread(self.mid, fmt="toon")
        self.assertEqual(rc, 0, f"out={out!r} err={err!r}")
        axi = _toon.decode(out)["axi"]
        self.assertEqual(axi.get("withheld"), 1, f"toon mode must decode the same withheld:1; axi={axi!r}")


if __name__ == "__main__":
    unittest.main()
