"""YAML rule parser and graph query evaluator for custom governance rules.

Users define architectural rules as YAML files in ``.roam/rules/``.
Roam evaluates them against the indexed graph and reports violations.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger(__name__)

from roam._glob_match import matches_glob as _matches_glob
from roam.db.connection import find_project_root
from roam.index.parser import detect_language, parse_file
from roam.output.formatter import WarningsOut
from roam.rules.ast_match import (
    compile_ast_pattern,
    find_ast_matches,
    normalize_language_name,
)
from roam.rules.dataflow import collect_dataflow_findings

# ---------------------------------------------------------------------------
# YAML loading with fallback
# ---------------------------------------------------------------------------


def _load_yaml(
    path: Path,
    *,
    warnings_out: WarningsOut = None,
) -> dict | list | None:
    """Load a single YAML rule file, returning the parsed object or None on error.

    Delegates file-read + parse + root-type check (mapping OR list, since
    a malformed top-level-list file is a valid wrong-shape signal — see
    ``_parse_simple_yaml_text``) to
    :func:`roam.commands._yaml_loader.load_yaml_with_warnings` (W1036
    leftover scope: cmd_adrs + rules.engine sibling migration of W1051
    + W1052).

    W1036 (Pattern 2 — silent fallback, mirror of W706's
    ``_load_ignore_findings_file``): when *warnings_out* is supplied as
    a ``list[str]``, every silent-fallback path (file unreadable,
    malformed YAML, tiny-parser fallback failure) appends an actionable
    warning naming the path, the failure shape, and the resolution.
    Pre-W1036 callers that don't supply ``warnings_out`` retain
    byte-identical silent-``None`` behaviour: the legacy
    :func:`load_rules` flow continues to synthesize a placeholder
    ``{"_error": "failed to parse <name>"}`` record for each
    unparseable file so ``evaluate_rule`` can surface the parse error
    inside the rules envelope.

    W1030-followup-F: thin wrapper over :func:`_load_yaml_with_status`
    that drops the closed-enum ``LoadStatus`` return so pre-W1030-followup-F
    callers (every existing test in ``test_rules_engine_warnings_out.py``
    plus the legacy :func:`load_rules` flow) stay byte-identical.
    """
    data, _status = _load_yaml_with_status(path, warnings_out=warnings_out)
    return data


def _load_yaml_with_status(
    path: Path,
    *,
    warnings_out: WarningsOut = None,
) -> tuple[dict | list | None, str]:
    """W1030-followup-F: load a single YAML rule file and return ``(data, status)``.

    ``status`` is a closed-enum string drawn from
    :data:`roam.commands._yaml_loader.LOAD_STATUSES`
    (``"ok"`` / ``"missing"`` / ``"empty_file"`` / ``"empty_yaml"`` /
    ``"read_error"`` / ``"parse_error"`` / ``"wrong_root_type"`` /
    ``"schema_invalid"``). Lets the directory-level
    :func:`load_rules_with_status` aggregate per-file states into a
    single envelope-level ``config_state`` via worst-status rollup.

    Mirrors the cmd_check_rules / cmd_fitness pattern: this is the
    library-side primitive; consumers (``cmd_rules``) wire the rollup
    onto their envelope.
    """
    from roam.commands._yaml_loader import load_yaml_with_warnings

    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data, status = load_yaml_with_warnings(
        path,
        tiny_parser=_parse_simple_yaml_text,
        allow_list_root=True,
        config_label="rules-yaml",
        warnings_out=warnings_out,
        return_status=True,
    )
    if data is None:
        # Helper returns None only when the file is missing.
        return None, status
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper recorded a parse failure (read error / malformed YAML /
        # tiny-parser fallback failure). Preserve the pre-W1036 contract:
        # downstream ``load_rules`` synthesizes an `_error` placeholder
        # when ``_load_yaml`` returns None, and the rules envelope
        # surfaces the parse error per-rule. Returning the empty container
        # here would hide the placeholder.
        return None, status
    # PyYAML-without-warnings_out path: when the caller didn't pass an
    # accumulator AND PyYAML (or the tiny parser) raised, the helper
    # still returns the empty container (``[]`` because
    # ``allow_list_root=True``) silently. Treat the empty container as
    # "file produced no useful data" so the historical
    # ``return None`` -> placeholder-_error flow holds.
    if warnings_out is None and isinstance(data, (dict, list)) and not data:
        return None, status
    return data, status


def _coerce_scalar(val: str) -> object:
    """Best-effort YAML scalar coercion: bool / int / float / quoted string."""
    val = val.strip().strip('"').strip("'")
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    try:
        return int(val)
    except ValueError:
        try:
            return float(val)
        except ValueError:
            return val


def _parse_simple_yaml(path: Path) -> dict | None:
    """Minimal YAML subset parser for rule files (no PyYAML dependency).

    Reads ``path`` and delegates to :func:`_parse_simple_yaml_text`. Kept
    as the historical entry point for callers that still pass a ``Path``
    (W1036: the shared helper substrate uses
    :func:`_parse_simple_yaml_text` directly so the helper can run on the
    in-memory text it already read).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        sys.stderr.write(f"[rules] YAML read failed for {path}: {exc}\n")
        return None
    try:
        return _parse_simple_yaml_text(text)
    except ValueError as exc:
        sys.stderr.write(f"[rules] fallback YAML parser failed for {path}: {exc}\n")
        return None


_YAML_QUOTED_SCALAR_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
_SimpleYamlFrame = tuple[int, object, str, object, object]


def _validate_simple_yaml_lines(text: str) -> str:
    """Return the first real line after checking simple malformed shapes."""
    first_real_line = ""
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not first_real_line:
            first_real_line = stripped
        unquoted = _YAML_QUOTED_SCALAR_RE.sub("", stripped)
        opens = unquoted.count("[") + unquoted.count("{")
        closes = unquoted.count("]") + unquoted.count("}")
        if opens != closes:
            raise ValueError("malformed YAML: unbalanced brackets")
    return first_real_line


def _parse_top_level_yaml_list(text: str) -> list[object]:
    """Parse the intentionally-limited top-level list signal."""
    items: list[object] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            items.append(_coerce_scalar(stripped[2:].strip()))
    return items


def _promote_empty_yaml_dict_frame(stack: list[_SimpleYamlFrame]) -> tuple[object, str]:
    """Promote ``key:`` placeholder dicts to lists for nested ``-`` items."""
    top_indent, top_container, top_kind, top_parent, top_pkey = stack[-1]
    if not (
        top_kind == "dict"
        and isinstance(top_container, dict)
        and not top_container
        and top_parent is not None
        and top_pkey is not None
    ):
        return top_container, top_kind

    new_list: list[object] = []
    if isinstance(top_parent, dict):
        top_parent[top_pkey] = new_list
    elif isinstance(top_parent, list) and isinstance(top_pkey, int):
        top_parent[top_pkey] = new_list
    else:
        return top_container, top_kind
    stack[-1] = (top_indent, new_list, "list", top_parent, top_pkey)
    return new_list, "list"


def _append_simple_yaml_list_item(
    items: list[object],
    after_dash: str,
    indent: int,
    stack: list[_SimpleYamlFrame],
) -> None:
    """Append one limited YAML list item and update the parse stack."""
    if ":" in after_dash:
        new_item: dict = {}
        items.append(new_item)
        key, _, val = after_dash.partition(":")
        key = key.strip()
        if val.strip():
            new_item[key] = _coerce_scalar(val)
        else:
            new_item[key] = {}
        # Push the new item dict so subsequent same-indent keys populate it.
        stack.append((indent + 2, new_item, "dict", items, len(items) - 1))
    else:
        items.append(_coerce_scalar(after_dash))


def _handle_simple_yaml_list_line(
    stripped: str,
    indent: int,
    stack: list[_SimpleYamlFrame],
) -> None:
    """Handle a ``-`` line in the fallback parser."""
    after_dash = stripped[2:].strip()
    top_container, top_kind = _promote_empty_yaml_dict_frame(stack)
    if top_kind == "list" and isinstance(top_container, list):
        _append_simple_yaml_list_item(top_container, after_dash, indent, stack)


def _split_simple_yaml_mapping(stripped: str) -> tuple[str, str, str] | None:
    """Split a simple ``key: value`` line into key, raw value, stripped value."""
    if ":" not in stripped:
        return None
    key, _, val = stripped.partition(":")
    return key.strip(), val, val.strip()


def _parse_inline_simple_yaml_list(val: str) -> list[str]:
    """Parse the fallback parser's simple one-line list syntax."""
    return [v.strip().strip('"').strip("'") for v in val[1:-1].split(",") if v.strip()]


def _handle_simple_yaml_mapping_line(
    parsed: tuple[str, str, str],
    indent: int,
    stack: list[_SimpleYamlFrame],
) -> None:
    """Handle one mapping line against the current dict frame."""
    _i, container, kind, _pp, _pk = stack[-1]
    if kind != "dict" or not isinstance(container, dict):
        return

    key, val_raw, val = parsed
    if not val:
        child: dict = {}
        container[key] = child
        stack.append((indent + 2, child, "dict", container, key))
    elif val.startswith("[") and val.endswith("]"):
        container[key] = _parse_inline_simple_yaml_list(val)
    else:
        container[key] = _coerce_scalar(val_raw)


def _collapse_empty_yaml_placeholders(node):
    """Collapse empty placeholder dicts to match PyYAML's null result."""
    if isinstance(node, dict):
        for k in list(node):
            node[k] = _collapse_empty_yaml_placeholders(node[k])
        return node if node else None
    if isinstance(node, list):
        return [_collapse_empty_yaml_placeholders(v) for v in node]
    return node


def _parse_simple_yaml_text(text: str) -> dict | list | None:
    """Minimal YAML subset parser for rule files (no PyYAML dependency).

    Handles:
    * flat key-value pairs
    * inline lists ``[a, b, c]``
    * nested maps under an indented key
    * list-of-dicts shape introduced by ``- key: value`` items (the
      ``rules:`` block in rules.yml is the canonical use)

    12.34 (2026-05-06) — added list-of-dicts support; CI 12.33 failed
    on the PyYAML-not-installed lane because
    ``test_load_rules_yaml_simple`` uses that shape.

    Frames track ``(indent, container, kind, parent_key)``. When we hit
    a ``- key: value`` item and the current frame is an empty dict, we
    promote it to a list inside the parent container at the recorded
    ``parent_key``, then push a new dict frame for the item.

    W1036: hoisted from :func:`_parse_simple_yaml` so the shared
    :func:`roam.commands._yaml_loader.load_yaml_with_warnings` helper can
    invoke this directly as its ``tiny_parser`` callback on the
    pre-read text. Raises ``ValueError`` on truly malformed input (e.g.
    unbalanced brackets) — the helper catches that and routes through
    the tiny-parser-failed warning path.
    """
    # 12.35 (2026-05-06) — sanity-check obviously malformed YAML so callers
    # don't get a permissive non-empty result that hides the bug. PyYAML
    # raises YAMLError on shapes like `this is not: valid: yaml: at all: [`;
    # the fallback should mimic that behaviour. Cheap signal: unbalanced
    # brackets on a single line, but ONLY counting brackets OUTSIDE quoted
    # strings (12.36 — community rule files like
    # `sources: ["$_GET[", "$_POST["]` have legitimate brackets-inside-quotes
    # that aren't balanced if we count naively).
    first_real_line = _validate_simple_yaml_lines(text)

    # 12.35 — top-level-is-a-list detection. PyYAML returns a Python list
    # for input that starts with `- `; the loader downstream surfaces a
    # "must be a mapping" warning because rules.yml requires a dict at
    # the root. Without this, a top-level-list file silently returns {}
    # and no warning ever surfaces.
    if first_real_line.startswith("- "):
        return _parse_top_level_yaml_list(text)

    result: dict = {}
    # Frame: (indent, container, kind, parent_dict, parent_key)
    # The root frame has parent_dict=None / parent_key=None.
    stack: list[_SimpleYamlFrame] = [(0, result, "dict", None, None)]

    for raw in text.split("\n"):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw) - len(raw.lstrip())

        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()

        if stripped.startswith("- "):
            _handle_simple_yaml_list_line(stripped, indent, stack)
            continue

        parsed = _split_simple_yaml_mapping(stripped)
        if parsed is None:
            continue
        _handle_simple_yaml_mapping_line(parsed, indent, stack)

    # 12.36 (2026-05-06) — collapse empty placeholder dicts to None so
    # `rules:\n` with no items returns `{"rules": None}` (matching
    # PyYAML behaviour). Without this, the loader downstream sees an
    # empty dict and emits a spurious "must be a list, got dict" warning.
    cleaned = _collapse_empty_yaml_placeholders(result)
    if not cleaned:
        return None
    return cleaned


def _dump_scalar(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # Quote when the scalar contains characters that change YAML semantics:
    # leading/trailing whitespace, special chars, glob/wildcard, leading
    # comment, leading bracket/dash, or empty string.
    if not s:
        return "''"
    special = set("[]{}#&*!|>'\"%@`,:?")
    if s[0] in special or s[-1] in (" ", "\t") or any(c in s for c in special):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _dump_kv(key: str, value, pad: str, indent: int) -> str:
    """One `key: value` line, recursing into non-empty containers."""
    if isinstance(value, (dict, list)) and value:
        return f"{pad}{key}:" + _dump_value(value, indent)
    return f"{pad}{key}: {_dump_scalar(value)}"


def _dump_list_item(item, pad: str, indent: int) -> list[str]:
    """Lines for one `- item` list entry (first key shares the dash line)."""
    if not isinstance(item, dict):
        return [f"{pad}- {_dump_scalar(item)}"]
    if not item:
        return [f"{pad}- {{}}"]
    first_key, *rest_keys = item.keys()
    lines = [_dump_kv(f"- {first_key}", item[first_key], pad, indent + 4)]
    sub_pad = " " * (indent + 2)
    lines.extend(_dump_kv(k, item[k], sub_pad, indent + 4) for k in rest_keys)
    return lines


def _dump_value(v, indent: int) -> str:
    if isinstance(v, dict):
        if not v:
            return "{}"
        pad = " " * indent
        lines = [""] + [_dump_kv(k, sub, pad, indent + 2) for k, sub in v.items()]
        return "\n".join(lines)
    if isinstance(v, list):
        if not v:
            return "[]"
        pad = " " * indent
        lines = [""]
        for item in v:
            lines.extend(_dump_list_item(item, pad, indent))
        return "\n".join(lines)
    return f" {_dump_scalar(v)}"


def _emit_simple_yaml(doc: dict) -> str:
    """Minimal YAML emitter for the rules.yml shape (no PyYAML dependency).

    Output mirrors what `yaml.safe_dump(doc, sort_keys=False)` would
    produce for the documented `{"rules": [{...}, {...}]}` structure.
    Used by `roam rules-validate --fix` when PyYAML isn't available
    (the parse path already has a fallback; this completes the
    round-trip).

    12.37 (2026-05-06) — added so the `--fix` write-back works on
    installs without PyYAML.
    """
    return "\n".join(_dump_kv(k, v, "", 2) for k, v in doc.items()) + "\n"


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------


def load_rules(
    rules_dir: Path,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Load all .yaml/.yml files from the rules directory.

    Returns a list of rule dicts. Files that fail to parse are silently
    skipped (a warning is included in the rule's ``_error`` key).

    W1036 (Pattern 2 — silent fallback, sibling of W1051 + W1052): when
    *warnings_out* is supplied as a ``list[str]``, every silent-fallback
    path inside :func:`_load_yaml` (file unreadable, malformed YAML,
    tiny-parser fallback failure, non-mapping root) appends an
    actionable warning naming the path, the failure shape, and the
    resolution. Pre-W1036 callers that don't supply ``warnings_out``
    retain byte-identical silent-`_error` behaviour: each unparseable
    file still becomes a placeholder rule with ``_error`` so the
    envelope surfaces per-file parse failures via :func:`evaluate_rule`.

    W1030-followup-F: thin wrapper over :func:`load_rules_with_status`
    that drops the directory-level ``LoadStatus`` rollup so pre-W1030-followup-F
    callers stay byte-identical.
    """
    rules, _status = load_rules_with_status(rules_dir, warnings_out=warnings_out)
    return rules


# W1030-followup-F: severity rank for the directory-level LoadStatus rollup.
# Higher rank = more degraded. Used by :func:`load_rules_with_status` to
# aggregate per-file states into a single ``config_state`` field on the
# ``cmd_rules`` envelope.
#
# - ok (0)               -- at least one file parsed cleanly; no per-file errors
# - missing (1)          -- no rules directory configured; legitimate default state
# - empty_file (2)       -- directory exists but contains no .yaml/.yml files (stub),
#                           OR a file on disk is zero-byte / whitespace-only
# - empty_yaml (2)       -- a file on disk is comments-only
# - read_error (3)       -- a file is unreadable; broken
# - schema_invalid (3)   -- a file parsed but failed validator
# - wrong_root_type (3)  -- a file's root is list/scalar, not mapping
# - parse_error (3)      -- a file is malformed YAML/JSON
#
# Mirrors :data:`roam.commands.cmd_check_rules._STATUS_RANK` (W1030-followup-C)
# and :data:`roam.commands.cmd_fitness._DEGRADED_LOAD_STATUSES` (W1030-followup-D).
_RULES_STATUS_RANK: dict[str, int] = {
    "ok": 0,
    "missing": 1,
    "empty_file": 2,
    "empty_yaml": 2,
    "read_error": 3,
    "schema_invalid": 3,
    "wrong_root_type": 3,
    "parse_error": 3,
}


def _worst_rules_status(*statuses: str) -> str:
    """W1030-followup-F: roll up per-file ``LoadStatus`` values to the worst.

    Degraded states override ``ok``; the most-degraded state wins. Returns
    ``"ok"`` when every status is ``"ok"``. Unknown statuses (not in
    :data:`_RULES_STATUS_RANK`) sort below ``"ok"`` so a future LoadStatus
    addition can't silently downgrade the rollup.
    """
    if not statuses:
        return "ok"
    worst = statuses[0]
    worst_rank = _RULES_STATUS_RANK.get(worst, -1)
    for s in statuses[1:]:
        s_rank = _RULES_STATUS_RANK.get(s, -1)
        if s_rank > worst_rank:
            worst = s
            worst_rank = s_rank
    return worst


def load_rules_with_status(
    rules_dir: Path,
    *,
    warnings_out: WarningsOut = None,
) -> tuple[list[dict], str]:
    """W1030-followup-F: load all .yaml/.yml files and return ``(rules, status)``.

    ``status`` is a closed-enum string drawn from
    :data:`roam.commands._yaml_loader.LOAD_STATUSES`. Because the rules
    engine reads a *directory* of files rather than a single config, the
    status is a worst-state rollup across the per-file ``LoadStatus``
    values returned by :func:`_load_yaml_with_status`.

    Status semantics:

    * ``"missing"`` -- the rules directory does not exist (the consumer's
      ``--init`` / "no rules directory" branch should fire on this path,
      but we surface the status uniformly for envelope disclosure).
    * ``"empty_file"`` -- the rules directory exists but contains no
      ``.yaml`` / ``.yml`` files (an empty stub directory). Distinct from
      ``"missing"`` so the agent can disambiguate "never configured" from
      "configured but no rules written yet".
    * ``"ok"`` -- at least one file parsed cleanly AND no file failed.
    * ``"parse_error"`` / ``"wrong_root_type"`` / ``"read_error"`` /
      ``"schema_invalid"`` -- the worst per-file state when any file
      failed to parse cleanly (the canonical loader already emitted a
      warning via ``warnings_out``).
    """
    if not rules_dir.is_dir():
        return [], "missing"

    rules: list[dict] = []
    statuses: list[str] = []
    saw_any_file = False
    for p in sorted(rules_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix not in (".yaml", ".yml"):
            continue
        saw_any_file = True
        data, file_status = _load_yaml_with_status(p, warnings_out=warnings_out)
        statuses.append(file_status)
        if data is None or not isinstance(data, dict):
            # None = parse failure / missing file; list = top-level-list
            # is a wrong-root-type signal the engine cannot evaluate. Both
            # turn into the `_error` placeholder so the envelope surfaces
            # which file failed and why.
            error_msg = f"failed to parse {p.name}"
            if isinstance(data, list):
                error_msg = (
                    f"{p.name}: top-level list is not a valid rule shape; "
                    "expected a mapping with keys like `name`, `severity`, `match`"
                )
            rules.append(
                {
                    "name": p.name,
                    "severity": "error",
                    "_error": error_msg,
                    "_file": str(p),
                }
            )
            continue
        data["_file"] = str(p)
        rules.append(data)

    if not saw_any_file:
        # Directory exists but contains no .yaml/.yml files -- an empty
        # stub. Distinct from missing so the agent can disambiguate
        # "never configured" from "configured but no rules written yet".
        return rules, "empty_file"

    return rules, _worst_rules_status(*statuses)


# ---------------------------------------------------------------------------
# Exemption helpers
# ---------------------------------------------------------------------------


def _is_exempt(symbol_name: str, file_path: str, exempt: dict) -> bool:
    """Check if a symbol/file combination is exempt from the rule."""
    exempt_symbols = exempt.get("symbols", [])
    if isinstance(exempt_symbols, str):
        exempt_symbols = [exempt_symbols]
    for es in exempt_symbols:
        if es == symbol_name:
            return True

    exempt_files = exempt.get("files", [])
    if isinstance(exempt_files, str):
        exempt_files = [exempt_files]
    for ef in exempt_files:
        if _matches_glob(file_path, ef):
            return True

    return False


# ``_matches_glob`` is imported from ``roam._glob_match`` at the top of
# this module (W856 hoist — was duplicated in ``policy/graph_clauses.py``).


def _matches_kind(kind: str, kind_filter: list | str | None) -> bool:
    """Check if a symbol kind matches the kind filter."""
    if kind_filter is None:
        return True
    if isinstance(kind_filter, str):
        kind_filter = [kind_filter]
    return kind in kind_filter


def _table_exists(conn, table_name: str) -> bool:
    """Return True when a table exists in the current SQLite DB."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _table_columns(conn, table_name: str) -> set[str]:
    """Return the set of columns for a table, or empty set if unavailable."""
    try:
        rows = conn.execute("PRAGMA table_info({})".format(table_name)).fetchall()
    except sqlite3.OperationalError:
        return set()

    cols: set[str] = set()
    for row in rows:
        try:
            cols.add(str(row["name"]))
        except (KeyError, IndexError, TypeError):
            if len(row) > 1:
                cols.add(str(row[1]))
    return cols


def _as_float_or_none(value) -> float | None:
    """Convert value to float when possible, else return None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        log.debug("Ignoring non-numeric rule value %r", value)
        return None


# ---------------------------------------------------------------------------
# Rule evaluation: path_match
# ---------------------------------------------------------------------------


def _evaluate_path_match(rule: dict, conn) -> dict:
    """Evaluate a path_match rule: find edges between from/to patterns.

    Looks for direct edges (or paths up to max_distance) from symbols
    matching ``match.from`` criteria to symbols matching ``match.to`` criteria.
    """
    match = rule.get("match", {})
    from_spec = match.get("from", {})
    to_spec = match.get("to", {})
    max_distance = match.get("max_distance", 1)
    exempt = rule.get("exempt", {})

    from_glob = from_spec.get("file_glob")
    from_kind = from_spec.get("kind")
    to_glob = to_spec.get("file_glob")
    to_kind = to_spec.get("kind")

    # Query edges joining source and target symbols with their files
    rows = conn.execute("""
        SELECT
            s1.name AS src_name, s1.kind AS src_kind,
            f1.path AS src_file, s1.line_start AS src_line,
            s2.name AS tgt_name, s2.kind AS tgt_kind,
            f2.path AS tgt_file, s2.line_start AS tgt_line,
            e.kind AS edge_kind
        FROM edges e
        JOIN symbols s1 ON e.source_id = s1.id
        JOIN files f1 ON s1.file_id = f1.id
        JOIN symbols s2 ON e.target_id = s2.id
        JOIN files f2 ON s2.file_id = f2.id
    """).fetchall()

    violations: list[dict] = []
    for row in rows:
        src_file = row["src_file"]
        tgt_file = row["tgt_file"]
        src_name = row["src_name"]
        tgt_name = row["tgt_name"]
        src_kind = row["src_kind"]
        tgt_kind = row["tgt_kind"]

        # Apply from-pattern filter
        if from_glob and not _matches_glob(src_file, from_glob):
            continue
        if not _matches_kind(src_kind, from_kind):
            continue

        # Apply to-pattern filter
        if to_glob and not _matches_glob(tgt_file, to_glob):
            continue
        if not _matches_kind(tgt_kind, to_kind):
            continue

        # max_distance=1 means direct edge (already satisfied)
        # For max_distance > 1 we would need BFS, but direct edge
        # matching covers the core use case.
        if max_distance < 1:
            continue

        # Check exemptions
        if _is_exempt(src_name, src_file, exempt):
            continue
        if _is_exempt(tgt_name, tgt_file, exempt):
            continue

        violations.append(
            {
                "symbol": src_name,
                "file": src_file,
                "line": row["src_line"],
                "reason": f"{src_name} ({src_file}) -> {tgt_name} ({tgt_file})",
            }
        )

    name = rule.get("name", "unnamed")
    severity = rule.get("severity", "error")
    passed = len(violations) == 0

    return {
        "name": name,
        "severity": severity,
        "passed": passed,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Rule evaluation: symbol_match
# ---------------------------------------------------------------------------


def _build_symbol_match_query(conn) -> tuple[str, str, str]:
    """Compute the schema-aware SQL fragments needed for the symbol_match
    SELECT. Returns ``(file_role_expr, param_expr, symbol_lines_expr,
    file_lines_expr, symbol_metrics_join)``."""
    file_cols = _table_columns(conn, "files")
    symbol_cols = _table_columns(conn, "symbols")
    has_symbol_metrics = _table_exists(conn, "symbol_metrics")

    if "line_count" in file_cols and "loc" in file_cols:
        file_lines_expr = "COALESCE(f.line_count, f.loc)"
    elif "line_count" in file_cols:
        file_lines_expr = "f.line_count"
    elif "loc" in file_cols:
        file_lines_expr = "f.loc"
    else:
        file_lines_expr = "NULL"

    file_role_expr = "f.file_role" if "file_role" in file_cols else "NULL"

    symbol_lines_fallback = (
        "(CASE WHEN s.line_start IS NOT NULL AND s.line_end IS NOT NULL "
        "THEN (s.line_end - s.line_start + 1) ELSE NULL END)"
        if "line_end" in symbol_cols
        else "NULL"
    )
    if has_symbol_metrics:
        param_expr = "sm.param_count"
        symbol_lines_expr = "COALESCE(sm.line_count, {})".format(symbol_lines_fallback)
        symbol_metrics_join = "LEFT JOIN symbol_metrics sm ON s.id = sm.symbol_id"
    else:
        param_expr = "NULL"
        symbol_lines_expr = symbol_lines_fallback
        symbol_metrics_join = ""

    query = """
        SELECT s.id, s.name, s.kind, s.line_start, s.is_exported,
               f.path AS file_path, {file_role_expr} AS file_role,
               COALESCE(gm.in_degree, 0) AS in_degree,
               {param_expr} AS param_count,
               {symbol_lines_expr} AS symbol_line_count,
               {file_lines_expr} AS file_line_count
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        LEFT JOIN graph_metrics gm ON s.id = gm.symbol_id
        {symbol_metrics_join}
        WHERE 1=1
    """.format(
        file_role_expr=file_role_expr,
        param_expr=param_expr,
        symbol_lines_expr=symbol_lines_expr,
        file_lines_expr=file_lines_expr,
        symbol_metrics_join=symbol_metrics_join,
    )
    return query


def _parse_match_requirements(match: dict) -> tuple[dict, re.Pattern | None, str | None]:
    """Parse and normalise the require-* parameters under ``match.require``.

    Returns ``(requires_dict, compiled_name_regex, regex_error)``. The
    error string is non-None when a name_regex was provided but failed to
    compile; callers should surface it as a violation.
    """
    require = match.get("require", {})
    requires = {
        "has_test": bool(require.get("has_test", False)),
        "max_params": _as_float_or_none(require.get("max_params")),
        "min_params": _as_float_or_none(require.get("min_params")),
        "max_symbol_lines": _as_float_or_none(require.get("max_symbol_lines")),
        "min_symbol_lines": _as_float_or_none(require.get("min_symbol_lines")),
        "max_file_lines": _as_float_or_none(require.get("max_file_lines")),
        "min_file_lines": _as_float_or_none(require.get("min_file_lines")),
    }
    compiled_regex = None
    regex_error = None
    require_name_regex = require.get("name_regex")
    if isinstance(require_name_regex, str) and require_name_regex.strip():
        try:
            compiled_regex = re.compile(require_name_regex)
        except re.error as exc:
            regex_error = str(exc)
    return requires, compiled_regex, regex_error


def _bound_check(label: str, value: float | None, max_v: float | None, min_v: float | None) -> list[str]:
    """Standard min/max bound check that returns the reason strings.

    Centralises the 'value unavailable / exceeds / is below' messaging that
    every numeric requirement (params, symbol_lines, file_lines) repeats.
    """
    if max_v is None and min_v is None:
        return []
    if value is None:
        return ["{} unavailable".format(label)]
    out: list[str] = []
    if max_v is not None and value > max_v:
        out.append("{} {:.0f} exceeds {:.0f}".format(label, value, max_v))
    if min_v is not None and value < min_v:
        out.append("{} {:.0f} is below {:.0f}".format(label, value, min_v))
    return out


def _row_violation_reasons(row, requires, compiled_regex, conn) -> list[str]:
    """Apply every require-* check to a single row and return the failures.

    Empty list means the row passes all requirements.
    """
    reasons: list[str] = []
    symbol_name = row["name"]
    if requires["has_test"] and not _symbol_has_test(conn, row["id"]):
        reasons.append("{} has no test coverage".format(symbol_name))
    if compiled_regex and not compiled_regex.search(symbol_name):
        reasons.append("name '{}' does not match {}".format(symbol_name, compiled_regex.pattern))
    reasons.extend(
        _bound_check(
            "parameter count",
            _as_float_or_none(row["param_count"]),
            requires["max_params"],
            requires["min_params"],
        )
    )
    reasons.extend(
        _bound_check(
            "symbol line count",
            _as_float_or_none(row["symbol_line_count"]),
            requires["max_symbol_lines"],
            requires["min_symbol_lines"],
        )
    )
    reasons.extend(
        _bound_check(
            "file line count",
            _as_float_or_none(row["file_line_count"]),
            requires["max_file_lines"],
            requires["min_file_lines"],
        )
    )
    return reasons


def _evaluate_symbol_match(rule: dict, conn) -> dict:
    """Evaluate a symbol_match rule: find symbols matching criteria.

    Supports requirement checks under ``match.require``:
    - ``has_test``: matched symbols must have test coverage
    - ``name_regex``: symbol name must match regex
    - ``max_params`` / ``min_params``
    - ``max_symbol_lines`` / ``min_symbol_lines``
    - ``max_file_lines`` / ``min_file_lines``
    """
    match = rule.get("match", {})
    exempt = rule.get("exempt", {})

    kind_filter = match.get("kind")
    exported_filter = match.get("exported")
    file_glob = match.get("file_glob")
    min_fan_in = match.get("min_fan_in")
    max_fan_in = match.get("max_fan_in")

    requires, compiled_regex, regex_error = _parse_match_requirements(match)
    if regex_error is not None:
        return {
            "name": rule.get("name", "unnamed"),
            "severity": rule.get("severity", "error"),
            "passed": False,
            "violations": [
                {
                    "symbol": "",
                    "file": rule.get("_file", ""),
                    "line": None,
                    "reason": "invalid require.name_regex: {}".format(regex_error),
                }
            ],
        }

    query = _build_symbol_match_query(conn)
    params: list = []
    if kind_filter:
        if isinstance(kind_filter, str):
            kind_filter = [kind_filter]
        placeholders = ",".join("?" for _ in kind_filter)
        query += f" AND s.kind IN ({placeholders})"
        params.extend(kind_filter)
    if exported_filter is True:
        query += " AND s.is_exported = 1"
    elif exported_filter is False:
        query += " AND s.is_exported = 0"

    rows = conn.execute(query, params).fetchall()

    has_requirements = any(
        [
            requires["has_test"],
            compiled_regex is not None,
            requires["max_params"] is not None,
            requires["min_params"] is not None,
            requires["max_symbol_lines"] is not None,
            requires["min_symbol_lines"] is not None,
            requires["max_file_lines"] is not None,
            requires["min_file_lines"] is not None,
        ]
    )

    violations: list[dict] = []
    for row in rows:
        file_path = row["file_path"]
        symbol_name = row["name"]
        in_deg = _as_float_or_none(row["in_degree"]) or 0.0

        if file_glob and not _matches_glob(file_path, file_glob):
            continue
        if min_fan_in is not None and in_deg < float(min_fan_in):
            continue
        if max_fan_in is not None and in_deg > float(max_fan_in):
            continue
        if _is_exempt(symbol_name, file_path, exempt):
            continue

        if has_requirements:
            reasons = _row_violation_reasons(row, requires, compiled_regex, conn)
            if not reasons:
                continue
            violations.append(
                {
                    "symbol": symbol_name,
                    "file": file_path,
                    "line": row["line_start"],
                    "reason": "; ".join(reasons),
                }
            )
        else:
            violations.append(
                {
                    "symbol": symbol_name,
                    "file": file_path,
                    "line": row["line_start"],
                    "reason": f"{symbol_name} matches rule criteria",
                }
            )

    return {
        "name": rule.get("name", "unnamed"),
        "severity": rule.get("severity", "error"),
        "passed": len(violations) == 0,
        "violations": violations,
    }


def _symbol_has_test(conn, symbol_id: int) -> bool:
    """Check if a symbol has edges from test files."""
    rows = conn.execute(
        """
        SELECT 1 FROM edges e
        JOIN symbols s ON e.source_id = s.id
        JOIN files f ON s.file_id = f.id
        WHERE e.target_id = ?
          AND (f.file_role = 'test' OR f.path LIKE '%%test%%')
        LIMIT 1
    """,
        (symbol_id,),
    ).fetchall()
    return len(rows) > 0


# ---------------------------------------------------------------------------
# Rule evaluation: ast_match
# ---------------------------------------------------------------------------


def _format_capture_preview(captures: dict[str, str]) -> str:
    """Format captured metavariables for rule output."""
    if not captures:
        return ""
    parts: list[str] = []
    for name in sorted(captures.keys()):
        text = " ".join(captures[name].split())
        if len(text) > 40:
            text = text[:37] + "..."
        parts.append("${}={}".format(name, text))
    return ", ".join(parts)


def _ast_match_failure(name: str, severity: str, rule: dict, reason: str) -> dict:
    """Build the standard failure result for an ast_match rule."""
    return {
        "name": name,
        "severity": severity,
        "passed": False,
        "violations": [
            {
                "symbol": "",
                "file": rule.get("_file", ""),
                "line": None,
                "reason": reason,
            }
        ],
    }


def _ast_match_project_root() -> Path:
    """Return the root used to resolve indexed relative paths."""
    try:
        return find_project_root()
    except OSError:
        return Path.cwd()


def _ast_match_detected_language(
    rel_path: str,
    *,
    file_glob,
    exempt: dict,
    language_filter: str | None,
) -> str | None:
    """Return the detected language for an eligible ast_match file."""
    if file_glob and not _matches_glob(rel_path, file_glob):
        return None
    if _is_exempt("", rel_path, exempt):
        return None

    detected_lang = normalize_language_name(detect_language(rel_path))
    if language_filter and detected_lang != language_filter:
        return None
    return detected_lang


def _ast_match_parsed_candidate(
    root: Path,
    rel_path: str,
    *,
    file_glob,
    exempt: dict,
    language_filter: str | None,
):
    """Parse an eligible ast_match file and return its active language."""
    detected_lang = _ast_match_detected_language(
        rel_path,
        file_glob=file_glob,
        exempt=exempt,
        language_filter=language_filter,
    )
    if detected_lang is None:
        return None

    tree, source, parsed_lang = parse_file(root / rel_path, detected_lang)
    if tree is None or source is None:
        return None

    active_lang = normalize_language_name(parsed_lang or detected_lang or language_filter)
    if active_lang is None:
        return None
    return tree, source, active_lang


def _cached_ast_pattern(
    pattern: str,
    active_lang: str,
    compiled_cache: dict[str, object],
) -> tuple[object | None, str | None]:
    """Compile an AST pattern once per active language."""
    compiled = compiled_cache.get(active_lang)
    if compiled is not None:
        return compiled, None

    try:
        compiled = compile_ast_pattern(pattern, active_lang)
    except Exception as exc:  # noqa: BLE001 - user rule/parser failures become rule output
        return None, "AST pattern compile failed: {}".format(exc)

    compiled_cache[active_lang] = compiled
    return compiled, None


def _ast_match_violation(pattern: str, rel_path: str, match: dict) -> dict:
    """Convert one AST match into the public violation shape."""
    cap_text = _format_capture_preview(match.get("captures", {}))
    reason = "AST pattern matched: {}".format(pattern)
    if cap_text:
        reason += " ({})".format(cap_text)

    return {
        "symbol": "",
        "file": rel_path,
        "line": match.get("line"),
        "reason": reason,
        "captures": match.get("captures", {}),
    }


def _append_ast_match_violations(
    pattern: str,
    rel_path: str,
    tree,
    source,
    compiled,
    violations: list[dict],
    max_matches: int,
) -> bool:
    """Append matches for one file and return True when the limit is reached."""
    remaining = 0
    if max_matches > 0:
        remaining = max_matches - len(violations)
        if remaining <= 0:
            return True

    matches = find_ast_matches(
        tree,
        source,
        compiled,
        max_matches=remaining,
    )
    for m in matches:
        violations.append(_ast_match_violation(pattern, rel_path, m))
        if max_matches > 0 and len(violations) >= max_matches:
            return True
    return False


def _evaluate_ast_match(rule: dict, conn) -> dict:
    """Evaluate an ast_match rule: structural pattern matching with metavars.

    Rule shape:

    type: ast_match
    match:
      ast: "deprecated_call($EXPR)"
      language: python
      file_glob: "**/*.py"
      max_matches: 100
    """
    match = rule.get("match", {})
    exempt = rule.get("exempt", {})

    pattern = match.get("ast")
    if pattern is None and rule.get("type") == "ast_match":
        # Compatibility: allow `match.pattern` when type is explicit.
        pattern = match.get("pattern")

    language_filter = normalize_language_name(match.get("language"))
    file_glob = match.get("file_glob")
    max_matches = int(match.get("max_matches", 0) or 0)

    name = rule.get("name", "unnamed")
    severity = rule.get("severity", "error")

    if not isinstance(pattern, str) or not pattern.strip():
        return _ast_match_failure(
            name,
            severity,
            rule,
            "ast_match rule missing non-empty match.ast pattern",
        )

    root = _ast_match_project_root()
    rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
    violations: list[dict] = []
    compiled_cache: dict[str, object] = {}

    for row in rows:
        rel_path = row["path"]
        parsed = _ast_match_parsed_candidate(
            root,
            rel_path,
            file_glob=file_glob,
            exempt=exempt,
            language_filter=language_filter,
        )
        if parsed is None:
            continue
        tree, source, active_lang = parsed

        compiled, compile_error = _cached_ast_pattern(pattern, active_lang, compiled_cache)
        if compile_error is not None:
            return _ast_match_failure(name, severity, rule, compile_error)

        if _append_ast_match_violations(
            pattern,
            rel_path,
            tree,
            source,
            compiled,
            violations,
            max_matches,
        ):
            break

    return {
        "name": name,
        "severity": severity,
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Rule evaluation: dataflow_match
# ---------------------------------------------------------------------------


# Optional violation fields copied from a dataflow finding when present.
_DATAFLOW_VIOLATION_OPTIONAL_FIELDS = (
    "variable",
    "source",
    "sink",
    "confidence",
    "chain_length",
)


def _resolve_dataflow_patterns(match: dict) -> list | str | None:
    """Resolve the pattern list for a dataflow_match rule.

    ``patterns`` wins; otherwise fall back to singular ``pattern`` or the
    legacy ``dataflow`` key.
    """
    patterns = match.get("patterns")
    if patterns is None:
        # Compatibility aliases:
        # - singular "pattern"
        # - "dataflow" value
        patterns = match.get("pattern", match.get("dataflow"))
    return patterns


def _filter_findings_by_threshold(
    findings: list[dict],
    key: str,
    value,
    cast,
    predicate,
) -> list[dict]:
    """Apply a numeric threshold filter, ignoring unparseable values."""
    if value is None:
        return findings
    try:
        bound = cast(value)
    except (TypeError, ValueError):
        return findings
    return [f for f in findings if predicate(f.get(key), bound)]


def _violation_from_finding(item: dict, exempt: dict) -> dict | None:
    """Build a violation dict from a dataflow finding, or None if exempt."""
    symbol_name = item.get("symbol", "")
    file_path = item.get("file", "")
    if _is_exempt(symbol_name, file_path, exempt):
        return None

    violation = {
        "symbol": symbol_name,
        "file": file_path,
        "line": item.get("line"),
        "reason": item.get("reason", "dataflow rule matched"),
        "type": item.get("type"),
    }
    violation.update(
        {field: item[field] for field in _DATAFLOW_VIOLATION_OPTIONAL_FIELDS if field in item}
    )
    return violation


def _evaluate_dataflow_match(rule: dict, conn) -> dict:
    """Evaluate a dataflow_match rule using intra- and inter-procedural heuristics.

    Rule shape:

    type: dataflow_match
    match:
      patterns: [dead_assignment, unused_param, source_to_sink,
                 inter_source_to_sink, inter_unused_param, inter_unused_return]
      file_glob: "**/*.py"
      max_matches: 100
      sources: ["input(", "request.args"]
      sinks: ["os.system(", "subprocess.run("]
      sanitizers: ["escape(", "sanitize("]
      max_chain_length: 5
      min_confidence: 0.5
    """
    match = rule.get("match", {})
    exempt = rule.get("exempt", {})

    patterns = _resolve_dataflow_patterns(match)
    file_glob = match.get("file_glob")
    max_matches = int(match.get("max_matches", 0) or 0)
    sources = match.get("sources")
    sinks = match.get("sinks")

    findings = collect_dataflow_findings(
        conn,
        patterns=patterns,
        file_glob=file_glob,
        max_matches=max_matches if max_matches > 0 else 0,
        sources=sources,
        sinks=sinks,
    )

    findings = _filter_findings_by_threshold(
        findings,
        "chain_length",
        match.get("max_chain_length"),
        int,
        lambda chain_length, bound: chain_length <= bound,
    )
    findings = _filter_findings_by_threshold(
        findings,
        "confidence",
        match.get("min_confidence"),
        float,
        lambda confidence, bound: confidence >= bound,
    )

    violations: list[dict] = []
    for item in findings:
        violation = _violation_from_finding(item, exempt)
        if violation is not None:
            violations.append(violation)

    if max_matches > 0:
        violations = violations[:max_matches]

    return {
        "name": rule.get("name", "unnamed"),
        "severity": rule.get("severity", "error"),
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Rule type detection + dispatch
# ---------------------------------------------------------------------------


def _detect_rule_type(rule: dict) -> str:
    """Detect the rule type from its match specification.

    If ``type`` is explicitly set, it wins.
    Otherwise:
    - ``must`` / ``must_not`` block => graph_clause (R18)
    - ``from`` + ``to``             => path_match
    - ``ast``                       => ast_match
    - ``dataflow``                  => dataflow_match
    - fallback                      => symbol_match
    """
    explicit = rule.get("type")
    if isinstance(explicit, str) and explicit:
        return explicit

    # R18: a rule carrying a `must:` or `must_not:` block is a graph-clause
    # rule even when it also has a `when:` filter — the new clauses live
    # only in those blocks. Detect them before the legacy path/ast/dataflow
    # checks so a `when: {pattern: ...}` doesn't fall through to symbol_match.
    if isinstance(rule.get("must"), dict) or isinstance(rule.get("must_not"), dict):
        return "graph_clause"

    match = rule.get("match", {})
    if "from" in match and "to" in match:
        return "path_match"
    if "ast" in match:
        return "ast_match"
    if "dataflow" in match or "patterns" in match and isinstance(match.get("patterns"), list):
        return "dataflow_match"
    return "symbol_match"


# ---------------------------------------------------------------------------
# Rule evaluation: graph_clause (R18)
# ---------------------------------------------------------------------------


def _candidate_files_for_when(conn, when: dict) -> list[dict]:
    """Return file rows matching the ``when.pattern`` glob (R18 candidates)."""
    pattern = when.get("pattern") or when.get("file_glob")
    if not pattern:
        return []
    rows = conn.execute("SELECT id, path FROM files").fetchall()
    return [dict(r) for r in rows if _matches_glob(r["path"], pattern)]


def _candidate_symbols_for_when(conn, when: dict) -> list[dict]:
    """Return symbol rows matching the ``when`` clause.

    Supported keys: ``symbol`` (exact qname/name), ``symbol_kind`` (kind
    filter; ``public_api`` is treated as is_exported=1).
    """
    name = when.get("symbol")
    kind = when.get("symbol_kind") or when.get("kind")
    pattern = when.get("pattern") or when.get("file_glob")
    sql = (
        "SELECT s.id, s.name, s.qualified_name, s.kind, s.line_start, "
        "s.is_exported, f.path AS file_path "
        "FROM symbols s JOIN files f ON s.file_id = f.id WHERE 1=1"
    )
    params: list = []
    if name:
        sql += " AND (s.qualified_name = ? OR s.name = ?)"
        params.extend([name, name])
    if kind == "public_api":
        sql += " AND s.is_exported = 1"
    elif kind:
        if isinstance(kind, str):
            kind = [kind]
        ph = ",".join("?" for _ in kind)
        sql += f" AND s.kind IN ({ph})"
        params.extend(kind)
    rows = conn.execute(sql, params).fetchall()
    if pattern:
        rows = [r for r in rows if _matches_glob(r["file_path"], pattern)]
    return [dict(r) for r in rows]


def _clause_rule_error(name: str, severity: str, rule: dict, clause: str, reason: str) -> dict:
    """Failure envelope for a graph-clause rule that cannot be evaluated."""
    return {
        "name": name,
        "severity": severity,
        "passed": False,
        "partial_success": True,
        "violations": [
            {
                "symbol": "",
                "file": rule.get("_file", ""),
                "line": None,
                "clause": clause,
                "reason": reason,
            }
        ],
    }


def _graph_clause_max_depth(rule: dict, default_depth: int) -> int:
    """Apply the per-rule depth override used by graph-clause rules."""
    rule_depth = rule.get("depth")
    if isinstance(rule_depth, (int, float)) and int(rule_depth) > 0:
        return int(rule_depth)
    return default_depth


def _graph_clause_result(name: str, severity: str, violations: list[dict], partial: bool) -> dict:
    """Build the standard graph-clause result envelope."""
    result = {
        "name": name,
        "severity": severity,
        "passed": len(violations) == 0,
        "violations": violations,
    }
    if partial:
        result["partial_success"] = True
    return result


def _graph_clause_targets(conn, when: dict, exempt: dict) -> list[tuple[str, str, int | None, dict]] | None:
    """Normalize `when`-matched candidates into (label, file, line, candidate) tuples.

    Symbol-scoped rules yield one tuple per matching symbol; file-scoped
    rules yield one per matching file (empty label, no line). Returns
    ``None`` when the rule carries no `when` filter at all.
    """
    if "symbol" in when or "symbol_kind" in when:
        return [
            (
                sym.get("qualified_name") or sym.get("name"),
                sym.get("file_path"),
                sym.get("line_start"),
                sym,
            )
            for sym in _candidate_symbols_for_when(conn, when)
            if not _is_exempt(sym["name"], sym["file_path"], exempt)
        ]
    if "pattern" in when or "file_glob" in when:
        return [
            ("", f["path"], None, {"file_path": f["path"]})
            for f in _candidate_files_for_when(conn, when)
            if not _is_exempt("", f["path"], exempt)
        ]
    return None


def _build_clause_plan(must, must_not) -> tuple[list[tuple[str, str, str]], str | None]:
    """Flatten must/must_not blocks into (block_kind, clause, arg) triples.

    Returns ``(plan, unknown_clause_name)`` — the second element is set (and
    the plan invalid) when a block names a clause outside SUPPORTED_CLAUSES.
    """
    from roam.policy.graph_clauses import SUPPORTED_CLAUSES

    plan: list[tuple[str, str, str]] = []
    for block_kind, block in (("must", must), ("must_not", must_not)):
        if not isinstance(block, dict):
            continue
        for cname, carg in block.items():
            if cname not in SUPPORTED_CLAUSES:
                return [], cname
            plan.append((block_kind, cname, str(carg)))
    return plan, None


def _eval_plan_for_target(
    conn,
    plan: list[tuple[str, str, str]],
    label: str,
    target_file: str,
    line: int | None,
    candidate: dict,
    *,
    max_depth: int,
    max_nodes: int,
    message: str,
) -> tuple[list[dict], bool]:
    """Run every clause in the plan against one candidate.

    Returns ``(violations, partial)`` — partial is True when any clause
    reported a non-ok evidence status.
    """
    from roam.policy.graph_clauses import evaluate_clause

    violations: list[dict] = []
    partial = False
    for block_kind, cname, carg in plan:
        matches, evidence = evaluate_clause(
            cname,
            carg,
            conn=conn,
            target_symbol=label or None,
            target_file=target_file,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        status = evidence.get("status", "ok") if isinstance(evidence, dict) else "ok"
        if status not in ("ok",):
            partial = True
        # must => clause must hold; must_not => clause must NOT hold.
        fired = (block_kind == "must" and not matches) or (block_kind == "must_not" and matches)
        if fired:
            violations.append(
                {
                    "symbol": label,
                    "file": target_file,
                    "line": line,
                    "clause": cname,
                    "block": block_kind,
                    "evidence": evidence,
                    "reason": _format_clause_reason(block_kind, cname, carg, candidate, evidence, message),
                }
            )
    return violations, partial


def _eval_plan_for_targets(
    conn,
    plan: list[tuple[str, str, str]],
    targets: list[tuple[str, str, int | None, dict]],
    *,
    max_depth: int,
    max_nodes: int,
    message: str,
) -> tuple[list[dict], bool]:
    """Run a graph-clause plan against every resolved target."""
    violations: list[dict] = []
    partial = False
    for label, target_file, line, candidate in targets:
        target_violations, target_partial = _eval_plan_for_target(
            conn,
            plan,
            label,
            target_file,
            line,
            candidate,
            max_depth=max_depth,
            max_nodes=max_nodes,
            message=message,
        )
        violations.extend(target_violations)
        partial = partial or target_partial
    return violations, partial


def _evaluate_graph_clause(rule: dict, conn, *, max_depth: int = 3, max_nodes: int = 100) -> dict:
    """Evaluate an R18 graph-clause rule.

    Rule shape::

        when:
          pattern: "src/handlers/**.py"   # file glob (file-scoped clauses)
          symbol: "create_order"          # OR a specific symbol
          symbol_kind: "public_api"       # OR a kind filter
        must:
          reachable_from: "src/db/__init__.py"
        must_not:
          imports_from: "src/legacy"
        severity: high
        message: "Handlers must use the canonical DB layer"

    A single rule may carry either ``must`` or ``must_not`` (or both).
    Each must-block contains exactly ONE of the four supported clause
    names (``reachable_from`` / ``imports_from`` / ``clones_with`` /
    ``tested_by``).
    """
    name = rule.get("name") or rule.get("id") or "unnamed"
    severity = rule.get("severity", "error")
    message = rule.get("message", "")
    when = rule.get("when") or {}
    must = rule.get("must") or {}
    must_not = rule.get("must_not") or {}
    exempt = rule.get("exempt", {})

    # Per-rule depth override (defensive — the CLI normally controls this).
    max_depth = _graph_clause_max_depth(rule, max_depth)

    plan, unknown = _build_clause_plan(must, must_not)
    if unknown is not None:
        # Lazy import to keep the rules engine importable without the policy
        # package on bare installs.
        from roam.policy.graph_clauses import SUPPORTED_CLAUSES

        return _clause_rule_error(
            name,
            severity,
            rule,
            unknown,
            f"unknown clause '{unknown}' — supported: {', '.join(SUPPORTED_CLAUSES)}",
        )

    if not plan:
        return {
            "name": name,
            "severity": severity,
            "passed": True,
            "violations": [],
            "reason": "rule has no must / must_not clauses",
        }

    # Symbol-scoped rules iterate matching symbols; file-scoped rules iterate
    # matching files; a rule with no `when` filter cannot be evaluated.
    targets = _graph_clause_targets(conn, when, exempt)
    if targets is None:
        return _clause_rule_error(
            name,
            severity,
            rule,
            "",
            "graph_clause rule has no `when` filter — add "
            "`when: {pattern: '...'}` or `when: {symbol: '...'}` "
            "to scope the rule",
        )

    violations, partial = _eval_plan_for_targets(
        conn,
        plan,
        targets,
        max_depth=max_depth,
        max_nodes=max_nodes,
        message=message,
    )
    return _graph_clause_result(name, severity, violations, partial)


def _format_clause_reason(block_kind, cname, carg, candidate, evidence, message):
    """One-line, paste-able violation reason that includes the message."""
    where = candidate.get("file_path") or candidate.get("file") or "?"
    sym = candidate.get("qualified_name") or candidate.get("name") or ""
    head = sym if sym else where
    status = (evidence or {}).get("status") if isinstance(evidence, dict) else None
    if status and status != "ok":
        return f"{head}: clause `{cname}` could not run ({status})"
    if block_kind == "must":
        base = f"{head}: must {cname}({carg}) — FAILED"
    else:
        base = f"{head}: must_not {cname}({carg}) — VIOLATED"
    if message:
        base += f" — {message}"
    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_rule(rule: dict, conn, G=None, *, max_depth: int = 3, max_nodes: int = 100) -> dict:
    """Evaluate a single rule against the indexed DB.

    Returns ``{name, severity, passed, violations: [{symbol, file, line, reason}]}``.

    ``max_depth`` / ``max_nodes`` apply to graph-aware clauses (R18); they
    are ignored by the legacy rule types.
    """
    # Handle parse errors
    if "_error" in rule:
        return {
            "name": rule.get("name", "unknown"),
            "severity": rule.get("severity", "error"),
            "passed": False,
            "violations": [
                {
                    "symbol": "",
                    "file": rule.get("_file", ""),
                    "line": None,
                    "reason": rule["_error"],
                }
            ],
        }

    rule_type = _detect_rule_type(rule)

    if rule_type == "path_match":
        return _evaluate_path_match(rule, conn)
    if rule_type == "ast_match":
        return _evaluate_ast_match(rule, conn)
    if rule_type == "dataflow_match":
        return _evaluate_dataflow_match(rule, conn)
    if rule_type == "graph_clause":
        return _evaluate_graph_clause(rule, conn, max_depth=max_depth, max_nodes=max_nodes)
    return _evaluate_symbol_match(rule, conn)


def evaluate_all(
    rules_dir: Path,
    conn,
    *,
    max_depth: int = 3,
    max_nodes: int = 100,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Load and evaluate all rules from the rules directory.

    Returns a list of result dicts, one per rule.

    W1036 (Pattern 2 — silent fallback, sibling of W1051 + W1052):
    ``warnings_out`` is plumbed through to :func:`load_rules` so each
    silent-fallback path inside the per-file loader appends a
    structured warning. Pre-W1036 callers (no accumulator) get
    byte-identical behaviour — every parse failure still surfaces via
    the existing ``_error`` placeholder rule.

    W1030-followup-F: thin wrapper over :func:`evaluate_all_with_status`
    that drops the directory-level ``LoadStatus`` rollup so pre-W1030-followup-F
    callers stay byte-identical.
    """
    results, _status = evaluate_all_with_status(
        rules_dir,
        conn,
        max_depth=max_depth,
        max_nodes=max_nodes,
        warnings_out=warnings_out,
    )
    return results


def evaluate_all_with_status(
    rules_dir: Path,
    conn,
    *,
    max_depth: int = 3,
    max_nodes: int = 100,
    warnings_out: WarningsOut = None,
) -> tuple[list[dict], str]:
    """W1030-followup-F: evaluate all rules and return ``(results, status)``.

    Mirrors :func:`load_rules_with_status` but extends it with the
    per-rule evaluation pass. Callers that need the on-disk state for
    envelope disambiguation (``cmd_rules``) use this entry point; the
    legacy :func:`evaluate_all` keeps the single-list return for
    byte-identical pre-W1030-followup-F callers.
    """
    rules, status = load_rules_with_status(rules_dir, warnings_out=warnings_out)
    results: list[dict] = []
    for rule in rules:
        results.append(evaluate_rule(rule, conn, max_depth=max_depth, max_nodes=max_nodes))
    return results, status
