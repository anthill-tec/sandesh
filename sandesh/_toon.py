# Vendored TOON codec — verbatim copy (CR-SAN-047 §S1). DO NOT EDIT.
#
# Source:  /home/antonyj/.crucible/clients/toon.py
# SHA256:  8c04f1441d5208ee67271742782eee2e6e689d24f73a7d48805a166bf166d523
# Copied:  2026-09-26
# CR:      CR-SAN-047 (AXI/TOON CLI envelope)
#
# Everything after the sentinel line below is the byte-for-byte source;
# tests/test_toon_vendor.py hashes it against sandesh/_toon_provenance.py.
# --- end provenance ---
"""Fleet-shared Python TOON codec — full official-spec implementation.

A faithful Python port of the first-party TypeScript reference
(``@toon-format/toon`` 4.x, ``node_modules/@toon-format/toon/dist/index.mjs``),
replacing the retired 4-construct Crucible subset (CR-CRU-046 §S2, Option A —
PyPI ``toon-format`` 0.1.0 is a NotImplementedError stub, so this port IS the
Python implementation). Cross-validated both directions against the reference
by the §S4 oracle in the bun suite: every client envelope must decode via
``@toon-format/toon``, and official-encoded output must decode via this module.

Public API (unchanged surface — loaded by path from ``_crucible_axi._toon()``
and the decode-instrument test files):
    encode(obj) -> str    Python dict -> TOON text (reference DEFAULT form:
                          indentSize 2, comma delimiter)
    decode(text) -> dict  TOON text -> Python value (strict mode, the
                          reference decoder's default)

Constructs (the full official grammar):
    scalar lines, nested objects, inline primitive arrays ``key[N]: a,b``,
    list arrays with ``- `` item markers, tabular uniform-object arrays
    ``key[N]{cols}:``, keyed tabular objects ``key[N:]{cols}:``, quoted
    keys/strings with the full MUST-quote set and escape sequences, comment
    lines, and the tab/pipe delimiter variants on decode.
"""
from __future__ import annotations

import math
import re
from typing import Any

_DELIMITERS = (",", "\t", "|")
_DEFAULT_DELIMITER = ","
_INDENT_SIZE = 2

_NUMERIC_LITERAL = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:e[+-]?\d+)?$", re.IGNORECASE)
_NUMERIC_LIKE = re.compile(r"^[+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?$", re.IGNORECASE)
_UNQUOTED_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")
_LEADING_WHITESPACE = re.compile(r"^[ \t]*")
_BRACKET_LENGTH = re.compile(r"^(?:0|[1-9]\d*)$")


class ToonDecodeError(ValueError):
    """Raised when TOON text cannot be parsed (mirrors the reference's
    ``ToonDecodeError``); carries the 1-based source line when known."""

    def __init__(self, message: str, line: int | None = None, source: str | None = None):
        prefix = f"Line {line}: " if line is not None else ""
        super().__init__(prefix + message)
        self.line = line
        self.source = source


# ── shared string utilities (src/shared/string-utils.ts) ─────────────────────

def _trim_spaces(value: str) -> str:
    """Trim surrounding ASCII spaces (U+0020) ONLY — never the full
    whitespace set (tabs may be delimiters, NBSP is token content)."""
    return value.strip(" ")


def _escape_string(value: str) -> str:
    out = (value.replace("\\", "\\\\").replace('"', '\\"')
           .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return _CONTROL_CHARS.sub(lambda m: f"\\u{ord(m.group(0)):04x}", out)


def _unescape_string(value: str) -> str:
    unescaped: list[str] = []
    i = 0
    simple = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}
    while i < len(value):
        if value[i] == "\\":
            if i + 1 >= len(value):
                raise ToonDecodeError("Invalid escape sequence: backslash at end of string")
            nxt = value[i + 1]
            if nxt in simple:
                unescaped.append(simple[nxt])
                i += 2
                continue
            if nxt == "u":
                if i + 6 > len(value):
                    raise ToonDecodeError(
                        f'Invalid escape sequence: truncated \\u escape at "{value[i:i + 6]}"')
                hexs = value[i + 2:i + 6]
                if not re.fullmatch(r"[0-9a-fA-F]{4}", hexs):
                    raise ToonDecodeError(
                        f'Invalid escape sequence: \\u must be followed by 4 hex digits, got "{hexs}"')
                code_unit = int(hexs, 16)
                if 0xD800 <= code_unit <= 0xDFFF:
                    raise ToonDecodeError(
                        f"Invalid escape sequence: \\u{hexs} is a lone surrogate. "
                        f"Supplementary code points MUST appear as literal UTF-8")
                unescaped.append(chr(code_unit))
                i += 6
                continue
            raise ToonDecodeError(f"Invalid escape sequence: \\{nxt}")
        unescaped.append(value[i])
        i += 1
    return "".join(unescaped)


def _find_closing_quote(content: str, start: int) -> int:
    i = start + 1
    while i < len(content):
        if content[i] == "\\" and i + 1 < len(content):
            i += 2
            continue
        if content[i] == '"':
            return i
        i += 1
    return -1


def _find_unquoted_char(content: str, char: str, start: int = 0) -> int:
    in_quotes = False
    i = start
    while i < len(content):
        c = content[i]
        if c == "\\" and i + 1 < len(content) and in_quotes:
            i += 2
            continue
        if c == '"':
            in_quotes = not in_quotes
            i += 1
            continue
        if c == char and not in_quotes:
            return i
        i += 1
    return -1


# ── literal utilities (src/shared/literal-utils.ts) ──────────────────────────

def _is_boolean_or_null_literal(token: str) -> bool:
    return token in ("true", "false", "null")


def _is_numeric_literal(token: str) -> bool:
    if not token or not _NUMERIC_LITERAL.match(token):
        return False
    try:
        return math.isfinite(float(token))
    except (OverflowError, ValueError):
        return False


# ── encode: normalization (src/encode/normalize.ts) ──────────────────────────

def _assert_no_lone_surrogate(value: str, context: str) -> None:
    for index, ch in enumerate(value):
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            raise TypeError(
                f"Cannot encode {context} containing an unpaired surrogate "
                f"U+{code:04X} at index {index}")


def _normalize_value(value: Any) -> Any:
    if value is None or value is True or value is False:
        return value
    if isinstance(value, str):
        _assert_no_lone_surrogate(value, "string value")
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value == 0.0:
            return 0.0  # -0.0 -> 0 per the reference
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict = {}
        for key, val in value.items():
            key_str = key if isinstance(key, str) else str(key)
            _assert_no_lone_surrogate(key_str, "object key")
            normalized[key_str] = _normalize_value(val)
        return normalized
    return None  # non-JSON-model values encode as null, per the reference


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (str, bool, int, float))


def _is_array(value: Any) -> bool:
    return isinstance(value, list)


def _is_object(value: Any) -> bool:
    return isinstance(value, dict)


def _is_array_of_primitives(value: list) -> bool:
    return all(_is_primitive(item) for item in value)


def _is_array_of_arrays(value: list) -> bool:
    return all(_is_array(item) for item in value)


def _is_array_of_objects(value: list) -> bool:
    return all(_is_object(item) for item in value)


# ── encode: primitives + quoting (src/encode/primitives.ts, validation.ts) ──

def _is_safe_unquoted(value: str, delimiter: str = _DEFAULT_DELIMITER) -> bool:
    if not value:
        return False
    if value[0] in " \t" or value[-1] in " \t":
        return False
    if _is_boolean_or_null_literal(value) or _NUMERIC_LIKE.match(value):
        return False
    if ":" in value or '"' in value or "\\" in value:
        return False
    if any(c in value for c in "[]{}"):
        return False
    if _CONTROL_CHARS.search(value):
        return False
    if delimiter in value:
        return False
    if value.startswith("-") or value.startswith("#"):
        return False
    return True


def _number_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e16:
        return str(int(value))  # JS String(2.0) == "2"
    return str(value)


def _encode_primitive(value: Any, delimiter: str) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _number_text(value)
    return _encode_string_literal(value, delimiter)


def _encode_string_literal(value: str, delimiter: str = _DEFAULT_DELIMITER) -> str:
    if _is_safe_unquoted(value, delimiter):
        return value
    return f'"{_escape_string(value)}"'


def _encode_key(key: str) -> str:
    if _UNQUOTED_KEY.match(key):
        return key
    return f'"{_escape_string(key)}"'


def _encode_and_join_primitives(values: list, delimiter: str) -> str:
    return delimiter.join(_encode_primitive(v, delimiter) for v in values)


def _format_header(length: int, key: str | None = None,
                   fields: list | None = None, delimiter: str = _DEFAULT_DELIMITER,
                   keyed: bool = False) -> str:
    header = _encode_key(key) if key is not None else ""
    header += (f"[{length}{':' if keyed else ''}"
               f"{delimiter if delimiter != _DEFAULT_DELIMITER else ''}]")
    if fields:
        header += "{" + _format_field_segment(fields, delimiter) + "}"
    return header + ":"


def _format_field_segment(fields: list, delimiter: str) -> str:
    parts = []
    for name, children in fields:
        part = _encode_key(name)
        if children:
            part += "{" + _format_field_segment(children, delimiter) + "}"
        parts.append(part)
    return delimiter.join(parts)


# ── encode: tabular classification (src/encode/tabular.ts) ───────────────────
# Field nodes are (name, children) tuples; children is None for leaf fields.

def _extract_tabular_fields(rows: list) -> list | None:
    if not rows:
        return None
    first_keys = list(rows[0].keys())
    if not first_keys:
        return None
    for row in rows:
        if len(row) != len(first_keys):
            return None
        for key in first_keys:
            if key not in row:
                return None
    field_nodes = []
    for key in first_keys:
        node = _classify_column(key, [row[key] for row in rows])
        if node is None:
            return None
        field_nodes.append(node)
    return field_nodes


def _classify_column(name: str, values: list) -> tuple | None:
    if all(_is_primitive(v) for v in values):
        return (name, None)
    if not all(_is_object(v) and len(v) > 0 for v in values):
        return None
    children = _extract_tabular_fields(values)
    if children is None:
        return None
    return (name, children)


def _extract_keyed_tabular_fields(value: dict) -> list | None:
    entry_values = list(value.values())
    if len(entry_values) < 2:
        return None
    if not all(_is_object(v) and len(v) > 0 for v in entry_values):
        return None
    return _extract_tabular_fields(entry_values)


def _collect_row_leaves(row: dict, fields: list) -> list:
    leaves: list = []
    for name, children in fields:
        value = row[name]
        if children:
            leaves.extend(_collect_row_leaves(value, children))
        else:
            leaves.append(value)
    return leaves


def _count_leaf_fields(fields: list) -> int:
    return sum(_count_leaf_fields(children) if children else 1
               for _, children in fields)


# ── encode: line emitters (src/encode/encoders.ts) ───────────────────────────

def _indented_line(depth: int, content: str) -> str:
    return " " * (_INDENT_SIZE * depth) + content


def _indented_list_item(depth: int, content: str) -> str:
    return _indented_line(depth, "- " + content)


def _encode_json_value(value: Any, delimiter: str, depth: int) -> list[str]:
    if _is_primitive(value):
        encoded = _encode_primitive(value, delimiter)
        return [encoded] if encoded != "" else []
    if _is_array(value):
        return _encode_array_lines(None, value, depth, delimiter)
    if _is_object(value):
        keyed_fields = _extract_keyed_tabular_fields(value)
        if keyed_fields:
            return _encode_keyed_object_lines(None, value, keyed_fields, depth, delimiter)
        return _encode_object_lines(value, depth, delimiter)
    return []


def _encode_object_lines(value: dict, depth: int, delimiter: str) -> list[str]:
    lines: list[str] = []
    for key, val in value.items():
        lines.extend(_encode_key_value_pair_lines(key, val, depth, delimiter))
    return lines


def _encode_key_value_pair_lines(key: str, value: Any, depth: int, delimiter: str) -> list[str]:
    encoded_key = _encode_key(key)
    if _is_primitive(value):
        return [_indented_line(depth, f"{encoded_key}: {_encode_primitive(value, delimiter)}")]
    if _is_array(value):
        return _encode_array_lines(key, value, depth, delimiter)
    if _is_object(value):
        keyed_fields = _extract_keyed_tabular_fields(value)
        if keyed_fields:
            return _encode_keyed_object_lines(key, value, keyed_fields, depth, delimiter)
        lines = [_indented_line(depth, f"{encoded_key}:")]
        if value:
            lines.extend(_encode_object_lines(value, depth + 1, delimiter))
        return lines
    return []


def _encode_keyed_object_lines(key: str | None, value: dict, fields: list,
                               depth: int, delimiter: str) -> list[str]:
    entries = list(value.items())
    lines = [_indented_line(depth, _format_header(
        len(entries), key=key, fields=fields, delimiter=delimiter, keyed=True))]
    lines.extend(_encode_keyed_entry_rows_lines(entries, fields, depth + 1, delimiter))
    return lines


def _encode_keyed_entry_rows_lines(entries: list, fields: list, depth: int,
                                   delimiter: str) -> list[str]:
    return [
        _indented_line(
            depth,
            f"{_encode_key(entry_key)}: "
            f"{_encode_and_join_primitives(_collect_row_leaves(entry_value, fields), delimiter)}")
        for entry_key, entry_value in entries
    ]


def _encode_array_lines(key: str | None, value: list, depth: int, delimiter: str) -> list[str]:
    if not value:
        content = f"{_encode_key(key)}: []" if key is not None else "[]"
        return [_indented_line(depth, content)]
    if _is_array_of_primitives(value):
        return [_indented_line(depth, _encode_inline_array_line(value, delimiter, key))]
    if _is_array_of_arrays(value) and all(_is_array_of_primitives(arr) for arr in value):
        lines = [_indented_line(depth, _format_header(len(value), key=key, delimiter=delimiter))]
        lines.extend(
            _indented_list_item(depth + 1, _encode_inline_array_line(arr, delimiter))
            for arr in value)
        return lines
    if _is_array_of_objects(value):
        fields = _extract_tabular_fields(value)
        if fields:
            lines = [_indented_line(depth, _format_header(
                len(value), key=key, fields=fields, delimiter=delimiter))]
            lines.extend(_write_tabular_rows_lines(value, fields, depth + 1, delimiter))
            return lines
    return _encode_mixed_array_as_list_items_lines(key, value, depth, delimiter)


def _encode_inline_array_line(values: list, delimiter: str, key: str | None = None) -> str:
    header = _format_header(len(values), key=key, delimiter=delimiter)
    if not values:
        return header
    return f"{header} {_encode_and_join_primitives(values, delimiter)}"


def _write_tabular_rows_lines(rows: list, fields: list, depth: int, delimiter: str) -> list[str]:
    return [
        _indented_line(depth, _encode_and_join_primitives(
            _collect_row_leaves(row, fields), delimiter))
        for row in rows
    ]


def _encode_mixed_array_as_list_items_lines(key: str | None, items: list, depth: int,
                                            delimiter: str) -> list[str]:
    lines = [_indented_line(depth, _format_header(len(items), key=key, delimiter=delimiter))]
    for item in items:
        lines.extend(_encode_list_item_value_lines(item, depth + 1, delimiter))
    return lines


def _encode_list_item_value_lines(value: Any, depth: int, delimiter: str) -> list[str]:
    if _is_primitive(value):
        return [_indented_list_item(depth, _encode_primitive(value, delimiter))]
    if _is_array(value):
        if _is_array_of_primitives(value):
            return [_indented_list_item(depth, _encode_inline_array_line(value, delimiter))]
        lines = [_indented_list_item(depth, _format_header(len(value), delimiter=delimiter))]
        for item in value:
            lines.extend(_encode_list_item_value_lines(item, depth + 1, delimiter))
        return lines
    if _is_object(value):
        return _encode_object_as_list_item_lines(value, depth, delimiter)
    return []


def _encode_object_as_list_item_lines(obj: dict, depth: int, delimiter: str) -> list[str]:
    if not obj:
        return [_indented_line(depth, "-")]
    entries = list(obj.items())
    first_key, first_value = entries[0]
    rest_entries = entries[1:]
    lines: list[str] = []

    if _is_array(first_value) and first_value and _is_array_of_objects(first_value):
        fields = _extract_tabular_fields(first_value)
        if fields:
            lines.append(_indented_list_item(depth, _format_header(
                len(first_value), key=first_key, fields=fields, delimiter=delimiter)))
            lines.extend(_write_tabular_rows_lines(first_value, fields, depth + 2, delimiter))
            if rest_entries:
                lines.extend(_encode_object_lines(dict(rest_entries), depth + 1, delimiter))
            return lines

    if _is_object(first_value):
        keyed_fields = _extract_keyed_tabular_fields(first_value)
        if keyed_fields:
            keyed_entries = list(first_value.items())
            lines.append(_indented_list_item(depth, _format_header(
                len(keyed_entries), key=first_key, fields=keyed_fields,
                delimiter=delimiter, keyed=True)))
            lines.extend(_encode_keyed_entry_rows_lines(
                keyed_entries, keyed_fields, depth + 2, delimiter))
            if rest_entries:
                lines.extend(_encode_object_lines(dict(rest_entries), depth + 1, delimiter))
            return lines

    encoded_key = _encode_key(first_key)
    if _is_primitive(first_value):
        lines.append(_indented_list_item(
            depth, f"{encoded_key}: {_encode_primitive(first_value, delimiter)}"))
    elif _is_array(first_value):
        if not first_value:
            lines.append(_indented_list_item(depth, f"{encoded_key}: []"))
        elif _is_array_of_primitives(first_value):
            lines.append(_indented_list_item(
                depth, encoded_key + _encode_inline_array_line(first_value, delimiter)))
        else:
            lines.append(_indented_list_item(
                depth, encoded_key + _format_header(len(first_value), delimiter=delimiter)))
            for item in first_value:
                lines.extend(_encode_list_item_value_lines(item, depth + 2, delimiter))
    elif _is_object(first_value):
        lines.append(_indented_list_item(depth, f"{encoded_key}:"))
        if first_value:
            lines.extend(_encode_object_lines(first_value, depth + 2, delimiter))
    if rest_entries:
        lines.extend(_encode_object_lines(dict(rest_entries), depth + 1, delimiter))
    return lines


def encode(obj: dict) -> str:
    """Serialize a Python dict to official-spec TOON text (default form:
    2-space indent, comma delimiter — the reference library's defaults)."""
    if not isinstance(obj, dict):
        raise TypeError(f"encode expects a dict at the top level, got {type(obj).__name__}")
    return "\n".join(_encode_json_value(_normalize_value(obj), _DEFAULT_DELIMITER, 0))


# ── decode: scanner (src/decode/scanner.ts) ──────────────────────────────────
# A scanned line is a dict: raw, indent, content, depth, line_number.

def _trim_trailing_spaces(value: str) -> str:
    return value.rstrip(" ")


def _scan_lines(text: str, indent_size: int, strict: bool) -> tuple[list[dict], list[dict]]:
    lines: list[dict] = []
    blank_lines: list[dict] = []
    for line_number, raw in enumerate(text.split("\n"), start=1):
        if line_number == 1 and raw.startswith("\ufeff"):
            raw = raw[1:]
        if raw.endswith("\r"):
            raw = raw[:-1]
        leading = _LEADING_WHITESPACE.match(raw).group(0)
        first_tab = leading.find("\t")
        indent = first_tab if strict and first_tab != -1 else len(leading)
        tab_indent = 0 if strict or first_tab == -1 else leading.count("\t")
        content = _trim_trailing_spaces(raw[indent:])
        if content.startswith("#"):
            continue  # comment line
        depth = (indent - tab_indent) // indent_size + tab_indent
        if not content:
            blank_lines.append({"line_number": line_number, "indent": indent, "depth": depth})
            continue
        if strict:
            if first_tab != -1:
                raise ToonDecodeError("Tabs are not allowed in indentation in strict mode",
                                      line=line_number, source=raw)
            if indent > 0 and indent % indent_size != 0:
                raise ToonDecodeError(
                    f"Indentation must be exact multiple of {indent_size}, "
                    f"but found {indent} spaces", line=line_number, source=raw)
        lines.append({"raw": raw, "indent": indent, "content": content,
                      "depth": depth, "line_number": line_number})
    return lines, blank_lines


class _Reader:
    """Sequential reader over scanned lines (the sync analog of the
    reference's line-reader)."""

    def __init__(self, lines: list[dict], blank_lines: list[dict]):
        self._lines = lines
        self._pos = 0
        self.last_line: dict | None = None
        self.blank_lines = blank_lines

    def peek(self) -> dict | None:
        return self._lines[self._pos] if self._pos < len(self._lines) else None

    def read(self) -> dict | None:
        line = self.peek()
        if line is not None:
            self._pos += 1
            self.last_line = line
        return line


def _with_line(line: dict, fn):
    """Attach line context to errors raised by pure parser helpers."""
    try:
        return fn()
    except ToonDecodeError as exc:
        if exc.line is not None:
            raise
        raise ToonDecodeError(str(exc), line=line["line_number"], source=line["raw"]) from exc
    except (ValueError, SyntaxError) as exc:
        raise ToonDecodeError(str(exc), line=line["line_number"], source=line["raw"]) from exc


# ── decode: token/header parsing (src/decode/parser.ts) ──────────────────────

def _parse_bracket_segment(seg: str, default_delimiter: str) -> tuple[int, str, bool]:
    content = seg
    delimiter = default_delimiter
    if content.endswith("\t"):
        delimiter = "\t"
        content = content[:-1]
    elif content.endswith("|"):
        delimiter = "|"
        content = content[:-1]
    keyed = False
    if content.endswith(":"):
        keyed = True
        content = content[:-1]
    if not _BRACKET_LENGTH.match(content):
        raise ToonDecodeError(
            f'Invalid array length: "{seg}" (expected non-negative integer with no leading zeros)')
    return int(content), delimiter, keyed


def _split_field_entries(content: str, delimiter: str) -> list[str]:
    entries: list[str] = []
    buf: list[str] = []
    in_quotes = False
    brace_depth = 0
    i = 0
    while i < len(content):
        char = content[i]
        if char == "\\" and i + 1 < len(content) and in_quotes:
            buf.append(char + content[i + 1])
            i += 2
            continue
        if char == '"':
            in_quotes = not in_quotes
            buf.append(char)
            i += 1
            continue
        if not in_quotes:
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
            elif char == delimiter and brace_depth == 0:
                entries.append("".join(buf))
                buf = []
                i += 1
                continue
        buf.append(char)
        i += 1
    entries.append("".join(buf))
    return entries


def _find_matching_brace(content: str, brace_start: int) -> int:
    in_quotes = False
    brace_depth = 0
    i = brace_start
    while i < len(content):
        char = content[i]
        if char == "\\" and i + 1 < len(content) and in_quotes:
            i += 2
            continue
        if char == '"':
            in_quotes = not in_quotes
            i += 1
            continue
        if not in_quotes:
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    return i
        i += 1
    return -1


def _parse_field_entries(fields_content: str, delimiter: str) -> list:
    fields = []
    for entry in _split_field_entries(fields_content, delimiter):
        trimmed = _trim_spaces(entry)
        if not trimmed:
            raise ToonDecodeError("Empty field name in field list")
        group_start = _find_unquoted_char(trimmed, "{")
        if group_start == -1:
            fields.append((_parse_string_literal(trimmed), None))
            continue
        name_part = _trim_spaces(trimmed[:group_start])
        if not name_part:
            raise ToonDecodeError("Missing field name before nested field group")
        group_end = _find_matching_brace(trimmed, group_start)
        if group_end == -1:
            raise ToonDecodeError("Unmatched brace in field list")
        if group_end != len(trimmed) - 1:
            raise ToonDecodeError("Unexpected content after nested field group")
        children = _parse_field_entries(trimmed[group_start + 1:group_end], delimiter)
        fields.append((_parse_string_literal(name_part), children))
    return fields


def _find_duplicate_field_name(fields: list) -> str | None:
    seen: set[str] = set()
    for name, children in fields:
        if name in seen:
            return name
        seen.add(name)
        if children:
            nested = _find_duplicate_field_name(children)
            if nested is not None:
                return nested
    return None


def _find_unquoted_mismatched_delimiter(content: str, active_delimiter: str) -> str | None:
    for candidate in _DELIMITERS:
        if candidate == active_delimiter:
            continue
        if _find_unquoted_char(content, candidate) != -1:
            return candidate
    return None


def _format_delimiter(delimiter: str) -> str:
    return "\\t" if delimiter == "\t" else delimiter


def _parse_delimited_values(input_str: str, delimiter: str) -> list[str]:
    values: list[str] = []
    buf: list[str] = []
    in_quotes = False
    i = 0
    while i < len(input_str):
        char = input_str[i]
        if char == "\\" and i + 1 < len(input_str) and in_quotes:
            buf.append(char + input_str[i + 1])
            i += 2
            continue
        if char == '"':
            in_quotes = not in_quotes
            buf.append(char)
            i += 1
            continue
        if char == delimiter and not in_quotes:
            values.append(_trim_spaces("".join(buf)))
            buf = []
            i += 1
            continue
        buf.append(char)
        i += 1
    if buf or values:
        values.append(_trim_spaces("".join(buf)))
    return values


def _parse_primitive_token(token: str) -> Any:
    trimmed = _trim_spaces(token)
    if not trimmed:
        return ""
    if trimmed.startswith('"'):
        return _parse_string_literal(trimmed)
    if trimmed == "true":
        return True
    if trimmed == "false":
        return False
    if trimmed == "null":
        return None
    if _is_numeric_literal(trimmed):
        if any(c in trimmed for c in ".eE"):
            parsed = float(trimmed)
            return 0.0 if parsed == 0.0 else parsed  # -0 -> 0
        return int(trimmed)
    return trimmed


def _parse_string_literal(token: str) -> str:
    trimmed = _trim_spaces(token)
    if trimmed.startswith('"'):
        closing = _find_closing_quote(trimmed, 0)
        if closing == -1:
            raise ToonDecodeError("Unterminated string: missing closing quote")
        if closing != len(trimmed) - 1:
            raise ToonDecodeError("Unexpected characters after closing quote")
        return _unescape_string(trimmed[1:closing])
    return trimmed


def _parse_key_token(content: str, start: int) -> tuple[str, int]:
    if content[start] == '"':
        closing = _find_closing_quote(content, start)
        if closing == -1:
            raise ToonDecodeError("Unterminated quoted key")
        key = _unescape_string(content[start + 1:closing])
        pos = closing + 1
        if pos >= len(content) or content[pos] != ":":
            raise ToonDecodeError("Missing colon after key")
        return key, pos + 1
    colon = _find_unquoted_char(content, ":", start)
    if colon == -1:
        raise ToonDecodeError("Missing colon after key")
    return _trim_spaces(content[start:colon]), colon + 1


def _is_array_header_content(content: str) -> bool:
    return content.strip().startswith("[") and _find_unquoted_char(content, ":") != -1


def _is_key_value_content(content: str) -> bool:
    return _find_unquoted_char(content, ":") != -1


def _parse_array_header_line(content: str, default_delimiter: str) -> dict:
    """Port of the reference's parseArrayHeaderLine: returns a dict with
    kind ``notHeader`` | ``invalid`` (with reason) | ``header`` (with header,
    inline_values, strict_error)."""
    trimmed_token = _trim_spaces(content)
    if trimmed_token.startswith('"'):
        closing = _find_closing_quote(trimmed_token, 0)
        if closing == -1 or not trimmed_token[closing + 1:].startswith("["):
            return {"kind": "notHeader"}
        key_end_index = len(content) - len(trimmed_token) + closing + 1
        bracket_start = content.find("[", key_end_index)
    else:
        bracket_start = _find_unquoted_char(content, "[")
    if bracket_start == -1:
        return {"kind": "notHeader"}
    first_colon = _find_unquoted_char(content, ":")
    if first_colon != -1 and first_colon < bracket_start:
        return {"kind": "notHeader"}
    bracket_end = _find_unquoted_char(content, "]", bracket_start)
    if bracket_end == -1:
        return {"kind": "notHeader"}
    brace_end = bracket_end + 1
    brace_start = _find_unquoted_char(content, "{", bracket_end)
    if brace_start != -1 and brace_start < _find_unquoted_char(content, ":", bracket_end):
        gap_before_brace = content[bracket_end + 1:brace_start]
        if gap_before_brace != "":
            trimmed_gap = gap_before_brace.strip()
            reason = ("Unexpected whitespace between bracket segment and field list"
                      if trimmed_gap == "" else
                      f'Unexpected content "{trimmed_gap}" between bracket segment and field list')
            return {"kind": "invalid", "reason": reason}
        found_brace_end = _find_matching_brace(content, brace_start)
        if found_brace_end != -1:
            brace_end = found_brace_end + 1
    colon_index = _find_unquoted_char(content, ":", max(bracket_end, brace_end))
    if colon_index == -1:
        return {"kind": "notHeader"}
    gap_start = max(bracket_end + 1, brace_end)
    gap_before_colon = content[gap_start:colon_index]
    if gap_before_colon != "":
        trimmed_gap = gap_before_colon.strip()
        reason = ("Unexpected whitespace between bracket segment and colon"
                  if trimmed_gap == "" else
                  f'Unexpected content "{trimmed_gap}" between bracket segment and colon')
        return {"kind": "invalid", "reason": reason}
    key = None
    if bracket_start > 0:
        raw_key = content[:bracket_start]
        if raw_key != raw_key.rstrip():
            return {"kind": "invalid",
                    "reason": "Unexpected whitespace between key and bracket segment"}
        key = _parse_string_literal(raw_key) if raw_key.startswith('"') else raw_key
    after_colon = _trim_spaces(content[colon_index + 1:])
    bracket_content = content[bracket_start + 1:bracket_end]
    try:
        length, delimiter, keyed = _parse_bracket_segment(bracket_content, default_delimiter)
    except ToonDecodeError as exc:
        return {"kind": "invalid", "reason": str(exc)}
    fields = None
    if brace_start != -1 and brace_start < colon_index:
        found_brace_end = _find_matching_brace(content, brace_start)
        if found_brace_end != -1 and found_brace_end < colon_index:
            fields_content = content[brace_start + 1:found_brace_end]
            mismatched = _find_unquoted_mismatched_delimiter(fields_content, delimiter)
            if mismatched is not None:
                return {"kind": "invalid", "reason": (
                    f'Header delimiter mismatch: bracket declares '
                    f'"{_format_delimiter(delimiter)}" but field list contains '
                    f'unquoted "{_format_delimiter(mismatched)}"')}
            try:
                fields = _parse_field_entries(fields_content, delimiter)
            except ToonDecodeError as exc:
                return {"kind": "invalid", "reason": str(exc)}
    duplicate = _find_duplicate_field_name(fields) if fields else None
    duplicate_reason = (f'Duplicate field name "{duplicate}" in field list'
                        if duplicate else None)
    if keyed and not fields:
        return {"kind": "invalid", "reason": "Keyed header requires a field list"}
    if fields and after_colon:
        return {"kind": "invalid",
                "reason": duplicate_reason or "Unexpected content after fields-bearing header colon"}
    return {"kind": "header",
            "header": {"key": key, "length": length, "delimiter": delimiter,
                       "fields": fields, "keyed": keyed},
            "inline_values": after_colon or None,
            "strict_error": duplicate_reason}


def _resolve_array_header(result: dict, strict: bool) -> dict | None:
    if result["kind"] == "notHeader":
        return None
    if result["kind"] == "invalid":
        if strict:
            raise ToonDecodeError(result["reason"])
        return None
    if strict and result["strict_error"] is not None:
        raise ToonDecodeError(result["strict_error"])
    return {"header": result["header"], "inline_values": result["inline_values"]}


# ── decode: validation (src/decode/validation.ts) ────────────────────────────

def _assert_expected_count(actual: int, expected: int, item_type: str,
                           strict: bool, line: dict) -> None:
    if strict and actual != expected:
        raise ToonDecodeError(f"Expected {expected} {item_type}, but got {actual}",
                              line=line["line_number"], source=line.get("raw"))


def _validate_no_extra_list_items(next_line: dict | None, item_depth: int,
                                  expected_count: int) -> None:
    if (next_line is not None and next_line["depth"] == item_depth
            and next_line["content"].startswith("- ")):
        raise ToonDecodeError(
            f"Expected {expected_count} list-form items, but found more",
            line=next_line["line_number"], source=next_line["raw"])


def _validate_no_extra_tabular_rows(next_line: dict | None, row_depth: int,
                                    header: dict) -> None:
    if (next_line is not None and next_line["depth"] == row_depth
            and not next_line["content"].startswith("- ")
            and _is_data_row(next_line["content"], header["delimiter"])):
        raise ToonDecodeError(
            f"Expected {header['length']} tabular rows, but found more",
            line=next_line["line_number"], source=next_line["raw"])


def _validate_no_blank_lines_in_range(start_line: int, end_line: int,
                                      blank_lines: list[dict], strict: bool,
                                      context: str) -> None:
    if not strict:
        return
    for blank in blank_lines:
        if start_line < blank["line_number"] < end_line:
            raise ToonDecodeError(
                f"Blank lines inside {context} are not allowed in strict mode",
                line=blank["line_number"])


def _is_data_row(content: str, delimiter: str) -> bool:
    colon_pos = _find_unquoted_char(content, ":")
    delimiter_pos = _find_unquoted_char(content, delimiter)
    if colon_pos == -1:
        return True
    return delimiter_pos != -1 and delimiter_pos < colon_pos


# ── decode: document decoders (src/decode/decoders.ts) ───────────────────────

def _over_indented_error(line: dict, expected_depth: int) -> ToonDecodeError:
    return ToonDecodeError(
        f"Over-indented line: expected depth {expected_depth}, but found {line['depth']}",
        line=line["line_number"], source=line["raw"])


def _assert_not_scalar_line(line: dict) -> None:
    content = line["content"]
    if (content.startswith("- ") or content == "-"
            or _find_unquoted_char(content, ":") != -1):
        return
    raise ToonDecodeError("Unexpected bare token line outside root primitive position",
                          line=line["line_number"], source=line["raw"])


def _assert_no_depth_jump(first_nested: dict, parent_depth: int, strict: bool) -> None:
    if strict and first_nested["depth"] > parent_depth + 1:
        raise ToonDecodeError(
            f"Indentation depth jump: expected depth {parent_depth + 1}, "
            f"but found {first_nested['depth']}",
            line=first_nested["line_number"], source=first_nested["raw"])


def _assert_no_duplicate_key(key: str, line: dict, seen_keys: set | None) -> None:
    if seen_keys is None:
        return
    if key in seen_keys:
        raise ToonDecodeError(f'Duplicate sibling key "{key}"',
                              line=line["line_number"], source=line["raw"])
    seen_keys.add(key)


def _assert_fully_consumed(reader: _Reader, strict: bool) -> None:
    if not strict:
        return
    line = reader.peek()
    if line is not None:
        raise ToonDecodeError("Unexpected content after the document root",
                              line=line["line_number"], source=line["raw"])


def _is_key_value_line(line: dict) -> bool:
    content = line["content"]
    if content.startswith('"'):
        closing = _find_closing_quote(content, 0)
        if closing == -1:
            return False
        return ":" in content[closing + 1:]
    return ":" in content


def _decode_document(reader: _Reader, strict: bool) -> Any:
    first = reader.peek()
    if first is None:
        return {}
    if _trim_spaces(first["content"]) == "[]":
        reader.read()
        _assert_fully_consumed(reader, strict)
        return []
    if _is_array_header_content(first["content"]):
        header_info = _with_line(first, lambda: _resolve_array_header(
            _parse_array_header_line(first["content"], _DEFAULT_DELIMITER), strict))
        if header_info is not None:
            reader.read()
            value = _decode_array_from_header(
                header_info["header"], header_info["inline_values"], reader, 0, strict, first)
            _assert_fully_consumed(reader, strict)
            return value
    reader.read()
    following = reader.peek()
    if following is None and not _is_key_value_line(first):
        return _with_line(first, lambda: _parse_primitive_token(first["content"]))
    if not _is_key_value_line(first) and following is not None and following["depth"] == 0:
        raise ToonDecodeError(
            "Top-level document must start with a key-value or array-header line",
            line=first["line_number"], source=first["raw"])
    root: dict = {}
    root_seen: set | None = set() if strict else None
    key, value = _decode_key_value(first, reader, 0, strict, root_seen)
    root[key] = value
    while True:
        line = reader.peek()
        if line is None:
            break
        if line["depth"] != 0:
            if strict:
                raise _over_indented_error(line, 0)
            _assert_not_scalar_line(line)
            reader.read()
            continue
        reader.read()
        key, value = _decode_key_value(line, reader, 0, strict, root_seen)
        root[key] = value
    return root


def _decode_key_value(line: dict, reader: _Reader, base_depth: int, strict: bool,
                      seen_keys: set | None) -> tuple[str, Any]:
    content = line["content"]
    array_header = _with_line(line, lambda: _resolve_array_header(
        _parse_array_header_line(content, _DEFAULT_DELIMITER), strict))
    if array_header is not None and array_header["header"]["key"] is not None:
        _assert_no_duplicate_key(array_header["header"]["key"], line, seen_keys)
        return array_header["header"]["key"], _decode_array_from_header(
            array_header["header"], array_header["inline_values"], reader,
            base_depth, strict, line)
    if array_header is not None and array_header["header"]["key"] is None and strict:
        if array_header["header"]["keyed"]:
            raise ToonDecodeError("Keyless keyed header is only valid at the document root",
                                  line=line["line_number"], source=line["raw"])
        raise ToonDecodeError(
            "Keyless array header is only valid at the document root or as a list item",
            line=line["line_number"], source=line["raw"])
    key, end = _with_line(line, lambda: _parse_key_token(content, 0))
    rest = _trim_spaces(content[end:])
    _assert_no_duplicate_key(key, line, seen_keys)
    if not rest:
        next_line = reader.peek()
        if next_line is not None and next_line["depth"] > base_depth:
            _assert_no_depth_jump(next_line, base_depth, strict)
            return key, _decode_object_fields(reader, base_depth + 1, strict)
        return key, {}
    if rest == "[]":
        return key, []
    return key, _with_line(line, lambda: _parse_primitive_token(rest))


def _decode_object_fields(reader: _Reader, base_depth: int, strict: bool) -> dict:
    obj: dict = {}
    computed_depth: int | None = None
    seen_keys: set | None = set() if strict else None
    while True:
        line = reader.peek()
        if line is None or line["depth"] < base_depth:
            break
        if computed_depth is None and line["depth"] >= base_depth:
            computed_depth = line["depth"]
        if line["depth"] == computed_depth:
            reader.read()
            key, value = _decode_key_value(line, reader, computed_depth, strict, seen_keys)
            obj[key] = value
        elif computed_depth is not None and line["depth"] > computed_depth:
            if strict:
                raise _over_indented_error(line, computed_depth)
            _assert_not_scalar_line(line)
            reader.read()
        else:
            break
    return obj


def _decode_array_from_header(header: dict, inline_values: str | None, reader: _Reader,
                              base_depth: int, strict: bool, header_line: dict) -> Any:
    if header["keyed"]:
        return _decode_keyed_object(header, reader, base_depth, strict, header_line)
    if inline_values is not None:
        return _decode_inline_primitive_array(header, inline_values, strict, header_line)
    if header["fields"]:
        return _decode_tabular_array(header, reader, base_depth, strict, header_line)
    return _decode_list_array(header, reader, base_depth, strict, header_line)


def _decode_inline_primitive_array(header: dict, inline_values: str, strict: bool,
                                   header_line: dict) -> list:
    if not _trim_spaces(inline_values):
        _assert_expected_count(0, header["length"], "inline-form values", strict, header_line)
        return []
    values = _with_line(header_line, lambda: _parse_delimited_values(
        inline_values, header["delimiter"]))
    primitives = _with_line(header_line,
                            lambda: [_parse_primitive_token(v) for v in values])
    _assert_expected_count(len(primitives), header["length"], "inline-form values",
                           strict, header_line)
    return primitives


def _decode_keyed_object(header: dict, reader: _Reader, base_depth: int, strict: bool,
                         header_line: dict) -> dict:
    entry_depth = base_depth + 1
    leaf_field_count = _count_leaf_fields(header["fields"])
    seen_entry_keys: set | None = set() if strict else None
    obj: dict = {}
    entry_count = 0
    start_line: int | None = None
    end_line: int | None = None
    last_entry_line = header_line
    while True:
        line = reader.peek()
        if line is None or line["depth"] <= base_depth:
            break
        if line["depth"] > entry_depth:
            if strict:
                raise ToonDecodeError("Unexpected indentation inside keyed tabular object",
                                      line=line["line_number"], source=line["raw"])
            reader.read()
            continue
        if _find_unquoted_char(line["content"], ":") == -1:
            if strict:
                raise ToonDecodeError("Expected entry row inside keyed tabular object",
                                      line=line["line_number"], source=line["raw"])
            reader.read()
            continue
        reader.read()
        if start_line is None:
            start_line = line["line_number"]
        end_line = line["line_number"]
        last_entry_line = line
        key, end = _with_line(line, lambda: _parse_key_token(line["content"], 0))
        _assert_no_duplicate_key(key, line, seen_entry_keys)
        cells_content = _trim_spaces(line["content"][end:])
        values = ([] if cells_content == "" else
                  _with_line(line, lambda: _parse_delimited_values(
                      cells_content, header["delimiter"])))
        _assert_expected_count(len(values), leaf_field_count, "keyed entry cells",
                               strict, line)
        primitives = _with_line(line, lambda: [_parse_primitive_token(v) for v in values])
        obj[key] = _build_object_from_fields(header["fields"], primitives)
        entry_count += 1
    _assert_expected_count(entry_count, header["length"], "keyed entries",
                           strict, last_entry_line)
    if strict and start_line is not None and end_line is not None:
        _validate_no_blank_lines_in_range(start_line, end_line, reader.blank_lines,
                                          strict, "keyed tabular object")
    return obj


def _decode_tabular_array(header: dict, reader: _Reader, base_depth: int, strict: bool,
                          header_line: dict) -> list:
    row_depth = base_depth + 1
    rows: list = []
    start_line: int | None = None
    end_line: int | None = None
    last_row_line = header_line
    while not strict or len(rows) < header["length"]:
        line = reader.peek()
        if line is None or line["depth"] < row_depth:
            break
        if line["depth"] == row_depth:
            if not _is_data_row(line["content"], header["delimiter"]):
                break
            if start_line is None:
                start_line = line["line_number"]
            end_line = line["line_number"]
            last_row_line = line
            reader.read()
            values = _with_line(line, lambda: _parse_delimited_values(
                line["content"], header["delimiter"]))
            _assert_expected_count(len(values), _count_leaf_fields(header["fields"]),
                                   "tabular row values", strict, line)
            primitives = _with_line(line, lambda: [_parse_primitive_token(v) for v in values])
            rows.append(_build_object_from_fields(header["fields"], primitives))
        else:
            break
    _assert_expected_count(len(rows), header["length"], "tabular rows", strict, last_row_line)
    if strict and start_line is not None and end_line is not None:
        _validate_no_blank_lines_in_range(start_line, end_line, reader.blank_lines,
                                          strict, "tabular array")
    if strict:
        _validate_no_extra_tabular_rows(reader.peek(), row_depth, header)
    return rows


def _decode_list_array(header: dict, reader: _Reader, base_depth: int, strict: bool,
                       header_line: dict) -> list:
    item_depth = base_depth + 1
    items: list = []
    start_line: int | None = None
    end_line: int | None = None
    last_item_line = header_line
    while not strict or len(items) < header["length"]:
        line = reader.peek()
        if line is None or line["depth"] < item_depth:
            break
        is_list_item = line["content"].startswith("- ") or line["content"] == "-"
        if line["depth"] == item_depth and is_list_item:
            if start_line is None:
                start_line = line["line_number"]
            end_line = line["line_number"]
            last_item_line = line
            items.append(_decode_list_item(reader, item_depth, strict))
            last_consumed = reader.last_line
            if last_consumed is not None:
                end_line = last_consumed["line_number"]
                last_item_line = last_consumed
        else:
            break
    _assert_expected_count(len(items), header["length"], "list-form items",
                           strict, last_item_line)
    if strict and start_line is not None and end_line is not None:
        _validate_no_blank_lines_in_range(start_line, end_line, reader.blank_lines,
                                          strict, "list-form array")
    if strict:
        _validate_no_extra_list_items(reader.peek(), item_depth, header["length"])
    return items


def _decode_list_item(reader: _Reader, base_depth: int, strict: bool) -> Any:
    line = reader.read()
    if line is None:
        raise ToonDecodeError("Expected list item")
    if line["content"] == "-":
        return {}
    if line["content"].startswith("- "):
        after_hyphen = line["content"][2:]
    else:
        raise ToonDecodeError('Expected list item to start with "- "',
                              line=line["line_number"], source=line["raw"])
    if not _trim_spaces(after_hyphen):
        return {}
    if _trim_spaces(after_hyphen) == "[]":
        return []
    item_line = {**line, "content": after_hyphen}
    if _is_array_header_content(after_hyphen):
        array_header = _with_line(item_line, lambda: _resolve_array_header(
            _parse_array_header_line(after_hyphen, _DEFAULT_DELIMITER), strict))
        if array_header is not None:
            if array_header["header"]["keyed"] or array_header["header"]["fields"] is not None:
                if strict:
                    if array_header["header"]["keyed"]:
                        raise ToonDecodeError(
                            "Keyless keyed header is only valid at the document root",
                            line=item_line["line_number"], source=item_line["raw"])
                    raise ToonDecodeError(
                        "Keyless header with a field list is only valid at the document root",
                        line=item_line["line_number"], source=item_line["raw"])
            else:
                return _decode_array_from_header(
                    array_header["header"], array_header["inline_values"], reader,
                    base_depth, strict, item_line)
    header_info = _with_line(item_line, lambda: _resolve_array_header(
        _parse_array_header_line(after_hyphen, _DEFAULT_DELIMITER), strict))
    if (header_info is not None and header_info["header"]["key"] is not None
            and header_info["header"]["fields"] is not None):
        header = header_info["header"]
        seen_keys: set | None = {header["key"]} if strict else None
        obj: dict = {header["key"]: _decode_array_from_header(
            header, header_info["inline_values"], reader, base_depth + 1, strict, item_line)}
        _follow_sibling_fields(reader, base_depth + 1, strict, seen_keys, obj)
        return obj
    if _is_key_value_content(after_hyphen):
        seen_keys = set() if strict else None
        obj = {}
        key, value = _decode_key_value(item_line, reader, base_depth + 1, strict, seen_keys)
        obj[key] = value
        _follow_sibling_fields(reader, base_depth + 1, strict, seen_keys, obj)
        return obj
    return _with_line(item_line, lambda: _parse_primitive_token(after_hyphen))


def _follow_sibling_fields(reader: _Reader, follow_depth: int, strict: bool,
                           seen_keys: set | None, obj: dict) -> None:
    while True:
        next_line = reader.peek()
        if next_line is None or next_line["depth"] < follow_depth:
            break
        if (next_line["depth"] == follow_depth
                and not next_line["content"].startswith("- ")):
            reader.read()
            key, value = _decode_key_value(next_line, reader, follow_depth, strict, seen_keys)
            obj[key] = value
        else:
            break


def _build_object_from_fields(fields: list, primitives: list) -> dict:
    cell_index = [0]

    def walk(nodes: list) -> dict:
        obj: dict = {}
        for name, children in nodes:
            if not children and cell_index[0] >= len(primitives):
                continue
            if children:
                obj[name] = walk(children)
            else:
                obj[name] = primitives[cell_index[0]]
                cell_index[0] += 1
        return obj

    return walk(fields)


def decode(text: str) -> dict:
    """Parse official-spec TOON text into a Python value (strict mode, the
    reference decoder's default: 2-space indent, comma default delimiter)."""
    lines, blank_lines = _scan_lines(text, _INDENT_SIZE, strict=True)
    return _decode_document(_Reader(lines, blank_lines), strict=True)
