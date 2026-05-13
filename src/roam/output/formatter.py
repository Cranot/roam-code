"""Token-efficient text formatting for AI consumption."""

from __future__ import annotations

import json as _json
import os
import time
from datetime import datetime, timezone

# Envelope schema versioning (semver: major.minor.patch)
# bumped to 1.1.0 to signal additive enhancements:
# `evidence.matched_patterns` on detector findings,
# `framework`/`framework_autodetected`/`framework_unknown` in math summary
# , `roi_band` on debt items, `context_lines` on rule
# violations + concerns (D6). All optional — pre-1.1 consumers continue
# to work; new consumers can opt in to the richer fields.
ENVELOPE_SCHEMA_VERSION = "1.1.0"
ENVELOPE_SCHEMA_NAME = "roam-envelope-v1"

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


# ── Token budget truncation ──────────────────────────────────────────

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

    # Deep copy to avoid mutating the original
    result: dict = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = dict(v)
        elif isinstance(v, list):
            result[k] = list(v)
        else:
            result[k] = v

    # Fields that must never be truncated
    preserved = {
        "command",
        "summary",
        "schema",
        "schema_version",
        "version",
        "project",
        "_meta",
    }

    # Sort list fields by importance before truncation so the most
    # important items survive progressive shrinking.
    any_importance_sorted = False
    for key, value in list(result.items()):
        if key in preserved:
            continue
        if isinstance(value, list):
            sorted_val, was_sorted = _sort_by_importance(value)
            if was_sorted:
                result[key] = sorted_val
                any_importance_sorted = True

    # Track how many items we omit across all list fields
    total_omitted = 0

    # Progressively shrink list fields until we fit
    # Start by keeping 10, then 5, then 3, then 1 item(s)
    for cap in (10, 5, 3, 1):
        for key, value in list(result.items()):
            if key in preserved:
                continue
            if isinstance(value, list) and len(value) > cap:
                result[key] = value[:cap]

        test_json = _json.dumps(result, default=str, sort_keys=True)
        if len(test_json) <= char_limit:
            break

    # If still too large, drop non-preserved keys entirely
    test_json = _json.dumps(result, default=str, sort_keys=True)
    if len(test_json) > char_limit:
        drop_keys = [k for k in list(result.keys()) if k not in preserved]
        for k in drop_keys:
            del result[k]
            test_json = _json.dumps(result, default=str, sort_keys=True)
            if len(test_json) <= char_limit:
                break

    # Count total omitted items across all truncated list fields
    for key in data:
        if key in preserved:
            continue
        orig = data.get(key)
        kept = result.get(key)
        if isinstance(orig, list):
            kept_len = len(kept) if isinstance(kept, list) else 0
            total_omitted += len(orig) - kept_len

    # Annotate summary with truncation metadata
    if "summary" in result and isinstance(result["summary"], dict):
        result["summary"]["truncated"] = True
        result["summary"]["budget_tokens"] = budget
        result["summary"]["full_output_tokens"] = estimate_tokens(full_json)
        if total_omitted > 0:
            result["summary"]["omitted_low_importance_nodes"] = total_omitted
        if any_importance_sorted:
            result["summary"]["kept_highest_importance"] = True

    return result


def _compact_mode_enabled() -> bool:
    """Return True when CLI requested compact/agent output mode."""
    try:
        import click

        ctx = click.get_current_context(silent=True)
        if ctx and isinstance(ctx.obj, dict):
            return bool(ctx.obj.get("compact") or ctx.obj.get("agent"))
    except Exception:
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

    Items are typically either bare strings or dicts with a ``message``
    / ``title`` / ``description`` / ``rule_id`` field. Falls back to
    ``str(item)`` when nothing useful is found.
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("message", "title", "description", "verdict", "observation", "rule_id"):
            v = item.get(key)
            if isinstance(v, str) and v:
                return v
    return str(item)


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
        "subcommands",
        "scenarios",
        "actions",
        "exits",
        "leaks",
        "gaps",
        "movers",
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


def _derive_agent_contract(out: dict, summary: dict) -> dict:
    """Build the bounded ``agent_contract`` derived block.

    Generic across all envelopes — pulls structural cues (verdict,
    numeric counts in summary, error lists, next_steps) without
    requiring per-command opt-in. Agents on tight context budgets can
    read just this dict; full-payload consumers ignore it.
    """
    facts: list[str] = []
    risks: list[str] = []
    next_commands: list[str] = []
    confidence: float | None = None

    verdict = summary.get("verdict")
    if isinstance(verdict, str) and verdict:
        facts.append(verdict[:_AGENT_CONTRACT_STR_TRUNCATE])

    # Numeric counts / scores from summary become concrete-noun facts.
    # LAW 4 (CLAUDE.md): humanize ``critical: 5`` → ``"5 critical
    # findings"``. State / metadata keys (state, partial_success, etc.)
    # stay in ``summary`` but do NOT pollute ``facts``. Dict/list values
    # are skipped — they aren't auto-summarizable.
    for key, value in summary.items():
        if key in _AGENT_CONTRACT_FACT_SKIP_KEYS:
            continue
        # Convention: leading-underscore keys are private metadata; never
        # surface them as user-facing facts. Covers ``_meta``, ``_trace``,
        # and any future internal annotation.
        if key.startswith("_"):
            continue
        if isinstance(value, bool):
            continue
        if key.endswith("_definition") or key.endswith("_distribution"):
            continue
        if isinstance(value, (int, float)):
            facts.append(
                _humanize_summary_fact(key, value)[:_AGENT_CONTRACT_STR_TRUNCATE]
            )
            if len(facts) >= _AGENT_CONTRACT_MAX_FACTS:
                break

    # Risks — first non-empty list among the conventional risk keys.
    for key in _RISK_KEYS:
        items = out.get(key)
        if isinstance(items, list) and items:
            for item in items[:_AGENT_CONTRACT_MAX_RISKS]:
                msg = _stringify_risk_item(item)
                risks.append(msg[:_AGENT_CONTRACT_STR_TRUNCATE])
            break

    # Confidence — pull from summary; either 0..1 float or a 0..100 int.
    raw_conf = summary.get("confidence")
    if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool):
        confidence = float(raw_conf)

    # Next steps — try the structured ``next_steps`` payload first,
    # then ``summary.next_commands`` as a less-formal fallback.
    next_source = out.get("next_steps")
    if not isinstance(next_source, list):
        next_source = summary.get("next_commands")
    if isinstance(next_source, list):
        for step in next_source[:_AGENT_CONTRACT_MAX_NEXT]:
            if isinstance(step, dict):
                cmd = step.get("command") or step.get("cmd") or step.get("action") or ""
            else:
                cmd = str(step)
            if cmd:
                next_commands.append(cmd[:_AGENT_CONTRACT_STR_TRUNCATE])

    return {
        "facts": facts,
        "risks": risks,
        "next_commands": next_commands,
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
        # Content-hash the envelope so re-running the same command with the
        # same inputs dedupes naturally (the bundle's auto-collect should not
        # see N copies of the same `roam health` run).
        h = hashlib.sha256(
            _json.dumps(envelope, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:12]
        # Sanitise command for filename use (slashes / spaces would be odd
        # but we have e.g. "pr-bundle-emit" already — slugify defensively).
        safe_cmd = "".join(c if (c.isalnum() or c in "-_") else "_" for c in command)
        out_path = responses_dir / f"{safe_cmd}_{h}.json"
        out_path.write_text(
            _json.dumps(envelope, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        # Best-effort. Never break the parent command.
        return


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
    try:
        from roam.cli import _get_active_deprecation_notice

        _depr_notice = _get_active_deprecation_notice()
        if _depr_notice and "deprecation_warning" not in summary:
            summary["deprecation_warning"] = _depr_notice
    except Exception:
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
            if (
                "next_commands" not in explicit_contract
                and auto_contract.get("next_commands")
            ):
                merged["next_commands"] = auto_contract["next_commands"]
            out["agent_contract"] = merged
        else:
            out["agent_contract"] = auto_contract

    # Non-deterministic metadata in _meta — kept separate so content
    # keys produce identical JSON across invocations (LLM cache-friendly).
    out["_meta"] = {
        "timestamp": ts,
        "index_age_s": _index_age_seconds(),
    }

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

    return out


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
    except Exception:
        pass
    return None


def _project_name() -> str:
    """Basename of the project root directory."""
    try:
        from roam.db.connection import find_project_root

        return find_project_root().name
    except Exception:
        return ""


def table_to_dicts(headers: list[str], rows: list[list[str]]) -> list[dict]:
    """Convert table headers + rows into a list of dicts (for JSON output)."""
    return [dict(zip(headers, row)) for row in rows]


# ── Compact output mode ──────────────────────────────────────────────


def compact_json_envelope(command: str, **payload) -> dict:
    """Minimal JSON envelope — strips version/timestamp/project overhead.

    For agents using --compact: emits only command name, summary, and payload.
    Saves ~150-200 tokens per call.
    """
    out = {"command": command}
    out.update(payload)
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
    preserved = {
        "command",
        "schema",
        "schema_version",
        "version",
        "project",
        "_meta",
    }
    list_counts: dict[str, int] = {}

    # Build stripped result: drop all list-valued payload fields
    result: dict = {}
    for k, v in data.items():
        if k in preserved:
            result[k] = v
        elif k == "summary":
            if keep_summary:
                result[k] = dict(v) if isinstance(v, dict) else v
        elif isinstance(v, list):
            # Drop list — record its count
            list_counts[k] = len(v)
        else:
            result[k] = v

    has_non_empty_lists = any(c > 0 for c in list_counts.values())

    # Annotate summary with progressive disclosure flags.
    # Keep the annotation minimal so summary is always <= detail in size.
    if "summary" not in result:
        result["summary"] = {}
    if isinstance(result.get("summary"), dict):
        result["summary"]["detail_available"] = True
        if has_non_empty_lists:
            result["summary"]["truncated"] = True

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
