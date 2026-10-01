"""test_axi_envelope.py — AC2 (synthetic, no store): the AXI envelope builder
and serialisers (§S2, CR-SAN-047).

  python3 -m unittest -v   (from the repo root)   or   python3 tests/test_axi_envelope.py

Exercises sandesh/axi.py directly against hand-built Envelope instances (no
fixture store needed for this cycle — the per-verb/fixture-store oracle is a
later cycle). sandesh/axi.py and sandesh/_toon.py don't exist yet — the
ImportError below is a valid RED (Mode 1: not-yet-existing SUT symbols).
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root — CR-SAN-049 guard bootstrap
import tests._store_guard  # noqa: F401 — real-store guard (CR-SAN-049): must be the first non-bootstrap import
import io
import json
import unittest

from sandesh import _toon
from sandesh.axi import Envelope, emit, error_envelope, render


def _addressbook_envelope(**overrides):
    fields = {
        "participants": [{"address": "Mainline - Demo", "listening": True}],
        "listening": "1/1",
    }
    fields.update(overrides.pop("fields", {}))
    kwargs = {"context": {"project": "Demo"}}
    kwargs.update(overrides)
    return Envelope("addressbook", True, fields, **kwargs)


class EnvelopeToDictTest(unittest.TestCase):
    def test_to_dict_shape_help_omitted_warnings_always_present(self):
        env = _addressbook_envelope()
        expected = {
            "axi": {
                "verb": "addressbook",
                "ok": True,
                "participants": [{"address": "Mainline - Demo", "listening": True}],
                "listening": "1/1",
                "context": {"project": "Demo"},
                "warnings": [],
            }
        }
        d = env.to_dict()
        self.assertEqual(d, expected)
        self.assertNotIn("help", d["axi"])
        self.assertIn("warnings", d["axi"])

    def test_to_dict_field_order_is_verb_ok_fields_context_warnings(self):
        env = _addressbook_envelope()
        d = env.to_dict()
        self.assertEqual(
            list(d["axi"].keys()),
            ["verb", "ok", "participants", "listening", "context", "warnings"],
        )

    def test_to_dict_includes_help_and_warnings_when_both_provided(self):
        env = _addressbook_envelope(help=["fetch --to <addr>"], warnings=["w1"])
        d = env.to_dict()
        self.assertEqual(d["axi"]["help"], ["fetch --to <addr>"])
        self.assertEqual(d["axi"]["warnings"], ["w1"])
        self.assertEqual(
            list(d["axi"].keys()),
            ["verb", "ok", "participants", "listening", "context", "help", "warnings"],
        )


class RenderRoundTripTest(unittest.TestCase):
    def test_render_toon_decodes_via_vendored_codec_to_the_same_dict(self):
        env = _addressbook_envelope()
        text = render(env, "toon")
        self.assertIsInstance(text, str)
        self.assertEqual(_toon.decode(text), env.to_dict())

    def test_render_json_round_trips_via_json_loads(self):
        env = _addressbook_envelope()
        text = render(env, "json")
        self.assertEqual(json.loads(text), env.to_dict())

    def test_render_human_raises_value_error(self):
        env = _addressbook_envelope()
        with self.assertRaises(ValueError):
            render(env, "human")

    def test_render_unknown_format_raises_value_error_naming_toon_and_json(self):
        env = _addressbook_envelope()
        with self.assertRaises(ValueError) as ctx:
            render(env, "xml")
        message = str(ctx.exception)
        self.assertIn("toon", message)
        self.assertIn("json", message)


class ToonWireFormatTest(unittest.TestCase):
    def test_uniform_object_array_renders_as_table_header_plus_one_row(self):
        env = _addressbook_envelope()
        text = render(env, "toon")
        lines = [line.strip() for line in text.splitlines()]
        self.assertIn("participants[1]{address,listening}:", lines)
        header_idx = lines.index("participants[1]{address,listening}:")
        row = lines[header_idx + 1]
        self.assertIn("Mainline - Demo", row)
        self.assertIn("true", row)
        self.assertNotIn("True", row)

    def test_empty_warnings_list_renders_as_bracketed_zero_length_line(self):
        env = Envelope(
            "addressbook", True, {"participants": [], "listening": "0/0"}, {"project": "Demo"}
        )
        text = render(env, "toon")
        stripped_lines = [line.rstrip() for line in text.splitlines()]
        # 2-space indent: warnings is a direct child of the top-level "axi:" object.
        self.assertIn("  warnings: []", stripped_lines)

    def test_string_with_comma_or_colon_is_json_quoted(self):
        env = _addressbook_envelope(fields={"note": "a: b, c"})
        text = render(env, "toon")
        self.assertIn('"a: b, c"', text)

    def test_bool_renders_bare_true_not_python_capitalised(self):
        env = _addressbook_envelope()
        text = render(env, "toon")
        self.assertIn("ok: true", text)
        self.assertNotIn("ok: True", text)
        self.assertNotIn("True", text)


class ErrorEnvelopeTest(unittest.TestCase):
    def _send_error_envelope(self):
        exc = ValueError("no recipients (after excluding the sender)")
        context = {"project": "Demo", "address": "Track 1 - Demo"}
        return error_envelope("send", exc, context)

    def test_error_envelope_is_not_ok_and_carries_the_error_field(self):
        env = self._send_error_envelope()
        self.assertIs(env.ok, False)
        self.assertEqual(env.fields, {"error": "no recipients (after excluding the sender)"})
        self.assertEqual(env.warnings, [])

    def test_error_envelope_to_dict_shape(self):
        env = self._send_error_envelope()
        d = env.to_dict()
        self.assertEqual(
            d,
            {
                "axi": {
                    "verb": "send",
                    "ok": False,
                    "error": "no recipients (after excluding the sender)",
                    "context": {"project": "Demo", "address": "Track 1 - Demo"},
                    "help": ["Resolve the reported error, then retry `sandesh send`."],
                    "warnings": [],
                }
            },
        )

    def test_error_envelope_renders_ok_false_and_error_message_in_toon(self):
        env = self._send_error_envelope()
        text = render(env, "toon")
        self.assertIn("ok: false", text)
        self.assertIn("error: no recipients (after excluding the sender)", text)


class EmitTest(unittest.TestCase):
    def test_emit_toon_writes_exactly_the_rendered_text_plus_one_newline(self):
        env = _addressbook_envelope()
        out = io.StringIO()
        emit(env, "toon", out=out)
        self.assertEqual(out.getvalue(), render(env, "toon") + "\n")

    def test_emit_human_writes_nothing_and_returns_none(self):
        env = _addressbook_envelope()
        out = io.StringIO()
        result = emit(env, "human", out=out)
        self.assertEqual(out.getvalue(), "")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
