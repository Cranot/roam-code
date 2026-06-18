"""Token-efficient text formatting for AI consumption."""

from __future__ import annotations

import json as _json
import os
import time
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, TypeAlias

# Envelope schema versioning (semver: major.minor.patch)
# bumped to 1.1.0 to signal additive enhancements:
# `evidence.matched_patterns` on detector findings,
# `framework`/`framework_autodetected`/`framework_unknown` in math summary
# , `roi_band` on debt items, `context_lines` on rule
# violations + concerns (D6). All optional — pre-1.1 consumers continue
# to work; new consumers can opt in to the richer fields.
ENVELOPE_SCHEMA_VERSION = "1.1.0"
ENVELOPE_SCHEMA_NAME = "roam-envelope-v1"

# Pattern-2 silent-fallback warnings accumulator type. W1043 alias for
# `list[str] | None`. Callers pass an empty list to opt into structured
# warning collection; passing None preserves byte-identical legacy
# silent-empty behaviour. See (internal memo) (W1039)
# for the idiom and (internal memo) (W1016) for the
# canonical loader helper that owns the warning format.
WarningsOut: TypeAlias = list[str] | None

_NON_CACHEABLE_COMMANDS = {
    "mutate",
    "annotate",
    "ingest-trace",
    "vuln-map",
    "reset",
    "clean",
    "index",
    "init",
}
_VOLATILE_COMMANDS = {"diff", "pr-risk", "pr-diff", "affected", "affected-tests", "weather"}

# Commands whose envelopes should NOT be written to .roam/responses/ even when
# ROAM_RUN_ID is set. These either log the act of logging (creating feedback
# loops) or own the responses directory themselves (pr-bundle auto-collect
# would double-count its own emit envelope).
_EXCLUDED_COMMANDS_FROM_RESPONSES_WRITE = {
    # runs telemetry — already persisted to .roam/runs/
    "runs-start",
    "runs-log",
    "runs-end",
    "runs-list",
    "runs-show",
    # agent memory — already persisted to .roam/memory.jsonl
    "memory-add",
    "memory-list",
    "memory-relevant",
    # constitution — wave 10.1 owns its own persistence
    "constitution-init",
    "constitution-check",
    "constitution-show",
    "constitution-apply",
    "constitution-where",
    # pr-bundle reads .roam/responses/; writing its own envelopes here would
    # double-count on subsequent auto-collect runs. Covers all command_label
    # values emitted by `_build_envelope` in cmd_pr_bundle.py — see that file
    # if a new subcommand label appears.
    "pr-bundle",
    "pr-bundle-init",
    "pr-bundle-emit",
    "pr-bundle-validate",
    "pr-bundle-add",
    "pr-bundle-set",
    "pr-bundle-set-intent",
    "pr-bundle-add-affected",
    "pr-bundle-add-risk",
    "pr-bundle-add-test-required",
    "pr-bundle-add-test-run",
    "pr-bundle-add-non-goal",
    "pr-bundle-add-context-cmd",
    "pr-bundle-add-context-symbol",
    "pr-bundle-add-context-file",
}

KIND_ABBREV = {
    "function": "fn",
    "class": "cls",
    "method": "meth",
    "variable": "var",
    "constant": "const",
    "interface": "iface",
    "struct": "struct",
    "enum": "enum",
    "module": "mod",
    "package": "pkg",
    "trait": "trait",
    "type_alias": "type",
    "property": "prop",
    "field": "field",
    "constructor": "ctor",
    "decorator": "deco",
}


def abbrev_kind(kind: str) -> str:
    return KIND_ABBREV.get(kind, kind)


def loc(path: str, line: int | None = None) -> str:
    if line is not None:
        return f"{path}:{line}"
    return path


def symbol_line(
    name: str, kind: str, signature: str | None, path: str, line: int | None = None, extra: str = ""
) -> str:
    parts = [abbrev_kind(kind), name]
    if signature:
        parts.append(signature)
    parts.append(loc(path, line))
    if extra:
        parts.append(extra)
    return "  ".join(parts)


def section(title: str, lines: list[str], budget: int = 0) -> str:
    out = [title]
    if budget and len(lines) > budget:
        out.extend(lines[:budget])
        out.append(f"  (+{len(lines) - budget} more)")
    else:
        out.extend(lines)
    return "\n".join(out)


def indent(text: str, level: int = 1) -> str:
    prefix = "  " * level
    return "\n".join(prefix + line for line in text.splitlines())


def truncate_lines(lines: list[str], budget: int) -> list[str]:
    if len(lines) <= budget:
        return lines
    return lines[:budget] + [f"(+{len(lines) - budget} more)"]


def format_signature(sig: str | None, max_len: int = 80) -> str:
    if not sig:
        return ""
    sig = sig.strip()
    if len(sig) > max_len:
        return sig[: max_len - 3] + "..."
    return sig


def format_edge_kind(kind: str) -> str:
    return kind.replace("_", " ")


def format_table(headers: list[str], rows: list[list[str]], budget: int = 0) -> str:
    """Render a 2-column-spaced left-aligned text table.

    Single-pass column-width computation: walks the displayed rows exactly
    once, stringifying each cell on the way (so the emit pass below does
    not re-do ``str(cell)`` work) and updating per-column widths inline.

    Output is byte-identical to the previous implementation: the only
    behavioural quirks preserved are
    (a) cells past ``len(headers)`` are still rendered (and contribute to
        the row's emit, but never widen the table) — same as before; and
    (b) trailing missing cells in a short row do not get padded, but
        non-final visible cells do (because they were ``ljust``-ed against
        the column width) — same as before.
    """
    if not rows:
        return "(none)"

    num_cols = len(headers)
    truncated = bool(budget) and len(rows) > budget
    display_count = budget if truncated else len(rows)

    # Single pass over ALL rows: stringify cells once and accumulate
    # per-column widths inline. We keep the str-versions of *display_rows*
    # only (no need to retain stringified rows we will not emit), but
    # widths are computed from every row so output matches the original
    # implementation byte-for-byte even when budget < len(rows).
    widths = [len(h) for h in headers]
    str_rows: list[list[str]] = []
    for idx, row in enumerate(rows):
        srow = [str(cell) for cell in row]
        if idx < display_count:
            str_rows.append(srow)
        # Manual loop beats enumerate()+max() — only writes when wider.
        upper = num_cols if len(srow) >= num_cols else len(srow)
        for i in range(upper):
            cell_len = len(srow[i])
            if cell_len > widths[i]:
                widths[i] = cell_len

    # Emit phase — uses pre-stringified cells; no second str()/len() pass.
    out_lines: list[str] = []
    out_lines.append("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    out_lines.append("  ".join("-" * w for w in widths))
    for srow in str_rows:
        # Match original semantics exactly: enumerate(srow) and ljust
        # against widths[i]. Rows wider than the header crashed in the
        # original (IndexError) and continue to crash here.
        out_lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(srow)))
    if truncated:
        out_lines.append(f"(+{len(rows) - budget} more)")
    return "\n".join(out_lines)


def to_json(data) -> str:
    """Serialize data to a JSON string with deterministic key ordering.

    Uses ``sort_keys=True`` so that identical data always produces
    byte-identical output — critical for LLM prompt-caching compatibility.
    """
    return _json.dumps(data, indent=2, default=str, sort_keys=True)


# -- Token budget truncation ------------------------------------------

# Conservative heuristic: 1 token ~ 4 characters (works for English + code).
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count from character length (1 token ~ 4 chars)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def budget_truncate(text: str, budget: int) -> str:
    """Truncate plain-text output to fit within a token budget.

    If *budget* is 0 or the text already fits, returns *text* unchanged.
    Otherwise, truncates to the last complete line within the character
    limit and appends a truncation notice.

    Parameters
    ----------
    text:
        The full output text.
    budget:
        Maximum output tokens (0 = unlimited).
    """
    if budget <= 0:
        return text

    char_limit = budget * _CHARS_PER_TOKEN

    if len(text) <= char_limit:
        return text

    # Truncate and find last complete line
    truncated = text[:char_limit]
    last_newline = truncated.rfind("\n")
    if last_newline > char_limit * 0.8:
        truncated = truncated[:last_newline]

    full_tokens = estimate_tokens(text)
    truncated += f"\n\n... truncated (budget: {budget} tokens, full output: ~{full_tokens} tokens)"
    return truncated


# Keys recognised as importance indicators (checked in priority order).
_IMPORTANCE_KEYS = ("pagerank", "importance", "score", "rank")


def _sort_by_importance(items: list) -> tuple[list, bool]:
    """Sort list items by importance descending if they carry an importance key.

    Returns ``(sorted_list, was_sorted)``.  When no recognised importance
    key is found in the first dict item, the original order is preserved
    and ``was_sorted`` is ``False``.
    """
    if not items:
        return items, False

    # Only attempt importance-sorting on lists of dicts
    first = items[0]
    if not isinstance(first, dict):
        return items, False

    # Find the importance key present in items
    imp_key: str | None = None
    for candidate in _IMPORTANCE_KEYS:
        if candidate in first:
            imp_key = candidate
            break

    if imp_key is None:
        return items, False

    # Sort descending by importance (highest first → kept on truncation)
    try:
        sorted_items = sorted(
            items,
            key=lambda d: d.get(imp_key, 0) if isinstance(d, dict) else 0,
            reverse=True,
        )
        return sorted_items, True
    except (TypeError, ValueError):
        return items, False


def _copy_envelope_mutable(data: dict) -> dict:
    """Shallow-copy envelope; nested dicts and lists get a one-level mutable copy."""
    result: dict = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = dict(v)
        elif isinstance(v, list):
            result[k] = list(v)
        else:
            result[k] = v
    return result


def _presort_list_fields(result: dict, preserved: set) -> bool:
    """Sort non-preserved list fields by importance in-place. Returns True if any field was sorted."""
    any_sorted = False
    for key, value in list(result.items()):
        if key in preserved:
            continue
        if isinstance(value, list):
            sorted_val, was_sorted = _sort_by_importance(value)
            if was_sorted:
                result[key] = sorted_val
                any_sorted = True
    return any_sorted


def _cap_lists_to_budget(result: dict, preserved: set, char_limit: int) -> None:
    """Progressively shrink non-preserved list fields (10→5→3→1) until result fits."""
    for cap in (10, 5, 3, 1):
        for key, value in list(result.items()):
            if key in preserved:
                continue
            if isinstance(value, list) and len(value) > cap:
                result[key] = value[:cap]
        if len(_json.dumps(result, default=str, sort_keys=True)) <= char_limit:
            return


def _drop_fields_to_budget(result: dict, preserved: set, char_limit: int) -> None:
    """Drop non-preserved keys one-by-one until result fits within char_limit."""
    if len(_json.dumps(result, default=str, sort_keys=True)) <= char_limit:
        return
    for k in [k for k in result if k not in preserved]:
        del result[k]
        if len(_json.dumps(result, default=str, sort_keys=True)) <= char_limit:
            break


def _count_omitted(data: dict, result: dict, preserved: set) -> int:
    """Count total list items omitted from non-preserved fields."""
    total = 0
    for key in data:
        if key in preserved:
            continue
        orig = data.get(key)
        kept = result.get(key)
        if isinstance(orig, list):
            total += len(orig) - (len(kept) if isinstance(kept, list) else 0)
    return total


def _annotate_truncation(
    result: dict, budget: int, full_json: str, total_omitted: int, importance_sorted: bool
) -> None:
    """Stamp truncation metadata onto result[\"summary\"]."""
    if "summary" not in result or not isinstance(result["summary"], dict):
        return
    s = result["summary"]
    s["truncated"] = True
    s["budget_tokens"] = budget
    s["full_output_tokens"] = estimate_tokens(full_json)
    if total_omitted > 0:
        s["omitted_low_importance_nodes"] = total_omitted
    if importance_sorted:
        s["kept_highest_importance"] = True


def budget_truncate_json(data: dict, budget: int) -> dict:
    """Truncate a JSON envelope intelligently within a token budget.

    Strategy:
    - Always preserve envelope fields: command, summary, schema,
      schema_version, version, project, _meta.
    - For list-valued payload fields, sort by importance (``pagerank``,
      ``importance``, ``score``, or ``rank`` key) descending, then keep
      only the top N items until the result fits.  Lists without a
      recognised importance key fall back to positional truncation.
    - Annotates summary with ``truncated=True``, ``budget_tokens``,
      ``omitted_low_importance_nodes``, and ``kept_highest_importance``.

    If *budget* is 0 or the serialized dict already fits, returns
    *data* unchanged.

    Parameters
    ----------
    data:
        A dict produced by :func:`json_envelope`.
    budget:
        Maximum output tokens (0 = unlimited).
    """
    if budget <= 0:
        return data

    full_json = _json.dumps(data, default=str, sort_keys=True)
    char_limit = budget * _CHARS_PER_TOKEN

    if len(full_json) <= char_limit:
        return data

    preserved = {
        "command",
        "summary",
        "schema",
        "schema_version",
        "version",
        "project",
        "_meta",
    }

    result = _copy_envelope_mutable(data)
    importance_sorted = _presort_list_fields(result, preserved)
    _cap_lists_to_budget(result, preserved, char_limit)
    _drop_fields_to_budget(result, preserved, char_limit)
    total_omitted = _count_omitted(data, result, preserved)
    _annotate_truncation(result, budget, full_json, total_omitted, importance_sorted)

    return result


def _compact_mode_enabled() -> bool:
    """Return True when CLI requested compact/agent output mode."""
    try:
        import click

        ctx = click.get_current_context(silent=True)
        if ctx and isinstance(ctx.obj, dict):
            return bool(ctx.obj.get("compact") or ctx.obj.get("agent"))
    except (ImportError, RuntimeError):
        # W677: narrowed from `except Exception` — ImportError covers the
        # `import click` path for non-CLI callers without click installed;
        # RuntimeError covers click.get_current_context edge cases where no
        # active context exists. Programmer-class errors (NameError /
        # AttributeError / TypeError) propagate per W531 fail-loud.
        pass
    return False


# Bounds for the derived agent_contract block. Total target ~200 tokens
# so the block stays useful for tight-context clients without becoming
# yet another bulky payload.
_AGENT_CONTRACT_MAX_FACTS = 5
_AGENT_CONTRACT_MAX_RISKS = 3
_AGENT_CONTRACT_MAX_NEXT = 5
_AGENT_CONTRACT_STR_TRUNCATE = 120

# Keys in the envelope payload that conventionally carry "things that
# went wrong" — used to populate the ``risks`` list. Order is preference;
# the first non-empty list wins.
_RISK_KEYS = (
    "errors",
    "violations",
    "blockers",
    "issues",
    "findings",
)


def _stringify_risk_item(item) -> str:
    """Pull a short human-readable string out of an envelope risk-item.

    ``agent_contract.risks[]`` must hold short, LAW-4-anchored fact
    STRINGS — never a Python ``repr()`` dict dump. Three input shapes
    are normalised here:

    1. **Bare string** — returned as-is.
    2. **R22 confidence triple** — ``{"value": <finding>, "confidence":
       ..., "reason": ...}`` (from :func:`roam.output.confidence.wrap_findings`).
       Recurse into ``value`` so the inner finding's message reaches the
       risk string instead of the wrapper's ``str(triple)`` repr.
    3. **Plain finding dict** — first a ``claim`` / ``message`` / ``title``
       / ``description`` / ``verdict`` / ``observation`` / ``rule_id``
       text field; failing that, a synthesised ``"<name>: <count> (<severity>)"``
       fact from the conventional integrity-check shape (``cmd_db_check``).

    The previous ``str(item)`` fallback leaked ``{'value': {...}, ...}``
    repr dumps into a consumer-facing field (CLAUDE.md "structured signal
    lost" anti-pattern); the synthesised-fact branch replaces it. A bare
    ``"<key>=<value>"`` join is the last resort for an unrecognised dict
    so the field still never carries a raw repr.
    """
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return str(item)

    # R22 triple — unwrap and recurse into the inner finding so the
    # message/rule of the actual finding surfaces, not str(triple).
    if "value" in item and "confidence" in item:
        return _stringify_risk_item(item["value"])

    # Prefer an explicit human-readable text field.
    for key in ("claim", "message", "title", "description", "verdict", "observation", "rule_id"):
        v = item.get(key)
        if isinstance(v, str) and v:
            return v

    # Integrity-check shape (cmd_db_check): {name, count, severity, note?}.
    # Synthesise a short fact instead of dumping repr(dict).
    name = item.get("name") or item.get("rule") or item.get("kind")
    if isinstance(name, str) and name:
        parts = [str(name)]
        count = item.get("count")
        if isinstance(count, (int, float)) and not isinstance(count, bool):
            parts.append(f": {count}")
        severity = item.get("severity") or item.get("level")
        if isinstance(severity, str) and severity:
            parts.append(f" ({severity})")
        note = item.get("note")
        if isinstance(note, str) and note:
            parts.append(f" — {note}")
        return "".join(parts)

    # Last resort — a key=value join, never a raw repr(dict).
    return ", ".join(f"{k}={v}" for k, v in item.items())


# Severity / status values that mean "this finding is NOT a surviving
# risk" — an all-clear integrity check row should not pollute risks[].
_NON_RISK_SEVERITIES = frozenset({"ok", "info", "pass", "passed", "none", "clean"})


def _is_non_risk_item(item) -> bool:
    """Return True iff *item* is a finding that records an all-clear state.

    Used to keep ``agent_contract.risks[]`` to genuine surviving risks
    (CLAUDE.md CONSTRAINT 7). Unwraps the R22 confidence triple so the
    inner finding's severity is the one inspected. Bare strings and
    items with no severity field are treated as risks (no evidence they
    are clean — fail towards surfacing).
    """
    if not isinstance(item, dict):
        return False
    if "value" in item and "confidence" in item and isinstance(item["value"], dict):
        item = item["value"]
    sev = item.get("severity") or item.get("level") or item.get("status")
    return isinstance(sev, str) and sev.lower() in _NON_RISK_SEVERITIES


# Keys that carry envelope state metadata, NOT user-facing analytical
# facts. These stay in ``summary`` for full-envelope consumers but never
# pollute the bounded ``agent_contract.facts`` list — they are abstract
# state-machine annotations, not concrete-noun analytical claims (LAW 4).
_AGENT_CONTRACT_FACT_SKIP_KEYS = frozenset(
    {
        "verdict",
        "confidence",
        "state",
        "partial_success",
        # Envelope plumbing — never analytical facts. ``schema`` /
        # ``schema_version`` shouldn't even land in ``summary`` but the
        # extra defense costs nothing.
        "schema",
        "schema_version",
        "version",
        "project",
        # Progress / truncation telemetry — bookkeeping, not analytical.
        "truncated",
        "budget_tokens",
        "full_output_tokens",
        "omitted_low_importance_nodes",
        "kept_highest_importance",
        "detail_available",
        # Paging plumbing (W1142 --limit cap-disclosure). These mirror
        # the analytical ``total`` / ``count`` keys for cap-hit reporting;
        # surfacing them as facts produces redundant restatements
        # (``"total 18"`` / ``"count 18"`` / ``"total count 18"``) and the
        # nonsensical ``"0 limit findings"`` on the default-limit path.
        # ``total`` and ``count`` stay surfaceable because some commands
        # genuinely report only those; ``total_count`` and ``limit`` are
        # paging-specific.
        "total_count",
        "limit",
        # Notice slots that some commands attach to ``summary`` — these
        # are advisory strings, not concrete facts about the analytical
        # subject.
        "deprecation_warning",
        "next_commands",
        # Hints / human-readable preamble — sit in summary for plain
        # readers but would just leak as a verbose fact otherwise.
        "hint",
        "note",
    }
)


def _humanize_summary_fact(key: str, value: int | float) -> str:
    """Turn a ``(key, numeric)`` summary entry into a concrete-noun fact.

    LAW 4 (CLAUDE.md): facts must anchor on concrete nouns, not abstract
    ``key: value`` pairs. ``critical: 5`` → ``"5 critical findings"``;
    ``health_score: 90`` → ``"health_score 90"``.

    Heuristic decision tree (W17.3 refinement):

    1. **Trailing ``_total`` quantifier** (``runs_total``, ``files_total``):
       these read as "<noun> total", so we strip the suffix, count-first,
       and append " total" — ``runs_total: 5`` → ``"5 runs total"``.
    2. **Measurement suffix** (``score`` / ``count`` / ``size`` /
       ``depth`` / ``ratio`` / ``rate``): the key NAMES a measurement;
       keep ``label value`` order — ``health_score: 90`` → ``"health
       score 90"``.
    3. **Pre-pluralised concrete nouns** (``files`` / ``symbols`` /
       ``edges`` / ``snapshots`` / ``hotspots`` / ``secrets`` / ...):
       the label already reads as a noun, appending "findings" would
       double it (``"3722 total files findings"`` reads as garbage).
       Emit ``N <label>`` with no suffix — ``total_files: 3722`` →
       ``"3722 total files"``.
    4. **Otherwise** (count-noun like ``critical`` / ``warning`` /
       ``info``): count-first + generic ``"findings"`` anchor so the
       string reads as a sentence — ``critical: 5`` → ``"5 critical
       findings"``.

    Examples::

        ("critical", 5)            -> "5 critical findings"
        ("warning", 12)            -> "12 warning findings"
        ("info", 3)                -> "3 info findings"
        ("health_score", 90)       -> "health score 90"
        ("symbol_count", 217)      -> "symbol count 217"
        ("runs_total", 5)          -> "5 runs total"
        ("total_files", 3722)      -> "3722 total files"
        ("symbols_with_effects", 8459)
                                   -> "8459 symbols with effects"
    """
    label = key.replace("_", " ").strip()
    if not label:
        return f"{value}"

    last_token = label.rsplit(" ", 1)[-1].lower()

    # (1) ``_total`` quantifier suffix: rewrite as count-first "<noun(s)>
    # total". The label keeps any preceding tokens but the trailing
    # "total" reads naturally only after the count.
    if last_token == "total" and " " in label:
        head = label.rsplit(" ", 1)[0]
        return f"{value} {head} total"

    # (2) Measurement-naming suffixes — key NAMES a measurement, value
    # is its reading. Keep ``label value`` order. ``total`` is here as a
    # standalone key (e.g. ``{"total": 7}``); the compound ``foo_total``
    # case was already peeled above into the ``"N foo total"`` form.
    measurement_suffixes = (
        "score",
        "count",
        "total",
        "size",
        "depth",
        "ratio",
        "rate",
        "pct",
        "percent",
        "percentage",
        "ms",
        "bytes",
        "kb",
        "mb",
        # W1280 dogfood — `cohesion` is a 0..1 ratio metric emitted by
        # `roam relate` summary.cohesion. Treated as a measurement-named
        # key so the humanizer renders "cohesion 1.0" instead of the
        # double-noun "1.0 cohesion findings" (LAW 4 anchoring).
        "cohesion",
    )
    if last_token in measurement_suffixes or label.endswith("_id"):
        return f"{label} {value}"

    # (3) Pre-pluralised concrete nouns — appending "findings" would
    # double-noun the fact. The auto-derive emits a clean ``N <label>``
    # so commands whose summary keys are already concrete plurals
    # produce readable facts without needing per-command overrides.
    concrete_plural_terminals = (
        # Concrete plural nouns: appending "findings" would double-noun.
        "files",
        "symbols",
        "edges",
        "nodes",
        "cycles",
        "clusters",
        "layers",
        "smells",
        "snapshots",
        "hotspots",
        "secrets",
        "endpoints",
        "agents",
        "rules",
        "commits",
        "tests",
        "dependencies",
        "modules",
        "directories",
        "patterns",
        "alerts",
        "issues",
        "findings",
        "violations",
        "warnings",
        "errors",
        "matches",
        "effects",
        "events",
        "queries",
        "shifts",
        "moves",
        "imports",
        "callers",
        "callees",
        "branches",
        "paths",
        "routes",
        "annotations",
        "types",
        "languages",
        "owners",
        "users",
        "frameworks",
        "vulnerabilities",
        "challenges",
        "keys",
        "values",
        "chars",
        "characters",
        "lines",
        "tokens",
        "bytes",
        "items",
        "entries",
        "records",
        "fields",
        "options",
        "flags",
        "literals",
        "markers",
        "subcommands",
        "scenarios",
        "actions",
        "exits",
        "leaks",
        "gaps",
        "movers",
        "kinds",
        # W1280 dogfood — `roam relate` summary.conflict_risks emitted
        # "0 conflict risks findings" because the terminal "risks" was
        # not anchored. Adding it here yields "0 conflict risks" — clean
        # count-noun form on a concrete-plural terminal (LAW 4).
        "risks",
        # Retrieval-shape terminals (W1073 dogfood): `roam retrieve` summary
        # keys ``candidates`` / ``budget`` / ``seeds`` are inherently
        # count-noun concrete plurals; double-anchoring them with "findings"
        # produced "10 candidates findings" / "4000 budget findings" which
        # parse as garbage. Adding them here makes the humanizer emit
        # "10 candidates" / "4000 budget" / "10 seeds" — readable and
        # LAW 4-anchored on the terminal noun directly.
        "candidates",
        "budget",
        "seeds",
        # Past-participle / state qualifiers used as terminal tokens
        # (``files_passed`` / ``symbols_failed`` / ``runs_skipped``).
        # The preceding noun is the analytical subject; appending
        # "findings" would still read awkwardly.
        "passed",
        "failed",
        "scanned",
        "checked",
        "owned",
        "analysed",
        "analyzed",
        "removed",
        "added",
        "skipped",
        "affected",
        "available",
        "trending",
        "scored",
        "confirmed",
        "upgrades",
        "downgrades",
        # W1073 dogfood — `budget_used`, `bytes_used`, `slots_used` and
        # peers all use ``used`` as a state qualifier. Adding it here
        # makes "2900 budget used" the natural anchored form instead of
        # the awkward "2900 budget used findings".
        "used",
        # Time units used as terminal nouns (``window_days`` etc.).
        "days",
        "weeks",
        "months",
        "years",
        "hours",
        "minutes",
        "seconds",
        "milliseconds",
    )
    if last_token in concrete_plural_terminals:
        return f"{value} {label}"

    # (4) Default: count-noun form. Numbers first, then label, then a
    # generic noun anchor so the string reads as a fact rather than
    # bare numerics.
    return f"{value} {label} findings"


def _truncate_fact(fact: str, limit: int = _AGENT_CONTRACT_STR_TRUNCATE) -> str:
    """Truncate a fact at a word boundary, appending ``"..."`` when cut.

    W-dogfood-K: the prior ``fact[:120]`` slice chopped strings
    mid-word (``"...for ful"`` from ``"...for full radius)"``),
    producing facts that are unintelligible to an agent and that
    leave half-words trailing inside the LAW-4 lint's "long sentence
    self-anchors" branch. Cutting at the last whitespace before the
    limit and appending ``"..."`` keeps the fact readable AND
    preserves the prior length budget (the ellipsis fits within the
    same overall character cap because we cut at <= limit - 3).
    """
    if len(fact) <= limit:
        return fact
    head = fact[: limit - 3]
    last_space = head.rfind(" ")
    if last_space > limit // 2:
        head = head[:last_space]
    return head.rstrip() + "..."


def _is_fact_eligible_key(key: str, value: object) -> bool:
    """Return True iff this summary key/value pair should emit a concrete-noun fact.

    LAW 4 (CLAUDE.md): only numeric values anchored on non-metadata keys
    become facts. Leading-underscore, skip-list, bool, and sidecar
    definition/distribution keys are all excluded.
    """
    if key in _AGENT_CONTRACT_FACT_SKIP_KEYS:
        return False
    # Leading-underscore keys are private metadata: ``_meta``, ``_trace``,
    # any future internal annotation.
    if key.startswith("_"):
        return False
    if isinstance(value, bool):
        return False
    if key.endswith("_definition") or key.endswith("_distribution"):
        return False
    return isinstance(value, (int, float))


def _extract_facts_from_summary(summary: dict) -> list[str]:
    """Extract verdict + numeric-count facts from *summary* for ``agent_contract.facts``.

    LAW 4 (CLAUDE.md): humanize ``critical: 5`` → ``"5 critical findings"``.
    State / metadata keys stay in ``summary`` but never pollute ``facts``
    (abstract state-machine annotations, not concrete-noun analytical claims).
    Dict/list values are skipped — they aren't auto-summarizable.
    """
    facts: list[str] = []
    verdict = summary.get("verdict")
    if isinstance(verdict, str) and verdict:
        facts.append(_truncate_fact(verdict))
    for key, value in summary.items():
        if _is_fact_eligible_key(key, value):
            facts.append(_truncate_fact(_humanize_summary_fact(key, value)))
            if len(facts) >= _AGENT_CONTRACT_MAX_FACTS:
                break
    return facts


def _collect_risk_strings(items: list, max_count: int) -> list[str]:
    """Collect up to *max_count* risk strings from *items*, skipping all-clear entries.

    CONSTRAINT 7 (CLAUDE.md): risks[] names SURVIVING risks only — findings
    whose severity says all-clear (ok/info/pass/none) are filtered out so an
    all-clear integrity sweep yields an empty risks[], not three "(ok)" lines.
    """
    risks: list[str] = []
    for item in items:
        if _is_non_risk_item(item):
            continue
        risks.append(_truncate_fact(_stringify_risk_item(item)))
        if len(risks) >= max_count:
            break
    return risks


def _extract_risks_from_envelope(out: dict) -> list[str]:
    """Extract surviving risks from *out* for ``agent_contract.risks``.

    First non-empty list among the conventional risk keys wins.
    """
    for key in _RISK_KEYS:
        items = out.get(key)
        if isinstance(items, list) and items:
            return _collect_risk_strings(items, _AGENT_CONTRACT_MAX_RISKS)
    return []


def _extract_next_commands(out: dict, summary: dict) -> list[str]:
    """Extract next commands from *out*/*summary* for ``agent_contract.next_commands``.

    Tries the structured ``next_steps`` payload first, then falls back to
    ``summary.next_commands`` as a less-formal alternative.
    """
    next_commands: list[str] = []
    next_source = out.get("next_steps")
    if not isinstance(next_source, list):
        next_source = summary.get("next_commands")
    if not isinstance(next_source, list):
        return next_commands
    for step in next_source[:_AGENT_CONTRACT_MAX_NEXT]:
        if isinstance(step, dict):
            cmd = step.get("command") or step.get("cmd") or step.get("action") or ""
        else:
            cmd = str(step)
        if cmd:
            next_commands.append(_truncate_fact(cmd))
    return next_commands


def _derive_agent_contract(out: dict, summary: dict) -> dict:
    """Build the bounded ``agent_contract`` derived block.

    Generic across all envelopes — pulls structural cues (verdict,
    numeric counts in summary, error lists, next_steps) without
    requiring per-command opt-in. Agents on tight context budgets can
    read just this dict; full-payload consumers ignore it.
    """
    raw_conf = summary.get("confidence")
    confidence: float | None = (
        float(raw_conf) if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool) else None
    )
    return {
        "facts": _extract_facts_from_summary(summary),
        "risks": _extract_risks_from_envelope(out),
        "next_commands": _extract_next_commands(out, summary),
        "confidence": confidence,
    }


def _has_active_bundle(repo_root) -> bool:
    """Return True iff a ``.roam/pr-bundles/*.json`` file exists.

    Signal: "an agent is actively building a PR bundle in this repo."
    Best-effort — any I/O failure (permission, missing root) returns False.
    """
    try:
        from pathlib import Path as _Path

        if not isinstance(repo_root, _Path):
            return False
        bundle_dir = repo_root / ".roam" / "pr-bundles"
        if not bundle_dir.is_dir():
            return False
        # ``any(...)`` short-circuits as soon as one .json is found.
        return any(p.suffix == ".json" and p.is_file() for p in bundle_dir.iterdir())
    except Exception:
        return False


def _write_response_to_responses_dir(envelope: dict) -> None:
    """Write *envelope* to ``.roam/responses/<sha>.json`` when an agent is active.

    Closes the gap surfaced by Wave 9.1 + W14.1: ``roam pr-bundle --auto-collect``
    walks ``.roam/responses/*.json`` but ONLY the MCP handle-off used to write
    there. CLI invocations of ``roam --json preflight X`` produced no envelopes
    for auto-collect to fold.

    The helper fires when EITHER trigger says an agent is actively building
    state worth folding into a PR bundle:

      1. ``ROAM_RUN_ID`` env var is set (explicit signal: a run is open).
      2. A PR bundle exists at ``.roam/pr-bundles/*.json`` (W15.2 followup:
         the bundle's existence is itself a signal — the agent is actively
         preparing a PR even if no run was opened, so the natural workflow
         ``pr-bundle init → preflight → pr-bundle emit --auto-collect`` no
         longer needs ROAM_RUN_ID threaded through it).

    Either signal alone is sufficient. Both still write only once per command
    invocation (the content-hash dedup prevents duplicates).

    Best-effort: silently no-ops on any failure — never break the parent
    command just because we couldn't write a side-car file.

    Gates (all of these must pass before either trigger can fire):
      - envelope must carry the canonical ``schema`` marker
      - envelope's command must NOT be in the exclusion list (avoids feedback
        loops with runs/memory/constitution/pr-bundle commands)
      - current working dir must be inside a roam project
    """
    if not isinstance(envelope, dict):
        return
    if envelope.get("schema") != ENVELOPE_SCHEMA_NAME:
        return
    command = envelope.get("command", "")
    if not isinstance(command, str) or not command:
        return
    if command in _EXCLUDED_COMMANDS_FROM_RESPONSES_WRITE:
        return
    try:
        import hashlib
        from pathlib import Path

        from roam.db.connection import find_project_root

        repo_root = find_project_root()
        # Refuse to write outside a roam project root.
        if not isinstance(repo_root, Path) or not repo_root.exists():
            return
        # Either trigger fires. Check env first (cheap) then disk (cheap-ish).
        env_signal = bool(os.environ.get("ROAM_RUN_ID"))
        bundle_signal = False
        if not env_signal:
            # Only probe the filesystem when the env didn't already authorise
            # the write. Keeps the no-active-state path zero-overhead.
            bundle_signal = _has_active_bundle(repo_root)
        if not (env_signal or bundle_signal):
            return
        responses_dir = repo_root / ".roam" / "responses"
        responses_dir.mkdir(parents=True, exist_ok=True)
        # Content-hash the LOGICAL envelope so re-running the same command with
        # the same inputs dedupes naturally (the bundle's auto-collect should not
        # see N copies of the same `roam health` run). Exclude the whole `_meta`
        # block from the dedup KEY: it carries non-deterministic fields
        # (timestamp, latency_ms, index_age_s, response_tokens, and the staleness
        # index_status/dirty_files) that vary call-to-call — e.g. a cold lazy-import
        # or git probe on the FIRST call inflates latency_ms relative to the second,
        # which would defeat dedup. The written file keeps full `_meta`; only the
        # dedup key drops it. This makes dedup robust to timing instead of relying
        # on `_meta` happening to serialise byte-identically across calls.
        _hash_src = {k: v for k, v in envelope.items() if k != "_meta"}
        h = hashlib.sha256(_json.dumps(_hash_src, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
        # Sanitise command for filename use (slashes / spaces would be odd
        # but we have e.g. "pr-bundle-emit" already — slugify defensively).
        safe_cmd = "".join(c if (c.isalnum() or c in "-_") else "_" for c in command)
        out_path = responses_dir / f"{safe_cmd}_{h}.json"
        out_path.write_text(_json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    except Exception:
        # Best-effort. Never break the parent command.
        return


# W975: loose-but-honest per W966 — ``**payload`` and ``summary`` are
# arbitrary user-supplied dicts merged via ``.update()``; do NOT TypedDict
# this return without an at-boundary validator. See W933 _resolved_thresholds
# for the canonical case study.
def json_envelope(command: str, summary: dict | None = None, budget: int = 0, **payload) -> dict:
    """Wrap command output in a self-describing envelope.

    Every ``roam --json <cmd>`` call should use this to produce consistent
    top-level keys that downstream tools (CI, dashboards, AI agents) can
    rely on.

    Non-deterministic metadata (``timestamp``, ``index_age_s``) is placed
    in a ``_meta`` sub-dict so the main content keys remain stable across
    invocations — enabling LLM prompt-caching (exact prefix matching).

    When *budget* > 0, the envelope is passed through
    :func:`budget_truncate_json` before being returned, intelligently
    trimming list payloads to fit within the token cap while preserving
    summary and envelope metadata.

    Returns a dict with at minimum::

        {
            "command":     "health",
            "version":     "<current>",
            "project":     "roam-code",
            "summary":     { ... },
            "_meta": {
                "timestamp":   "2026-02-12T14:30:00Z",
                "index_age_s": 42,
            },
            ...payload
        }
    """
    # If a deprecated alias was used to invoke roam, surface the notice in
    # `summary.deprecation_warning` so JSON consumers (who never see stderr)
    # can detect it. The slot is set by `roam.cli.resolve_command` and
    # cleared at the start of each new invocation. Defensive try/except: this
    # injection must never break envelope generation for non-CLI callers.
    summary = dict(summary) if summary else {}
    # W817: Pattern 2 always-emit discipline. Detector commands (dead /
    # complexity / clones / orphan-imports / bus-factor / auth-gaps and
    # likely others — see W805 sweep) historically omitted
    # `summary.partial_success` on their no-findings branches, leaving
    # agents unable to distinguish "scanned, clean" from "didn't run".
    # Default to ``False`` (clean) when missing — callers that genuinely
    # had a partial run still set it to ``True`` explicitly, which wins.
    if summary and "partial_success" not in summary:
        summary["partial_success"] = False
    try:
        from roam.cli import _get_active_deprecation_notice

        _depr_notice = _get_active_deprecation_notice()
        if _depr_notice and "deprecation_warning" not in summary:
            summary["deprecation_warning"] = _depr_notice
    except (ImportError, AttributeError):
        # W677: narrowed from `except Exception` — ImportError covers
        # non-CLI callers where `roam.cli` isn't importable; AttributeError
        # covers the case where the deprecation-notice slot helper hasn't
        # been wired up yet. Programmer-class errors (NameError /
        # TypeError) propagate per W531 fail-loud.
        pass

    if _compact_mode_enabled():
        compact = compact_json_envelope(command, summary=summary, **payload)
        if budget > 0:
            compact = budget_truncate_json(compact, budget)
        return compact

    # Version — read once and cache
    version = _get_version()

    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # Pull explicit agent_contract kwarg BEFORE updating payload, so the
    # auto-derive block can merge it instead of clobbering it.
    explicit_contract = payload.pop("agent_contract", None)

    out: dict = {
        "schema": ENVELOPE_SCHEMA_NAME,
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "command": command,
        "version": version,
        "project": _project_name(),
        "summary": summary,
    }
    out.update(payload)

    # Derived ``agent_contract`` block — bounded ~200 tokens. Agents on
    # tight context budgets can read just this and skip the full payload;
    # full-payload consumers ignore it. Opt-out via env
    # ``ROAM_AGENT_CONTRACT_BLOCK=0``.
    if os.environ.get("ROAM_AGENT_CONTRACT_BLOCK", "1").lower() not in ("0", "false", "no"):
        auto_contract = _derive_agent_contract(out, summary or {})
        if isinstance(explicit_contract, dict) and explicit_contract:
            # Merge: explicit fields win, auto-derived fills gaps. Always
            # keep auto-derived ``next_commands`` when caller did not
            # supply its own — agents rely on the auto-derived list when
            # ``summary.next_commands`` is set.
            merged = dict(auto_contract)
            for k, v in explicit_contract.items():
                if v is not None:
                    merged[k] = v
            if "next_commands" not in explicit_contract and auto_contract.get("next_commands"):
                merged["next_commands"] = auto_contract["next_commands"]
            out["agent_contract"] = merged
        else:
            out["agent_contract"] = auto_contract

    # Non-deterministic metadata in _meta — kept separate so content
    # keys produce identical JSON across invocations (LLM cache-friendly).
    #
    # W210 evidence axis: stamp ``roam_version`` here so every envelope
    # carries the producer-version provenance ``ChangeEvidence.roam_version``
    # expects. The field lives in ``_meta`` rather than at top level for
    # two reasons:
    #   (a) ``out["version"]`` already exists at top level for backward-
    #       compat consumers; ``_meta.roam_version`` is the W210-canonical
    #       location and matches the ChangeEvidence field name verbatim.
    #   (b) ``_meta`` is already non-deterministic (timestamp), so adding
    #       a stable field here cannot regress prompt-cache hit rates that
    #       were already locked in by the timestamp's variability.
    # The evidence packet content-hash (test_evidence_schema_migration)
    # hashes ``ChangeEvidence`` dataclass output, NOT envelope JSON — so
    # this addition does not affect those golden hashes.
    out["_meta"] = {
        "timestamp": ts,
        "index_age_s": _index_age_seconds(),
        "roam_version": version,
    }

    # Wave-1 staleness disclosure (Root-1a): for stale-sensitive commands, surface
    # index_status WHEN the index is actually stale, so an agent reading the envelope
    # cannot act on data computed against code that no longer matches. Placed in _meta
    # (alongside index_age_s), NOT top-level: top-level placement broke content-hash
    # dedup + envelope-consistency/diff contracts (a dirty real-repo tree fires it
    # globally during tests). _meta is the non-deterministic bucket dedup/consistency
    # already exclude. We do NOT flip summary.partial_success here — that field reflects
    # the CHECK outcome, not index freshness (conflating them broke clean-path tests).
    # Gated on `fresh is False`; lazy imports avoid an output<-commands cycle; failures
    # never break envelope generation.
    if "index_status" not in out["_meta"] and _command_is_stale_sensitive(command):
        _stale = _envelope_index_status()
        if isinstance(_stale, dict) and _stale.get("fresh") is False:
            out["_meta"]["index_status"] = _stale

    # Response metadata for MCP agents (#119)
    full_json = _json.dumps(out, default=str, sort_keys=True)
    out["_meta"]["response_tokens"] = estimate_tokens(full_json)
    out["_meta"]["latency_ms"] = None  # filled by caller if needed
    if command in _NON_CACHEABLE_COMMANDS:
        out["_meta"]["cacheable"] = False
        out["_meta"]["cache_ttl_s"] = 0
    elif command in _VOLATILE_COMMANDS:
        out["_meta"]["cacheable"] = True
        out["_meta"]["cache_ttl_s"] = 60
    else:
        out["_meta"]["cacheable"] = True
        out["_meta"]["cache_ttl_s"] = 300

    # Best-effort side-car write to `.roam/responses/` so `pr-bundle
    # --auto-collect` can fold this envelope into the bundle later. Fires
    # when EITHER ROAM_RUN_ID is set OR a `.roam/pr-bundles/*.json` exists
    # (W15.2 followup: bundle existence is now a sufficient trigger so the
    # natural ``pr-bundle init → preflight → pr-bundle emit --auto-collect``
    # workflow no longer requires threading ROAM_RUN_ID through). Silent no-op
    # otherwise. Writes the full untruncated envelope so downstream auto-collect
    # sees complete fields. Wrapped in try/except inside the helper — must
    # NEVER break envelope generation.
    _write_response_to_responses_dir(out)

    if budget > 0:
        out = budget_truncate_json(out, budget)
    else:
        # Pattern-6 default bounding (E1/N3): budget 0 ("no cap") historically let
        # high-fanout commands (uses/clones/path-coverage) emit 56K-224KB JSON --
        # over roam's own 20K-token mandate and worse than `grep -rl`. Cap ONLY
        # genuinely-oversized envelopes so normal (<cap) output stays byte-identical
        # and prompt-cache-stable. Override via ROAM_DEFAULT_JSON_BUDGET (0 disables).
        _cap = _default_json_budget()
        if _cap and out.get("_meta", {}).get("response_tokens", 0) > _cap:
            out = budget_truncate_json(out, _cap)

    return out


def _command_is_stale_sensitive(command: str) -> bool:
    """True if the command's capability declares ``stale_sensitive`` (default True).
    Lazy import: ``capability`` imports ``formatter``, so a top-level import would cycle."""
    try:
        from roam.capability import _CAPABILITIES

        cap = _CAPABILITIES.get(command)
        return bool(cap.stale_sensitive) if cap is not None else True
    except (ImportError, AttributeError):
        return False


def _envelope_index_status():
    """Lazily fetch ``index_status()`` for envelope staleness disclosure (dict or None).
    NOT cached: the MCP server is long-running and the index can be rebuilt mid-process,
    so a per-process cache would serve a stale freshness verdict."""
    try:
        from roam.commands.resolve import index_status

        return index_status()
    except (ImportError, OSError, ValueError):
        return None


def _default_json_budget() -> int:
    """Default token cap for oversized JSON envelopes when no explicit ``--budget``
    is given. Returns 0 to disable. High enough that normal command output is
    untouched (byte-identical); it only bounds the Pattern-6 blowouts
    (``uses``/``clones``/``path-coverage``). Tunable via ``ROAM_DEFAULT_JSON_BUDGET``."""
    # Validate without a silent except (loud-fallback discipline,
    # test_loud_fallback_no_new_silent_except): only an all-digit value
    # (optionally signed) is a valid override; anything else falls through to
    # the default rather than being swallowed.
    raw = (os.environ.get("ROAM_DEFAULT_JSON_BUDGET") or "").strip()
    if raw.lstrip("-").isdigit():
        return max(0, int(raw))
    return 20000


def _get_version() -> str:
    """Return roam-code version string."""
    from roam import __version__

    return __version__


def _index_age_seconds() -> int | None:
    """Seconds since .roam/index.db was last modified, or None if missing."""
    try:
        from roam.db.connection import get_db_path

        db_path = get_db_path()
        if db_path.exists():
            return int(time.time() - db_path.stat().st_mtime)
    except (OSError, FileNotFoundError):
        # Expected-signal guard: a stat() race after exists() (TOCTOU) or
        # a missing index just means "age unknown" — the None return IS
        # the disclosed signal to the caller. No lineage needed.
        pass
    return None


def _project_name() -> str:
    """Basename of the project root directory."""
    try:
        from roam.db.connection import find_project_root

        return find_project_root().name
    except OSError:
        return ""


def table_to_dicts(headers: list[str], rows: list[list[str]]) -> list[dict]:
    """Convert table headers + rows into a list of dicts (for JSON output)."""
    return [dict(zip(headers, row)) for row in rows]


# -- Compact output mode ----------------------------------------------


def compact_json_envelope(command: str, **payload) -> dict:
    """Minimal JSON envelope — strips version/timestamp/project overhead.

    For agents using --compact: emits only command name, summary, and payload.
    Saves ~150-200 tokens per call.

    W33f (2026-05-30): also runs the agent_contract derivation that
    `json_envelope` applies, BUT only when there is real content to derive
    from (summary, explicit contract, or payload). A bare
    `compact_json_envelope("foo")` call still returns just `{"command": "foo"}`
    so call sites that intentionally produce a minimal envelope aren't
    silently bloated.
    """
    summary = payload.pop("summary", None)
    explicit_contract = payload.pop("agent_contract", None)
    out: dict = {"command": command}
    if summary is not None:
        out["summary"] = summary
    out.update(payload)

    # Explicit contract always wins.
    if explicit_contract is not None:
        out["agent_contract"] = explicit_contract
        return out

    # Auto-derive only when there's something to derive from. Skip when the
    # caller passed nothing but a command name (preserves backward-compat
    # with the original minimal-envelope contract).
    if not (summary or payload):
        return out

    # Narrow exception list per W531/W662 — only swallow data-shape errors
    # the derivation function might raise when given an unusual summary.
    # Programmer-class errors (NameError, TypeError on attr access)
    # propagate so they surface in CI rather than producing silent bad output.
    try:
        out["agent_contract"] = _derive_agent_contract(out, summary or {})
    except (KeyError, AttributeError) as exc:
        # Log the swallow so this isn't a silent regression vector — but
        # don't propagate; envelope generation must never break.
        from roam.observability import log_swallowed

        log_swallowed("formatter:compact_json_envelope.derive_contract", exc)
    return out


def ws_loc(repo: str, path: str, line: int | None = None) -> str:
    """Repo-prefixed location string for workspace output."""
    if line is not None:
        return f"[{repo}] {path}:{line}"
    return f"[{repo}] {path}"


def ws_json_envelope(command: str, workspace: str, summary: dict | None = None, **payload) -> dict:
    """Workspace-aware JSON envelope.

    Extends :func:`json_envelope` with workspace metadata.
    """
    out = json_envelope(command, summary=summary, **payload)
    out["workspace"] = workspace
    return out


# W1000: list fields whose contents the caller MUST see even in
# default-detail-off mode. Sealing these closes the Pattern 2 silent-
# fallback hole that W994+W995 opened (warnings_out is the canonical
# example: malformed suppression YAML or expired/missing fields append
# to ``warnings_out``; if ``strip_list_payloads`` drops it, the user
# sees a clean envelope and the disclosure is silently lost).
#
# Closed allow-set, extended only with deliberate review. Each entry
# carries the same Pattern 2 obligation: "informational list the agent
# NEEDS to see to know the state is degraded".
#
# W1006 extension: ``errors`` and ``redactions`` join the set. Same
# Pattern-2 obligation as ``warnings_out``:
#   * ``errors`` — emitted at top-level by ``batch-search``, ``cga-verify``,
#     ``plugins``, ``rules-validate``, ``ws`` (and is the universal
#     disclosure idiom for any future command). Silently dropping a
#     non-empty ``errors`` list is the textbook Pattern-2 silent-fallback.
#   * ``redactions`` — emitted at top-level by ``pr-bundle`` and
#     ``evidence-doctor``. The producer comments explicitly call this
#     "Pattern 2 — explicit absence"; the agent NEEDS to know which
#     evidence axes were masked, otherwise it cannot tell a clean
#     packet from a redaction-heavy one.
# W1007 extension: ``agent_contract`` joins the set as a defensive
# disclosure marker. The canonical shape is a DICT (see
# ``_derive_agent_contract`` — emits ``{facts, risks, next_commands,
# confidence}``). Strip only fires on list-valued fields, so the
# canonical dict passes through the ``else`` branch untouched. But if
# a producer wrongly emits ``agent_contract: []`` (list instead of
# dict), the strip would silently drop it — making the schema mistake
# invisible to the agent and forever-debugged. Preserving the empty
# list surfaces the mistake at envelope top-level so consumers can
# detect and react. Per-emitter sweep for the actual producer bug
# stays open as a separate backlog item.
# Deliberately NOT added (W1006 audit, re-verified W1028 — all 4
# candidates remain DEFER; no state change since W1006 captured).
# Each entry below names the candidate, the W1006 deferral reason, and
# the W1028 re-audit finding (the bar for joining the set is "Pattern-2
# disclosure list emitted at envelope top-level by a command that calls
# ``strip_list_payloads``" — none of the 4 cleared all three gates):
#   * ``dropped_keys`` — no producer in source today. W1028 grep:
#     still zero matches across ``src/roam/``.
#   * ``dropped_reasons`` — only emitted nested under ``summary`` by
#     ``cmd_evidence_oscal``; ``summary`` is already preserved whole.
#     W1028 grep: single emit at ``cmd_evidence_oscal.py:374``, nested
#     under ``ar_counts`` → ``summary``. Preservation already covered.
#   * ``stale_reasons`` — lives inside ``ChangeEvidence`` packets, not at
#     envelope top-level; revisit if a packet flattener ever emerges.
#     W1028 grep: one top-level emit at ``cmd_evidence_doctor.py:901``,
#     but ``cmd_evidence_doctor`` does NOT call ``strip_list_payloads``
#     (no consumer at risk).
#   * ``enum_violations`` / ``trust_warnings`` / ``bundle_warnings`` —
#     belong to ``evidence-doctor`` / ``pr-bundle``, neither of which
#     calls ``strip_list_payloads``. W1028 grep: ``bundle_warnings`` is
#     aliased into ``warnings_out`` at ``cmd_pr_bundle.py:1776,1786``
#     (already preserved); ``enum_violations`` top-level list at
#     ``cmd_evidence_doctor.py:883`` has no strip-helper consumer.
# The W1028 drift-guard (``test_w1028_deferred_candidates_not_silently_added``
# in ``tests/test_formatter_preserved_list_fields.py``) pins both the
# preserved-set count AND the deferred-candidate membership so a future
# editor cannot silently widen the set without re-running this audit.
_ALWAYS_PRESERVED_LIST_FIELDS = frozenset(
    {
        "warnings_out",
        "errors",
        "redactions",
        "agent_contract",  # W1007 — see comment above
    }
)

# When a preserved list exceeds this length we keep the first N entries
# and emit a sibling ``<field>_truncated: <int>`` naming how many were
# dropped. Bounds the envelope size while keeping the disclosure honest.
_ALWAYS_PRESERVED_LIST_MAX = 10


#: Envelope top-level keys that ``strip_list_payloads`` always copies through
#: untouched regardless of their value shape. Kept private + module-scoped so
#: the partition helper can share it with the public entry point without
#: rebuilding the set on every call.
_STRIP_PASSTHROUGH_KEYS = frozenset(
    {
        "command",
        "schema",
        "schema_version",
        "version",
        "project",
        "_meta",
    }
)


def _cap_preserved_list(values: list) -> tuple[list, int]:
    """Cap a preserved-list field at :data:`_ALWAYS_PRESERVED_LIST_MAX`.

    Returns a ``(kept, dropped)`` tuple where ``kept`` is a fresh list
    (defensive copy, never the input) and ``dropped`` is the count of
    elements omitted (zero when the input is at or below the cap).
    """
    if len(values) > _ALWAYS_PRESERVED_LIST_MAX:
        kept = list(values[:_ALWAYS_PRESERVED_LIST_MAX])
        return kept, len(values) - _ALWAYS_PRESERVED_LIST_MAX
    return list(values), 0


def _partition_envelope_fields(
    data: dict,
    keep_summary: bool,
) -> tuple[dict, dict[str, int], dict[str, int]]:
    """Classify each top-level envelope key into the strip-output shape.

    Walks ``data`` once and routes each key/value to the right bucket:

    * passthrough keys (``_STRIP_PASSTHROUGH_KEYS``) and ``summary`` go into
      the result dict as-is (summary gets a shallow copy so callers can
      annotate it freely);
    * preserved-list keys (``_ALWAYS_PRESERVED_LIST_FIELDS``) are capped
      via :func:`_cap_preserved_list` and any drop is recorded in the
      ``preserved_list_truncations`` map plus a top-level
      ``<field>_truncated`` sibling on the result;
    * other list-valued keys are dropped from the result and their length
      is recorded in ``list_counts`` for W1008 disclosure;
    * scalar / dict values fall through to the result unchanged.

    Returns a ``(result, list_counts, preserved_list_truncations)`` tuple
    where ``result`` is the partially-built stripped envelope (summary
    disclosure annotations + top-level ``list_counts`` are written later
    by :func:`_annotate_summary_disclosure`).
    """
    result: dict = {}
    list_counts: dict[str, int] = {}
    preserved_list_truncations: dict[str, int] = {}

    for k, v in data.items():
        if k in _STRIP_PASSTHROUGH_KEYS:
            result[k] = v
        elif k == "summary":
            if keep_summary:
                result[k] = dict(v) if isinstance(v, dict) else v
        elif isinstance(v, list):
            if k in _ALWAYS_PRESERVED_LIST_FIELDS:
                kept, dropped = _cap_preserved_list(v)
                result[k] = kept
                if dropped:
                    result[f"{k}_truncated"] = dropped
                    preserved_list_truncations[k] = dropped
            else:
                list_counts[k] = len(v)
        else:
            result[k] = v

    return result, list_counts, preserved_list_truncations


def _detect_schema_violations(data: dict) -> list[str]:
    """Scan the ORIGINAL envelope for canonical-shape violations (W1100).

    Returns the ordered list of violation kinds discovered. Today the only
    recognised kind is ``agent_contract_shape`` (canonical shape is a
    dict per ``_derive_agent_contract``; a list at envelope top-level is
    the W1007 producer-bug signal). New violation kinds plug in here so
    the public ``strip_list_payloads`` stays single-purpose.
    """
    violations: list[str] = []
    if isinstance(data.get("agent_contract"), list):
        violations.append("agent_contract_shape")
    return violations


def _annotate_summary_disclosure(
    result: dict,
    list_counts: dict[str, int],
    preserved_list_truncations: dict[str, int],
    schema_violation_kinds: list[str],
) -> None:
    """Stamp the progressive-disclosure flags onto the result envelope.

    Writes the W1008 / W1100 / W1101 / W1102 disclosure surface in place:

    * ``summary.detail_available`` always true;
    * ``summary.truncated`` true iff any non-empty list was dropped OR
      any preserved list was capped;
    * ``summary.preserved_list_truncations`` always present (W1102
      symmetry) — empty dict when nothing was capped;
    * ``summary.partial_success`` overridden to true when schema
      violations were detected, and ``summary.schema_violations``
      extended (not replaced) with the new kinds (W1100);
    * top-level ``list_counts`` always present (W1101 symmetry) — empty
      dict when nothing was dropped.

    The annotation is minimal so summary stays ``<=`` detail in size.
    """
    has_non_empty_lists = any(c > 0 for c in list_counts.values())
    has_preserved_truncations = bool(preserved_list_truncations)

    if "summary" not in result:
        result["summary"] = {}
    summary = result.get("summary")
    if isinstance(summary, dict):
        summary["detail_available"] = True
        if has_non_empty_lists or has_preserved_truncations:
            summary["truncated"] = True
        # W1102: emit preserved_list_truncations always for symmetry with
        # W1101 list_counts + W1006 redactions[]. Empty dict tells the
        # consumer "strip_list_payloads ran and no preserved field was
        # clipped" vs an absent key which would be indistinguishable from
        # "envelope wasn't processed". Lives INSIDE summary (not top-level)
        # to mirror the per-field <field>_truncated siblings, which are
        # already top-level — the summary entry is the structured roll-up.
        # Shape: {field_name: dropped_count} — same shape as the internal
        # tracker, no semantic change.
        summary["preserved_list_truncations"] = dict(preserved_list_truncations)
        # W1100: schema violation overrides successful verdict — agent_contract
        # must be dict, list is malformed. Override existing ``False`` because
        # a malformed envelope is non-recoverable signal; ``setdefault`` would
        # let a stale ``partial_success: false`` bury the violation. Extend
        # (don't replace) any caller-supplied ``schema_violations`` list so
        # orthogonal violations remain visible.
        if schema_violation_kinds:
            summary["partial_success"] = True
            existing_violations = summary.get("schema_violations")
            if isinstance(existing_violations, list):
                for kind in schema_violation_kinds:
                    if kind not in existing_violations:
                        existing_violations.append(kind)
            else:
                summary["schema_violations"] = list(schema_violation_kinds)

    # W1008: surface ``list_counts`` at envelope top-level so agents can
    # tell which fields were dropped + how big they were (drives the
    # "re-request with --detail" decision). Mirrors the W1006/W1007
    # envelope-level disclosure pattern.
    # W1101: emit list_counts: {} always for symmetry with W1006
    # redactions[] (consumer-side absence-vs-empty disambiguation) — an
    # empty dict tells the consumer "strip_list_payloads ran and dropped
    # nothing", an absent key would be indistinguishable from "envelope
    # wasn't processed". LAW 6: a tiny ``{field: N}`` dict (or ``{}``),
    # not a list expansion -- compression preserved.
    result["list_counts"] = dict(list_counts)


def strip_list_payloads(data: dict, keep_summary: bool = True) -> dict:
    """Strip list-valued payload fields from a JSON envelope in default mode.

    Used by ``--detail``-aware commands whose headline output is NOT itself a
    list.  Full payloads return when ``--detail`` is set; in default mode the
    dropped fields are summarized via the ``detail_available`` flag on the
    summary dict.  When non-empty lists were stripped, also sets
    ``truncated: true`` in the summary.

    NOTE: this helper is only appropriate for commands whose primary signal is
    in scalar/dict summary fields.  Commands whose headline payload IS a list
    (e.g. ``guard``, ``plan-refactor``, ``suggest-refactoring``) must use
    custom caps instead -- stripping their lists would erase the headline.

    W1000 / W1006 / W1007: list fields named in
    :data:`_ALWAYS_PRESERVED_LIST_FIELDS` (``warnings_out``, ``errors``,
    ``redactions``, ``agent_contract``) ARE kept — these are Pattern 2
    silent-fallback disclosures the caller must see. ``agent_contract``
    is the W1007 defensive entry: the canonical shape is a dict, but if
    a producer ever emits the empty-list mistake the disclosure stays
    visible instead of silently disappearing. Lists longer than
    :data:`_ALWAYS_PRESERVED_LIST_MAX` are capped and a sibling
    ``<field>_truncated`` int is emitted naming how many entries were
    dropped.

    Parameters
    ----------
    data:
        A dict produced by :func:`json_envelope`.
    keep_summary:
        When True (default) the ``summary`` sub-dict is always preserved.

    Returns a new dict without list-valued payload keys.  The summary dict
    always receives ``detail_available: true``.  When non-empty lists were
    stripped, the summary also receives ``truncated: true``.
    """
    result, list_counts, preserved_list_truncations = _partition_envelope_fields(data, keep_summary)
    schema_violation_kinds = _detect_schema_violations(data)
    _annotate_summary_disclosure(
        result,
        list_counts,
        preserved_list_truncations,
        schema_violation_kinds,
    )
    return result


def format_table_compact(headers: list[str], rows: list[list[str]], budget: int = 0) -> str:
    """Tab-separated table output — 40-50% more token-efficient than padded tables."""
    if not rows:
        return "(none)"
    lines = ["\t".join(headers)]
    display_rows = rows[:budget] if budget and len(rows) > budget else rows
    for row in display_rows:
        lines.append("\t".join(str(cell) for cell in row))
    if budget and len(rows) > budget:
        lines.append(f"(+{len(rows) - budget} more)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# W1241 / Pattern-2 variant D: resolution-state disclosure helper.
#
# W324's cmd_annotate template established the canonical fix for the
# "silent success on degraded resolution" anti-pattern: any command that
# calls ``resolve.find_symbol()`` (or any other resolver with implicit
# fuzzy-match fallback) must surface WHICH tier of the lookup chain
# succeeded — agents otherwise can't tell an exact-symbol-match success
# from a fuzzy-LIKE-fallback "success" that landed on a different target.
#
# The W1233 audit found 38 sites repeating the same resolver-fallback
# shape; only cmd_annotate disclosed `resolution`. This helper is the
# shared substrate for W1242/W1243/W1244 flagship fixes + W1245 bulk
# adoption — one source of truth so the closed enum can't drift.
# ---------------------------------------------------------------------------

#: Closed enumeration of resolution-chain outcomes. Frozen so a drift-guard
#: test (`tests/test_resolution_disclosure.py`) can lock the membership;
#: extending requires a deliberate source edit there + here.
_RESOLUTION_KINDS: frozenset[str] = frozenset(
    {
        "symbol",  # found via qualified-name OR simple-name match (exact)
        "file",  # resolved by file-path exact match
        "file_substring",  # W1309: file-path fell back to LIKE %name% (substring)
        "fuzzy",  # found via LIKE / FTS fallback — likely-but-not-exact match
        "unresolved",  # nothing matched; downstream may store a dangling name
    }
)


def resolution_disclosure(
    resolution: Literal["symbol", "file", "file_substring", "fuzzy", "unresolved"],
    *,
    target: str | None = None,
    detail: Mapping[str, Any] | None = None,
    warnings_out: WarningsOut = None,
) -> dict[str, Any]:
    """Return the canonical Pattern-2 variant-D resolution-state disclosure.

    W324 cmd_annotate template: every command that calls ``find_symbol()``
    with an implicit fallback chain must surface which tier of the resolver
    succeeded so agents can distinguish an exact-symbol-match success from
    a fuzzy-LIKE-fallback or file-path-fallback "success". The
    ``partial_success`` flag is True for any non-``symbol`` resolution —
    the underlying action may still be valid (e.g., annotations relink on
    reindex), but the success verdict must reflect the degradation.

    Pattern-2c ``partial_success`` collision discipline (W1250):
        The helper sets ``partial_success = resolution != "symbol"``. When the
        caller's envelope ALSO carries a pre-existing ``partial_success`` flag
        (for orthogonal degradation reasons — truncation, timeout, no-path,
        etc.), callers MUST avoid clobbering one signal with the other:

        1. Filter the helper's ``partial_success`` key out of the merge so the
           pre-existing flag is not overwritten by a direct ``dict.update()``;
           OR
        2. OR-combine the two signals so the envelope flags partial-success
           when EITHER condition holds:
           ``partial_success = (existing_partial or (resolution != "symbol"))``.

        Reference adopters:

        - ``cmd_impact`` (W1242): pre-existing truncation flag → OR-combine.
        - ``cmd_trace`` (W1248): pre-existing no_path flag → OR-combine.
        - ``cmd_preflight`` (W1243): pre-existing error-path flag only → no
          conflict (the two flags do not co-occur on the success envelope).
        - ``cmd_diagnose`` (W1244): no pre-existing flag → direct merge.

    W1270 — reserved-key collision disclosure:
        Pre-W1270 the reserved-key filter silently dropped any
        ``resolution`` / ``partial_success`` / ``target`` entry supplied
        via ``detail``. That's a Pattern-2 silent-fallback: the helper
        claims to merge ``detail`` but quietly filters keys without
        telling the caller. ``warnings_out`` opts the call into structured
        disclosure — when a reserved key is dropped, the helper appends a
        canonical warning naming the dropped key + the recommended fix
        (OR-combine BEFORE calling). ``warnings_out=None`` (default)
        preserves byte-identical legacy behaviour.

    Args:
        resolution: One of ``{"symbol", "file", "fuzzy", "unresolved"}``.
            Must match a member of ``_RESOLUTION_KINDS``; unknown values
            raise ``ValueError`` so silent typos can't drift past lint.
        target: Optional resolved target string (qualified name, file path,
            or original input when unresolved). Echoed verbatim into the
            output dict when provided.
        detail: Optional extra fields to merge into the disclosure.
            ``resolution``, ``partial_success``, and ``target`` are
            reserved and cannot be overridden.
        warnings_out: Optional Pattern-2 warnings accumulator (``list[str]``
            or ``None``). When a non-None list is supplied AND ``detail``
            contains one or more reserved keys, the helper appends a
            structured warning per dropped key so the caller can surface
            the silent drop to agents. ``None`` (default) preserves the
            pre-W1270 silent-drop behaviour for legacy callers.

    Returns:
        A fresh dict (callers may mutate freely) with at minimum
        ``{"resolution": <kind>, "partial_success": <bool>}``, plus any
        non-reserved keys from ``detail`` and ``target`` when supplied.

    Raises:
        ValueError: If ``resolution`` is not in ``_RESOLUTION_KINDS``.
    """
    if resolution not in _RESOLUTION_KINDS:
        raise ValueError(f"resolution must be one of {sorted(_RESOLUTION_KINDS)}, got {resolution!r}")
    out: dict[str, Any] = {
        "resolution": resolution,
        "partial_success": resolution != "symbol",
    }
    if target is not None:
        out["target"] = target
    if detail:
        # Reserved keys cannot be overridden — keeps the closed-enum contract
        # tight and prevents accidental disclosure-shape drift at call sites.
        reserved = {"resolution", "partial_success", "target"}
        for k, v in detail.items():
            if k in reserved:
                # W1270: surface the silent drop via warnings_out when the
                # caller opted in. The legacy None-default path stays
                # byte-identical (silent drop) so existing adopters don't
                # regress.
                if warnings_out is not None:
                    warnings_out.append(
                        f"resolution_disclosure: detail contained reserved key "
                        f"{k!r}; dropped (use OR-combine BEFORE calling helper)"
                    )
                continue
            out[k] = v
    return out


# W1235: closed-vocabulary registry for "prerequisite missing" states.
# Pattern-2 memo G3 — Pattern-3a vocabulary fragmentation layered on
# Pattern-2 silent-fallback. Seven synonyms surfaced across substrate
# commands for the same underlying state ("the thing this command needs
# was never initialised"):
#
#   not_initialized  -- cmd_constitution.py (4 sites), cmd_laws.py (3 sites),
#                       cmd_pr_bundle.py (1 site)
#   uninitialized    -- cmd_audit_trail_verify.py (2 sites), cmd_next.py
#                       (2 sites)
#   no_trail         -- cmd_audit_trail_conformance.py (1 site)
#   no_scan          -- cmd_vulns.py (2 sites)
#   no_migrations    -- cmd_missing_index.py (1 site)
#   no_index         -- cmd_brief.py, cmd_doctor.py (4 sites),
#                       cmd_next.py, cmd_pr_bundle.py (4 sites)
#   no_data          -- cmd_agent_score.py (2 sites), cmd_causal_graph.py,
#                       cmd_doctor.py (7 sites), cmd_idempotency.py,
#                       cmd_side_effects.py, cmd_tx_boundaries.py (2 sites)
#
# Agents that branch on ``state == "not_initialized"`` silent-fail across
# the other 6 spellings today; mirrors the Pattern-3b _PARAM_ALIASES
# fix shape in ``src/roam/mcp_server.py``.
#
# This wave SHIPS the substrate only. Producer sites continue to emit
# their existing spellings until the bulk adoption wave migrates them
# through ``canonicalize_state()``. Adding the helper first lets the
# adoption sites land incrementally without breaking the lint contract.
_STATE_FAMILY_ALIASES: Mapping[str, str] = {
    "not_initialized": "not_initialized",  # canonical (self-map)
    "uninitialized": "not_initialized",
    "no_trail": "not_initialized",
    "no_scan": "not_initialized",
    "no_migrations": "not_initialized",
    "no_index": "not_initialized",
    "no_data": "not_initialized",
}

# Drift-guard set: every value in ``_STATE_FAMILY_ALIASES`` must appear
# here. The test ``test_state_family_aliases.py`` asserts the invariant
# so a future entry that introduces an unannounced canonical fails the
# lint instead of silently widening the vocabulary.
_STATE_FAMILY_CANONICALS: frozenset[str] = frozenset({"not_initialized"})


def canonicalize_state(state: str) -> str:
    """Map a state-family alias to its canonical form.

    Pattern-3a (vocabulary mismatch) normalization layered on Pattern-2
    (silent fallback). Producer sites emit one of the seven historical
    spellings in ``_STATE_FAMILY_ALIASES``; consumers that route on
    ``state`` should call this helper to collapse them onto the
    canonical ``"not_initialized"`` spelling.

    Unknown inputs (including the empty string) pass through unchanged
    so the helper composes safely with state vocabularies that have NOT
    been folded into this registry. The substrate is closed-vocabulary
    by design — extending the canonical set is a deliberate source edit,
    NOT a runtime hack.

    Args:
        state: The state string to canonicalize. May be any value; only
            entries in ``_STATE_FAMILY_ALIASES`` are rewritten.

    Returns:
        The canonical state name when ``state`` is a known alias, else
        the original ``state`` unchanged.
    """
    return _STATE_FAMILY_ALIASES.get(state, state)
