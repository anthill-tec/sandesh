"""test_axi_roundtrip_all_verbs.py — RED tests for CR-SAN-050 cycle 221 §S1/AC1,
against docs/changes/CR-SAN-050-wave16-verify-nits.md.

AC1 — `cli.AXI_FN` (the verb→handler registry `_run_machine` dispatches
through) is the single source of truth for "every verb the axi machine
surface covers". This file:

  1. Asserts the covered-argv set here equals `cli.AXI_FN`'s key set (so a
     new verb added to the registry without a matching argv case here is
     itself a failure — the CR's literal "a new verb cannot be missed"
     requirement).
  2. For EVERY verb in the registry, builds a minimal valid argv on a fresh,
     identically-provisioned fixture store, runs it once in `--format toon`
     and once in `--format json` (each on its OWN fresh store — mutating
     verbs like `register`/`archive`/`send` would otherwise leave the second
     run's store in a different state than the first saw), and asserts
     `_toon.decode(toon_out) == json.loads(json_out)` after normalising any
     timestamp-like substring (`\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}` ->
     `<TS>`) and the home view's `bin` field (-> `<BIN>`) — both vary between
     the two otherwise-identical builds (wall-clock second boundary /
     `sys.argv[0]` resolution) without indicating any real toon/json
     divergence.

Expected result: this is the AC1 BREADTH PIN, not new-feature RED — the
`_toon.decode(...) == json.loads(...)` oracle for every named verb already
holds today (shipped incrementally across cycles 205-208). Any verb that
fails here would be a genuine pre-existing divergence for GREEN to fix, not
an expected gap; the test method's own docstring/comments call out which (if
any) failed and why once a live run has been observed.

Fixture (fresh per build, `_build_store` — mirrors tests/test_axi_verbs.py's
`_BaseFixture` plus the eight admin/xproj/lifecycle pieces this cycle's argv
set needs): project 'Demo', 'Mainline - Demo' + 'Track 1 - Demo' +
'Track 2 - Demo' registered, message #1 ('Track 1 - Demo' -> 'Mainline -
Demo', subject/body 'hello world' / 'hello world body text'), message #2 (a
reply from 'Mainline - Demo'), admin 'TheAdmin' assigned. Verb-specific
`prep` callables push additional state a verb needs to be exercised
meaningfully (archived-first for unarchive/tombstone, granted-first for
revoke) — always run identically on both the toon-run store and the
json-run store.

Run (from the repo root):
  python3 -m unittest -v tests.test_axi_roundtrip_all_verbs
  or   PYTHONPATH=. .venv/bin/python tests/test_axi_roundtrip_all_verbs.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sandesh import sandesh_db as sdb
from sandesh import cli
from sandesh import _toon

_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def run_cli(argv, env=None):
    """Run cli.main(argv) in-process; returns (code, stdout, stderr).

    Mirrors tests/test_axi_verbs.py's run_cli.
    """
    prev = {}
    if env:
        for k, v in env.items():
            prev[k] = os.environ.get(k)
            os.environ[k] = v
    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = cli.main(argv)
                if rc is None:
                    rc = 0
            except SystemExit as exc:
                code = exc.code
                if code is None:
                    rc = 0
                elif isinstance(code, int):
                    rc = code
                else:
                    text = str(code)
                    err_buf.write(text if text.endswith("\n") else text + "\n")
                    rc = 1
    finally:
        if env:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return rc, out_buf.getvalue(), err_buf.getvalue()


def _normalize(obj):
    """Recursively replace timestamp-like substrings with '<TS>' and the
    home view's 'bin' field with '<BIN>' — both vary between two otherwise-
    identical fixture builds without indicating a real toon/json divergence."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "bin" and isinstance(v, str):
                out[k] = "<BIN>"
            else:
                out[k] = _normalize(v)
        return out
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    if isinstance(obj, str):
        return _TS_RE.sub("<TS>", obj)
    return obj


# --------------------------------------------------------------------------- #
# Fixture builder — a fresh, isolated XDG_DATA_HOME store per call.
# --------------------------------------------------------------------------- #

def _build_store(prep=None):
    """Provision a fresh 'Demo' project + two messages + an admin, run the
    optional `prep(con, store, mid, rid)` for verb-specific extra state, then
    close the connection. Returns (tmp_dir, mid, rid)."""
    tmp = tempfile.mkdtemp(prefix="sandesh-axi-roundtrip-test-")
    os.environ["XDG_DATA_HOME"] = tmp
    sdb.setup("Demo")
    con = sdb.connect()
    store = sdb.store_dir("Demo")
    sdb.register(con, "Mainline - Demo", kind="mainline", project="Demo")
    sdb.register(con, "Track 1 - Demo", kind="track", project="Demo")
    sdb.register(con, "Track 2 - Demo", kind="track", project="Demo")
    mid = sdb.send(con, store, "Track 1 - Demo", to=["Mainline - Demo"],
                   subject="hello world", body_text="hello world body text",
                   project="Demo")
    rid = sdb.reply(con, store, mid, "Mainline - Demo", project="Demo")
    sdb.assign_admin(con, "TheAdmin")
    if prep is not None:
        prep(con, store, mid, rid)
    con.close()
    return tmp, mid, rid


def _archive_prep(con, store, mid, rid):
    sdb.archive(con, "Demo", "Mainline - Demo")


def _grant_prep(con, store, mid, rid):
    sdb.grant_xproj(con, "Demo", "TheAdmin")


# --------------------------------------------------------------------------- #
# Per-verb minimal argv (argv_builder(mid, rid) -> (argv, env_or_None)) +
# an optional prep callable, one entry per cli.AXI_FN key.
# --------------------------------------------------------------------------- #

_VERB_ARGV = {
    "addressbook": (lambda mid, rid: (["addressbook", "--project", "Demo"], None), None),
    "inbox": (lambda mid, rid: (
        ["inbox", "--project", "Demo", "--to", "Mainline - Demo"], None), None),
    "fetch": (lambda mid, rid: (
        ["fetch", "--project", "Demo", "--to", "Mainline - Demo"], None), None),
    "send": (lambda mid, rid: (
        ["send", "--project", "Demo", "--from", "Track 1 - Demo",
         "--to", "Mainline - Demo", "--subject", "second message"], None), None),
    "reply": (lambda mid, rid: (
        ["reply", "--project", "Demo", "--to-msg", str(mid),
         "--from", "Track 2 - Demo"], None), None),
    "register": (lambda mid, rid: (
        ["register", "--project", "Demo", "--address", "Track 3 - Demo",
         "--kind", "track"], None), None),
    "unregister": (lambda mid, rid: (
        ["unregister", "--project", "Demo", "--address", "Track 2 - Demo",
         "--as", "Mainline - Demo"], None), None),
    "archive": (lambda mid, rid: (
        ["archive", "--project", "Demo", "--by", "Mainline - Demo"], None), None),
    "unarchive": (lambda mid, rid: (
        ["unarchive", "--project", "Demo", "--by", "Mainline - Demo"], None),
        _archive_prep),
    "search": (lambda mid, rid: (["search", "hello", "--to", "Mainline - Demo"], None), None),
    "thread": (lambda mid, rid: (["thread", "--project", "Demo", "--id", str(mid)], None), None),
    "projects": (lambda mid, rid: (["projects"], None), None),
    "setup": (lambda mid, rid: (["setup", "--project", "NewProj"], None), None),
    "grant": (lambda mid, rid: (
        ["grant", "--cross-project", "--project", "Demo", "--by", "TheAdmin"], None), None),
    "revoke": (lambda mid, rid: (
        ["revoke", "--cross-project", "--project", "Demo", "--by", "TheAdmin"], None),
        _grant_prep),
    "tombstone": (lambda mid, rid: (
        ["tombstone", "--project", "Demo", "--by", "TheAdmin", "--dry-run"], None),
        _archive_prep),
    "status": (lambda mid, rid: (
        ["status"], {"SANDESH_PROJECT": "Demo", "SANDESH_ADDRESS": "Mainline - Demo"}), None),
    "notify": (lambda mid, rid: (
        ["notify", "--project", "Demo", "--to", "Mainline - Demo", "--timeout", "1"], None), None),
    "init": (lambda mid, rid: (["init", "--check"], None), None),
    "migrate": (lambda mid, rid: (["migrate", "--status"], None), None),
    "consolidate": (lambda mid, rid: (["consolidate"], None), None),
    "reindex": (lambda mid, rid: (["reindex"], None), None),
}


def _run_one_format(fmt, argv_builder, prep):
    tmp, mid, rid = _build_store(prep)
    try:
        argv, env = argv_builder(mid, rid)
        rc, out, err = run_cli(["--format", fmt] + argv, env=env)
        # Collapse THIS build's own temp-dir path (e.g. the 'setup' verb's
        # 'store' field) before decoding — two separate fresh builds get two
        # different tmp dirs, which is not a real toon/json divergence.
        out = out.replace(tmp, "<TMP>")
        return rc, out, err
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class _AmbientEnvFixture(unittest.TestCase):
    """Saves/restores the ambient env vars every build touches; each verb
    case manages its OWN XDG_DATA_HOME/temp dir via `_build_store`."""

    def setUp(self):
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._prev_format = os.environ.pop("SANDESH_FORMAT", None)
        self._prev_address = os.environ.pop("SANDESH_ADDRESS", None)
        self._prev_project = os.environ.pop("SANDESH_PROJECT", None)

    def tearDown(self):
        for var, prev in (("XDG_DATA_HOME", self._prev_xdg),
                          ("SANDESH_FORMAT", self._prev_format),
                          ("SANDESH_ADDRESS", self._prev_address),
                          ("SANDESH_PROJECT", self._prev_project)):
            if prev is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prev


# =========================================================================== #
# AC1 — argv coverage + the per-verb toon/json parity breadth pin.
# =========================================================================== #

class ArgvCoverageMatchesRegistryTest(_AmbientEnvFixture):

    def test_argv_case_keys_equal_axi_fn_registry_keys(self):
        registry_verbs = set(cli.AXI_FN.keys())
        covered_verbs = set(_VERB_ARGV.keys())
        self.assertEqual(
            covered_verbs, registry_verbs,
            f"the argv case table must cover EXACTLY cli.AXI_FN's key set "
            f"(a verb in the registry with no argv case must fail this test); "
            f"missing from cases={sorted(registry_verbs - covered_verbs)}, "
            f"extra in cases={sorted(covered_verbs - registry_verbs)}")


class FormatAfterVerbAllVerbsTest(_AmbientEnvFixture):
    """`--format` placed AFTER the verb must be accepted by every verb — the
    ten built without the `common` parent (search, grant, revoke, archive,
    unarchive, tombstone, migrate, consolidate, reindex, init) included — and
    yield that verb's own envelope, exactly as the before-the-verb form does."""

    def test_format_after_the_verb_yields_the_verbs_envelope_for_every_verb(self):
        self.assertEqual(set(_VERB_ARGV), set(cli.AXI_FN))
        for verb, (argv_builder, prep) in sorted(_VERB_ARGV.items()):
            with self.subTest(verb=verb):
                tmp, mid, rid = _build_store(prep)
                try:
                    argv, env = argv_builder(mid, rid)
                    rc, out, err = run_cli(argv + ["--format", "json"], env=env)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertNotEqual(
                    rc, 2, f"{verb}: --format after the verb was rejected; out={out!r} err={err!r}")
                axi = json.loads(out)["axi"]
                self.assertEqual(axi["verb"], verb)


class AllVerbsToonJsonParityTest(_AmbientEnvFixture):
    """AC1 — every `cli.AXI_FN` verb's toon output decodes to the same dict as
    its json output (the literal AC2-from-CR-047 oracle), per verb."""

    def test_toon_json_parity_for_every_registered_verb(self):
        registry_verbs = sorted(cli.AXI_FN.keys())
        missing = [v for v in registry_verbs if v not in _VERB_ARGV]
        self.assertEqual(missing, [], f"no argv case for verb(s): {missing}")

        failures = []
        for verb in registry_verbs:
            argv_builder, prep = _VERB_ARGV[verb]
            rc_t, out_t, err_t = _run_one_format("toon", argv_builder, prep)
            rc_j, out_j, err_j = _run_one_format("json", argv_builder, prep)
            try:
                toon_decoded = _normalize(_toon.decode(out_t))
            except Exception as exc:
                failures.append(
                    f"{verb}: toon decode failed: {exc}; "
                    f"rc={rc_t} out={out_t!r} err={err_t!r}")
                continue
            try:
                json_decoded = _normalize(json.loads(out_j))
            except Exception as exc:
                failures.append(
                    f"{verb}: json decode failed: {exc}; "
                    f"rc={rc_j} out={out_j!r} err={err_j!r}")
                continue
            if toon_decoded != json_decoded:
                failures.append(
                    f"{verb}: toon != json (normalised)\n"
                    f"  toon (rc={rc_t})={toon_decoded!r}\n"
                    f"  json (rc={rc_j})={json_decoded!r}")

        self.assertEqual(
            failures, [],
            "AC1 breadth pin — toon/json parity failed for:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main(verbosity=2)
