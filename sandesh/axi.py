"""axi.py — the AXI envelope builder + serialisers (CR-SAN-047 §S2).

Presentation-layer helper: builds the ``{"axi": {...}}`` envelope described in
docs/research/PRD-axi-toon.md §4.1 and renders it as TOON (via the vendored
``sandesh._toon``) or JSON. Human mode has no envelope — ``render`` refuses it
and ``emit`` writes nothing. Per TOON v4.1 §9.1 an empty array renders as
``key: []`` (never the legacy ``key[0]:``), so ``warnings: []`` is the wire
form of a clean envelope. Stdlib + ``sandesh._toon`` only.
"""

import json
import sys

import sandesh._toon as _toon

FORMATS = ("human", "toon", "json")


class Envelope:
    """One AXI response: ``verb``/``ok`` + verb fields + context/help/warnings."""

    def __init__(self, verb, ok, fields=None, context=None, help=None, warnings=None):
        self.verb = verb
        self.ok = ok
        self.fields = dict(fields) if fields else {}
        self.context = dict(context) if context else {}
        self.help = list(help) if help else []
        self.warnings = list(warnings) if warnings else []

    def to_dict(self):
        """``{"axi": {verb, ok, ...fields, context, help?, warnings}}`` in that
        key order; ``help`` is omitted when empty, ``warnings`` always present."""
        axi = {"verb": self.verb, "ok": self.ok}
        axi.update(self.fields)
        axi["context"] = self.context
        if self.help:
            axi["help"] = self.help
        axi["warnings"] = self.warnings
        return {"axi": axi}


def render(env, fmt):
    """Serialise ``env`` as ``toon`` or ``json``; ``human`` has no envelope."""
    if fmt == "toon":
        return _toon.encode(env.to_dict())
    if fmt == "json":
        return json.dumps(env.to_dict(), ensure_ascii=False)
    if fmt == "human":
        raise ValueError("human mode has no envelope — render only 'toon' or 'json'")
    raise ValueError(f"unknown format {fmt!r} — expected one of: toon, json")


def emit(env, fmt, out=None):
    """Write ``render(env, fmt)`` + newline to ``out`` (default stdout); no-op
    for ``human``. Returns None."""
    if fmt == "human":
        return None
    stream = out if out is not None else sys.stdout
    stream.write(render(env, fmt) + "\n")
    return None


def error_envelope(verb, exc, context=None):
    """``ok:false`` envelope carrying ``error: str(exc)`` as its only field."""
    return Envelope(verb, False, {"error": str(exc)}, context)
