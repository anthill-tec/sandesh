"""_store_guard.py — the real-store guard + the shared ``TempStore`` mixin (CR-SAN-049 §S2).

WHY THIS EXISTS. ``<data_home>/sandesh/sandesh.db`` is the ONE global store shared by
every Sandesh project on this machine (Model B orchestrators, Crucible, …). The dev
shell exports ``XDG_DATA_HOME=~/.local/share`` globally, so "pointing at the real
store" is the NORMAL state of a test process — and the classic mitigation, a shell
prefix (``XDG_DATA_HOME=/tmp/x python tests/…``), is a trap: it is easy to forget and
invisible in the code. So the override lives HERE, in-process, and every test module
imports this module FIRST (``import tests._store_guard  # noqa``).

WHAT IT DOES AT IMPORT. It creates ONE per-process ``TemporaryDirectory(prefix=
"sandesh-tests-")`` under the SYSTEM temp root (``tempfile.gettempdir()`` — tmpfs on
the dev box, so nothing survives a reboot; never ``$HOME``, never the repo), registers
an ``atexit`` cleanup, and re-points ``XDG_DATA_HOME`` to it — UNLESS the incoming
value already resolves under the system temp root (a harness-supplied temp store is
respected), or ``SANDESH_TESTS_ALLOW_REAL_STORE=1`` is set (the explicit bypass; never
set in practice). ``sandesh_db.db_path()`` reads ``os.environ`` at call time, so import
order relative to ``sandesh`` does not matter.
"""

import atexit
import contextlib
import os
import tempfile


def temp_root():
    """The resolved system temp root — ``os.path.realpath(tempfile.gettempdir())``."""
    return os.path.realpath(tempfile.gettempdir())


def _under_temp_root(path):
    root = temp_root()
    real = os.path.realpath(path)
    return real == root or real.startswith(root + os.sep)


_GUARD = tempfile.TemporaryDirectory(prefix="sandesh-tests-", dir=temp_root())
GUARD_TMP = _GUARD.name


def _cleanup_guard():
    with contextlib.suppress(FileNotFoundError):  # already removed — nothing to do at exit
        _GUARD.cleanup()


atexit.register(_cleanup_guard)

_cur = os.environ.get("XDG_DATA_HOME")
if os.environ.get("SANDESH_TESTS_ALLOW_REAL_STORE") == "1":
    pass  # explicit bypass — leave the env untouched
elif _cur and _under_temp_root(_cur):
    pass  # already a temp-rooted store — respected
else:
    os.environ["XDG_DATA_HOME"] = GUARD_TMP


class TempStore:
    """``unittest.TestCase`` mixin giving each test its own throwaway store.

    ``setUp`` points ``XDG_DATA_HOME`` at a fresh ``sandesh-<testclass>-`` temp dir
    under the temp root; ``connect()`` opens and tracks a ``sandesh_db.connect()``
    connection; ``tearDown`` (also registered via ``addCleanup`` so it runs even if a
    subclass forgets ``super().tearDown()``) closes tracked connections, restores the
    previous env value and removes the dir. Cleanup is idempotent.
    """

    def setUp(self):
        parent_setup = getattr(super(), "setUp", None)
        if parent_setup is not None:
            parent_setup()
        self._prev_xdg = os.environ.get("XDG_DATA_HOME")
        self._tmp = tempfile.TemporaryDirectory(
            prefix=f"sandesh-{type(self).__name__.lower()}-", dir=temp_root())
        os.environ["XDG_DATA_HOME"] = self._tmp.name
        self._conns = []
        self._store_cleaned = False
        add_cleanup = getattr(self, "addCleanup", None)
        if add_cleanup is not None:
            add_cleanup(self._cleanup_store)

    def connect(self):
        """Open a ``sandesh_db.connect()`` connection against this test's store and track it."""
        from sandesh import sandesh_db as sdb
        con = sdb.connect()
        self._conns.append(con)
        return con

    def _cleanup_store(self):
        if self._store_cleaned:
            return
        self._store_cleaned = True
        for con in self._conns:
            # best-effort close during teardown — a connection already closed by
            # the test body raises ProgrammingError; nothing to do about it here
            with contextlib.suppress(Exception):
                con.close()
        self._conns = []
        if self._prev_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._prev_xdg
        with contextlib.suppress(FileNotFoundError):  # a test removed it already — fine
            self._tmp.cleanup()

    def tearDown(self):
        self._cleanup_store()
        parent_teardown = getattr(super(), "tearDown", None)
        if parent_teardown is not None:
            parent_teardown()
