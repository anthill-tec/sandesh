"""test_toon_vendor.py — AC1: the vendored TOON codec (§S1, CR-SAN-047).

  python3 -m unittest -v   (from the repo root)   or   python3 tests/test_toon_vendor.py

sandesh/_toon.py is expected to be a verbatim copy of the fleet's TOON codec
(~/.crucible/clients/toon.py), prefixed with a provenance header ending in the
sentinel line ``# --- end provenance ---``. sandesh/_toon_provenance.py is
expected to hold TOON_SOURCE_SHA256 = sha256(everything after that sentinel).
The tests verify that provenance contract and the encoder/decoder behavior.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import hashlib
import os
import sys
import unittest

from sandesh import _toon
from sandesh._toon_provenance import TOON_SOURCE_SHA256

_PROVENANCE_END_MARKER = "# --- end provenance ---"
_SOURCE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sandesh", "_toon.py"
)


def _split_header_and_body():
    """Return (header_text, body_text) split on the first line that equals
    the provenance-end sentinel (the sentinel line itself belongs to the
    header)."""
    with open(_SOURCE_PATH, "rb") as f:
        raw = f.read()
    lines = raw.decode("utf-8").splitlines(keepends=True)
    header_end = None
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") == _PROVENANCE_END_MARKER:
            header_end = i + 1
            break
    if header_end is None:
        raise AssertionError(
            f"provenance end marker {_PROVENANCE_END_MARKER!r} not found in {_SOURCE_PATH}"
        )
    return "".join(lines[:header_end]), "".join(lines[header_end:])


class ToonVendorProvenanceTest(unittest.TestCase):
    def test_source_hash_minus_provenance_header_matches_recorded_sha256(self):
        _header, body = _split_header_and_body()
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self.assertEqual(digest, TOON_SOURCE_SHA256)

    def test_provenance_header_hash_would_change_if_body_tampered(self):
        # Regression pin: prove the oracle is sensitive to body content, not
        # a vacuous "any string equals any string" comparison.
        _header, body = _split_header_and_body()
        tampered_digest = hashlib.sha256((body + "\n# tampered\n").encode("utf-8")).hexdigest()
        self.assertNotEqual(tampered_digest, TOON_SOURCE_SHA256)


class ToonVendorApiTest(unittest.TestCase):
    def test_encode_and_decode_are_callable(self):
        self.assertTrue(callable(_toon.encode))
        self.assertTrue(callable(_toon.decode))

    def test_encode_decode_round_trips_nested_structure(self):
        payload = {"a": 1, "b": [1, 2], "c": {"d": "x y"}}
        encoded = _toon.encode(payload)
        self.assertIsInstance(encoded, str)
        decoded = _toon.decode(encoded)
        self.assertEqual(decoded, payload)


if __name__ == "__main__":
    unittest.main()
