"""``roam pr-bundle`` -- proof-carrying PR bundle (R26 / W8.2).

The Roam Review MVP differentiator. Where competitors (CodeRabbit / Greptile /
Qodo) review a diff in isolation, this command lets an AI agent attach a
*structured proof envelope* to a PR demonstrating it:

  * stated its INTENT (1-line claim about what the change achieves),
  * read CONTEXT (which symbols / files it inspected, which roam commands
    it ran while preparing the change),
  * enumerated AFFECTED SYMBOLS (the symbols its diff touches),
  * named the RISKS it considered,
  * listed REQUIRED TESTS and the TEST RUNS that satisfy them,
  * declared KNOWN NON-GOALS (things the PR deliberately doesn't address),
  * gathered a ROAM VERDICT (blast/complexity/fitness/conventions).

Reviewers (humans or CI) can BLOCK on missing proof. The bundle is
incremental: an agent calls ``init``, then runs roam commands, then
``add`` / ``set`` / ``emit`` -- each step is atomic against
``.roam/pr-bundles/<branch>.json``.

The KILLER feature is ``--auto-collect`` (default on for ``emit``):
when the agent has been running other roam commands (``preflight``,
``impact``, ``critique``, ...) in the same workspace, those envelopes
land in ``.roam/responses/`` and ``pr-bundle emit`` automatically
folds their findings (risks, affected_symbols, tests_required,
commands_run) into the bundle. The agent doesn't have to manually
pipe them in.

W15.2 envelope reshape (BREAKING for any consumer reading the top-level
``auto_collect`` key): the auto-collect telemetry now lives at
``envelope["summary"]["auto_collect"]`` so it groups with the other
aggregate-data fields under ``summary`` (``side_effect_distribution``,
``risk_severity_distribution``, etc.). Zero external consumers were
reading the top-level slot at ship time — the move resolves a Pattern 3
split-brain documented in CLAUDE.md.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from roam.capability import roam_capability
from roam.commands.git_helpers import git_actor, git_branch
from roam.db.connection import find_project_root, open_db
from roam.output.confidence import confidence_level_rank
from roam.output.formatter import WarningsOut, json_envelope, to_json
from roam.runs.helpers import auto_log


# ---------------------------------------------------------------------------
# W236a / W232 secret-redaction at the producer boundary
# ---------------------------------------------------------------------------
#
# The W232 leak audit found that ``verdict`` and ``human_actor`` flow
# verbatim from the pr-bundle envelope into ``ChangeEvidence.verdict`` /
# ``ActorRef.actor_id``. A GitHub PAT planted in ``human_actor`` (e.g.
# ``alice+ghp_…@example.com``) lands inside the assurance-ref block.
#
# Fix: scrub each free-form actor / verdict string through a closed set
# of secret-shaped patterns BEFORE it lands in the envelope. When ANY
# pattern fires we (a) replace the match with ``[REDACTED]`` and (b)
# append ``"secret"`` to the envelope's top-level ``redactions[]`` array
# so downstream consumers can tell that redaction ran (Pattern 2 —
# explicit absence beats silent absence). ``"secret"`` is one of the six
# closed-enum REDACTION_REASONS at ``src/roam/evidence/_vocabulary.py``.
#
# W364 extraction: the canonical pattern set + scrubbers now live at
# ``roam.security.redact`` so other producers (W363 MCP wrappers,
# third-party plugins, drive-by collector paths) can scrub identically
# without duplicating the regex set. The private names below remain as
# thin re-export aliases for any pre-W364 test or call site that
# reaches in by attribute name.
from roam.security.redact import (
    SECRET_PATTERNS as _SECRET_PATTERNS,
    redact_secrets as _redact_secrets,
    scrub_actor_block as _scrub_actor_block,
)


# ---------------------------------------------------------------------------
# Storage layout
# ---------------------------------------------------------------------------

# All bundles live under .roam/pr-bundles/. We key by branch name to support
# multiple in-flight PRs in the same workspace. When the branch is detached
# or git is unavailable, we fall back to a single shared `pr-bundle.json`.
_BUNDLES_DIRNAME = "pr-bundles"
_DETACHED_FILENAME = "pr-bundle.json"


# When we read prior-command envelopes for --auto-collect, we look in this dir
# (same place the MCP server's handle-off writes large responses to). Agents
# that ran `roam --json preflight` etc. without MCP do NOT write here, so
# auto-collect is best-effort: tests verify the *happy path* (envelope JSON
# files dropped in this dir get folded into the bundle).
_RESPONSES_DIRNAME = "responses"


# W14.2 Synergy 2 — read-only mode soft-gate. Modes are a SOFT gate (Pattern 2
# + LAW 6): emit refuses to clobber an existing bundle when the active mode
# is read_only, but never destroys state. Higher modes (safe_edit / migration
# / autonomous_pr) all permit emit.
_MODE_RESTRICTED_STATE = "mode_restricted"


# ---------------------------------------------------------------------------
# W189 actor block — identity resolution for the pr-bundle envelope
# ---------------------------------------------------------------------------
#
# The W186 8-evidence-questions gap audit confirmed the collector at
# ``src/roam/evidence/collector.py:551-569`` ALREADY probes for an
# ``actor`` block on the pr-bundle envelope, BUT the producer
# (this module) never emitted one. As a result, every ``ChangeEvidence``
# packet built from a real pr-bundle had empty ``agent_id`` and
# ``human_actor`` fields. W189 closes that gap by adding the producer.
#
# W260 lifted the resolver into a shared helper at
# ``roam.commands.actor_helpers`` so the pr-replay synth-envelope path
# can call the same priority chain. The thin re-exports below preserve
# back-compat for existing tests and call sites (search ``tests/`` for
# ``_resolve_actor_block`` / ``_resolve_actor_kind``).
#
# Priority chain (first hit wins per field, LAW 11 — user intent over
# inference):
#   1. CLI flag (``--agent-id`` / ``--human-actor`` on ``pr-bundle emit``).
#   2. Environment variables (``ROAM_AGENT_ID`` / ``ROAM_HUMAN_ACTOR`` /
#      ``ROAM_MCP_CLIENT_ID`` / ``ROAM_CI_RUNNER_ID`` /
#      ``GITHUB_ACTIONS_RUN_ID``).
#   3. Git config ``user.email`` (human actor only).
#   4. Active run-ledger ``RunMeta.agent`` (agent id only).
#
# ``actor_kind`` is then derived from which fields ended up populated.
# Producers for ``mcp_client_id`` / ``tool_id`` are intentionally
# env-only (or NULL); ``tool_id`` is reserved for the W196 follow-up
# that adds per-tool-call MCP receipts.
from roam.commands.actor_helpers import (
    resolve_actor_block as _resolve_actor_block_impl,
    resolve_actor_kind as _resolve_actor_kind_impl,
)


def _resolve_actor_block(
    *,
    agent_id_override: str | None,
    human_actor_override: str | None,
    repo_root: Path | None = None,
) -> dict:
    """Back-compat wrapper around :func:`roam.commands.actor_helpers.resolve_actor_block`.

    New callers should import ``resolve_actor_block`` directly from
    ``roam.commands.actor_helpers``; this thin alias only exists so
    pre-W260 tests and the rest of this module continue to import the
    same symbol name they always did.
    """
    return _resolve_actor_block_impl(
        agent_id_override=agent_id_override,
        human_actor_override=human_actor_override,
        repo_root=repo_root,
    )


def _resolve_actor_kind(actor: dict) -> str:
    """Back-compat wrapper around :func:`roam.commands.actor_helpers.resolve_actor_kind`."""
    return _resolve_actor_kind_impl(actor)


def _mode_blocks_emit(repo_root: Path) -> tuple[bool, str, str | None]:
    """Return ``(blocked, active_mode_name, upgrade_to)``.

    ``blocked`` is ``True`` only when the active mode is ``read_only``;
    every higher mode allows emit. The resolver itself never raises
    (substrate rule) but we still wrap defensively so a broken
    constitution can never derail emit.
    """
    try:
        from roam.modes.policy import resolve_mode
    except Exception:
        return (False, "", None)
    try:
        active = resolve_mode(Path(repo_root))
    except Exception:
        return (False, "", None)
    if active.name == "read_only":
        # safe_edit is the lowest mode that allows pr-bundle; surface
        # that as the upgrade target.
        return (True, active.name, "safe_edit")
    return (False, active.name, None)


# Validation rule constants. Tweaked once -- referenced by validate() AND
# by tests so the contract is single-sourced.
_CONTEXT_READING_COMMANDS = ("preflight", "impact", "critique", "understand")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bundle_path(root: Path, branch: str | None = None) -> Path:
    """Resolve the on-disk path for the bundle on the current branch.

    Detached HEAD / no-git fall back to ``.roam/pr-bundle.json``.
    Branch names with ``/`` (e.g. ``feat/retry``) get the slashes replaced
    with ``__`` so the filename stays flat.
    """
    branch_name = branch if branch is not None else (git_branch() or "")
    bundles_dir = root / ".roam" / _BUNDLES_DIRNAME
    if not branch_name or branch_name == "HEAD":
        return root / ".roam" / _DETACHED_FILENAME
    safe = branch_name.replace("/", "__").replace("\\", "__")
    return bundles_dir / f"{safe}.json"


def _empty_bundle(intent: str = "") -> dict:
    """The canonical empty bundle shape. Schema lives here, single-sourced."""
    return {
        "schema": "roam-pr-bundle",
        "schema_version": 1,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "git": {},
        "intent": intent,
        "context_read": {
            "symbols_inspected": [],
            "files_inspected": [],
            "commands_run": [],
        },
        "affected_symbols": [],
        "risks": [],
        "tests_required": [],
        "tests_run": [],
        "known_non_goals": [],
        "roam_verdict": {
            "blast_radius_high": False,
            "complexity_increase": False,
            "fitness_violations": [],
            "conventions_violations": [],
        },
        # W224b — agentic-assurance approval + risk-acceptance trails.
        # Each row is a dict containing approver / reviewer / scope /
        # reason / expiry / recorded_at. Persisted in the bundle file so
        # `pr-bundle emit` can surface them in the envelope's top-level
        # ``approvals[]`` / ``accepted_risks[]`` arrays the collector
        # already reads.
        "approvals": [],
        "accepted_risks": [],
    }


def _load_bundle(path: Path) -> dict | None:
    """Read the bundle JSON, or None if missing / unreadable."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _atomic_write_bundle(path: Path, bundle: dict) -> None:
    """Write the bundle JSON atomically: tmp + rename in the same directory.

    Bundles are small; we don't optimise. The point is that a concurrent
    reader never sees a half-written file. The bundle's ``updated_at``
    stamp is refreshed here so every persisted state carries a
    write-time fingerprint.
    """
    # W17.1 atomic_io consolidation — delegate to shared helper.
    # ``default=str`` preserved by serialising upfront so non-JSON-native
    # types (e.g. ``datetime``) still round-trip the way callers expect.
    from roam.atomic_io import atomic_write_text

    bundle["updated_at"] = _utc_now()
    payload = json.dumps(bundle, indent=2, sort_keys=False, default=str, ensure_ascii=False)
    atomic_write_text(path, payload + "\n")


def _git_fingerprint(root: Path | None = None) -> dict:
    """Snapshot of git metadata at bundle-init time.

    W540 consolidation: resolve ``head_sha`` via the canonical
    ``roam.attest.cga._git_commit_sha(root)`` helper (the same helper
    that ``emit_vsa`` / ``cga`` / ``stale_refs`` already use) so every
    bundle init shells out to ``git rev-parse HEAD`` exactly ONCE. The
    legacy ``git_head_sha()`` wrapper (no ``cwd`` arg, "" sentinel) is
    kept as a back-compat shim for ``pr-analyze`` / ``audit_trail`` —
    out of scope for this task. Output shape is byte-identical: same
    keys, same string values, same SHA bytes.
    """
    out: dict[str, str] = {}
    branch = git_branch()
    if branch:
        out["branch"] = branch
    # Lazy import — avoids paying the attest-module import cost on
    # non-init pr-bundle calls (set / add / emit / validate / show).
    from roam.attest.cga import _git_commit_sha

    if root is None:
        try:
            root = find_project_root()
        except Exception:  # pragma: no cover — defensive (non-roam tree)
            root = None
    sha = _git_commit_sha(root) if root is not None else None
    if sha:
        out["head_sha"] = sha
    return out


# ---------------------------------------------------------------------------
# --auto-collect: fold prior envelopes into the bundle
# ---------------------------------------------------------------------------


def _candidate_responses(root: Path, since_ts: str | None) -> list[Path]:
    """Return prior-envelope JSON files in `.roam/responses/`.

    When ``since_ts`` is provided, only files whose mtime is at-or-after
    the bundle's ``created_at`` timestamp are considered. This keeps the
    auto-collect window scoped to "envelopes generated while preparing
    this PR" rather than scooping up the entire response cache.
    """
    responses_dir = root / ".roam" / _RESPONSES_DIRNAME
    if not responses_dir.is_dir():
        return []
    try:
        files = [p for p in responses_dir.iterdir() if p.suffix == ".json" and p.is_file()]
    except OSError:
        return []
    if since_ts:
        try:
            since_dt = datetime.fromisoformat(since_ts.replace("Z", "+00:00"))
            cutoff = since_dt.timestamp()
            files = [p for p in files if p.stat().st_mtime >= (cutoff - 1)]
        except (ValueError, OSError):
            pass
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def _merge_str_list(target: list, additions: list) -> int:
    """Append items from `additions` to `target`, dedup-preserving order.

    Returns count of items actually added.
    """
    seen = set()
    for item in target:
        if isinstance(item, str):
            seen.add(item)
    added = 0
    for item in additions:
        if not isinstance(item, str):
            continue
        if item in seen:
            continue
        seen.add(item)
        target.append(item)
        added += 1
    return added


def _merge_dict_list(target: list, additions: list, key_fields: tuple[str, ...]) -> int:
    """Append dicts from `additions` to `target`, deduping by `key_fields` tuple.

    A dict already present in target (same values for all `key_fields`)
    is not re-added. Non-dicts are skipped. Returns count added.
    """
    def key(d: dict) -> tuple:
        return tuple(d.get(k, "") for k in key_fields)

    seen = {key(d) for d in target if isinstance(d, dict)}
    added = 0
    for item in additions:
        if not isinstance(item, dict):
            continue
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        target.append(item)
        added += 1
    return added


def _harvest_envelope(bundle: dict, envelope: dict) -> dict:
    """Fold a single roam envelope's findings into `bundle` (in-place).

    Returns a small ``{section: count_added}`` map describing what was
    pulled in. Best-effort: missing / malformed fields are silently
    ignored. Never raises.
    """
    counts = {
        "commands_run": 0,
        "affected_symbols": 0,
        "risks": 0,
        "tests_required": 0,
    }
    if not isinstance(envelope, dict):
        return counts

    cmd = envelope.get("command")
    if isinstance(cmd, str) and cmd:
        # Record the source command (without arguments -- we don't have them).
        cmd_str = f"roam {cmd}"
        if _merge_str_list(bundle["context_read"]["commands_run"], [cmd_str]):
            counts["commands_run"] += 1

    # 1. affected_symbols -- some envelopes have a top-level list of dicts.
    payload_syms = envelope.get("affected_symbols")
    if isinstance(payload_syms, list):
        normalized = []
        for s in payload_syms:
            if isinstance(s, str):
                normalized.append({"name": s, "kind": "", "file": "", "blast_radius": 0})
            elif isinstance(s, dict):
                normalized.append(
                    {
                        "name": s.get("name") or s.get("symbol") or "",
                        "kind": s.get("kind", ""),
                        "file": s.get("file", ""),
                        "blast_radius": s.get("blast_radius", 0),
                    }
                )
        counts["affected_symbols"] += _merge_dict_list(
            bundle["affected_symbols"], normalized, ("name", "file")
        )

    # 2. risks -- agent_contract.risks OR top-level risks.
    risks_payload = envelope.get("risks")
    if not isinstance(risks_payload, list):
        contract = envelope.get("agent_contract")
        if isinstance(contract, dict):
            risks_payload = contract.get("risks")
    if isinstance(risks_payload, list):
        source_cmd = f"roam {cmd}" if isinstance(cmd, str) else ""
        normalized = []
        for r in risks_payload:
            if isinstance(r, str):
                normalized.append(
                    {"id": "", "severity": "M", "description": r, "source_command": source_cmd}
                )
            elif isinstance(r, dict):
                normalized.append(
                    {
                        "id": r.get("id", ""),
                        "severity": r.get("severity", "M"),
                        "description": r.get("description")
                        or r.get("message")
                        or r.get("detail", ""),
                        "source_command": r.get("source_command", source_cmd),
                    }
                )
        counts["risks"] += _merge_dict_list(bundle["risks"], normalized, ("description",))

    # 3. tests_required -- from agent_contract.next_commands OR explicit field.
    tests_payload = envelope.get("tests_required")
    if isinstance(tests_payload, list):
        normalized = []
        for t in tests_payload:
            if isinstance(t, str):
                normalized.append({"test_file": t, "reason": f"required by roam {cmd}"})
            elif isinstance(t, dict):
                normalized.append(
                    {
                        "test_file": t.get("test_file") or t.get("path") or t.get("file", ""),
                        "reason": t.get("reason", ""),
                    }
                )
        counts["tests_required"] += _merge_dict_list(
            bundle["tests_required"], normalized, ("test_file",)
        )

    # 4. summary.next_commands may include "run pytest tests/X.py" hints we
    # can lift as tests_required. Best-effort: only entries that look like
    # test files (contain "test" in the path).
    summary = envelope.get("summary")
    if isinstance(summary, dict):
        nc = summary.get("next_commands")
        if isinstance(nc, list):
            normalized = []
            for entry in nc:
                if not isinstance(entry, str):
                    continue
                if "test" in entry.lower() and (".py" in entry or ".js" in entry or ".ts" in entry):
                    # Crude: pull the test file path token.
                    for tok in entry.split():
                        if "test" in tok.lower() and ("/" in tok or "\\" in tok):
                            normalized.append(
                                {
                                    "test_file": tok,
                                    "reason": f"suggested by roam {cmd}",
                                }
                            )
                            break
            if normalized:
                counts["tests_required"] += _merge_dict_list(
                    bundle["tests_required"], normalized, ("test_file",)
                )

    return counts


def _auto_collect(bundle: dict, root: Path) -> dict:
    """Walk `.roam/responses/` and fold each envelope's findings into bundle.

    Returns aggregate counts. Best-effort throughout.
    """
    totals = {
        "envelopes_scanned": 0,
        "commands_run": 0,
        "affected_symbols": 0,
        "risks": 0,
        "tests_required": 0,
    }
    since = bundle.get("created_at")
    candidates = _candidate_responses(root, since if isinstance(since, str) else None)
    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        totals["envelopes_scanned"] += 1
        added = _harvest_envelope(bundle, data)
        for k in ("commands_run", "affected_symbols", "risks", "tests_required"):
            totals[k] += added.get(k, 0)
    # R28 integration: classify affected_symbols that don't yet carry world-
    # model fields. Covers legacy bundles + symbols folded in by the
    # response-envelope auto-collect pass above. Best-effort.
    try:
        totals["world_model_classified"] = _classify_legacy_affected_symbols(bundle)
    except Exception:
        totals["world_model_classified"] = 0
    return totals


# ---------------------------------------------------------------------------
# R28 world-model integration: side-effects + idempotency auto-classification
# ---------------------------------------------------------------------------
#
# When a symbol is added to the bundle (via `pr-bundle add affected <sym>` or
# folded in by `emit --auto-collect`), classify its side-effects + idempotency
# via the R28 detectors and surface the result as both:
#
#   1. Extra fields on the affected_symbol record (`side_effect_kinds`,
#      `idempotency_kind`, `world_model_confidence`), and
#   2. A derived risk in `bundle["risks"]` when the symbol does io_write /
#      mutation / process / non_idempotent — severity escalates per the rule
#      table in `_derive_world_model_severity()`.
#
# Best-effort throughout: if the DB doesn't exist, the symbol isn't indexed,
# or the classifiers crash, we silently fall back to confidence="unknown" and
# skip the risk append. NEVER fails the add command.

# Set of side-effect kinds that should NOT trigger a risk on their own.
_TRIVIAL_SIDE_EFFECT_KINDS = frozenset({"none", "unknown"})


# W20.5: surface unresolved-symbol state on `pr-bundle add affected <sym>`.
# Previously the command silently recorded any string as an affected symbol
# even when the symbol wasn't in the indexed symbol table -- the record
# landed with empty kind/file/blast_radius and causal_snapshot.state="no_index"
# but neither the verdict nor agent_contract.facts mentioned it. An agent
# automating bundle population would accumulate ghost symbols.
#
# Resolution states (Pattern 2 -- explicit absence):
#   - "ok"         : symbol resolved in the index; record stamped with metadata
#   - "no_db"      : no index DB exists (`roam init` never run in this repo)
#   - "not_found"  : DB exists but no symbol matches `name` or `qualified_name`
#   - "lookup_failed": DB or query crashed; treat as unresolved for safety
#
# NEVER raises and NEVER rejects the add: the record is always written. The
# envelope flips partial_success=True and the verdict names the unresolved
# state so the agent sees the warning on the very next read.

_UNRESOLVED_STATES = frozenset({"no_db", "not_found", "lookup_failed"})


def _resolve_symbol_in_index(symbol_name: str) -> tuple[dict | None, str]:
    """Look up ``symbol_name`` in the indexed symbol table.

    Returns ``(row_dict_or_None, state)`` where ``state`` is one of
    ``"ok"`` / ``"no_db"`` / ``"not_found"`` / ``"lookup_failed"``. The
    row dict (when present) carries at minimum ``name``, ``kind``, and
    ``file_path`` so the caller can stamp the affected_symbol record.

    Best-effort: any exception is folded into ``state="lookup_failed"``
    and ``row=None``. NEVER raises.
    """
    if not symbol_name or not isinstance(symbol_name, str):
        return None, "not_found"
    try:
        from roam.db.connection import db_exists
    except Exception:
        return None, "lookup_failed"
    try:
        if not db_exists():
            return None, "no_db"
    except Exception:
        return None, "lookup_failed"
    try:
        from roam.commands.resolve import find_symbol
    except Exception:
        return None, "lookup_failed"
    try:
        with open_db(readonly=True) as conn:
            row = find_symbol(conn, symbol_name)
    except Exception:
        return None, "lookup_failed"
    if row is None:
        return None, "not_found"
    # sqlite3.Row -> plain dict so the caller can read keys cleanly.
    try:
        return dict(row), "ok"
    except Exception:
        return None, "lookup_failed"


def _affected_record_is_unresolved(rec: dict) -> bool:
    """True iff ``rec`` was stamped with an unresolved resolution state.

    Used by ``_build_envelope`` to count ghost symbols on emit (W20.5).
    Records that predate this wiring (no ``resolution_state`` key) are
    treated as resolved -- legacy entries are not retroactively flagged
    so this fix is purely additive.
    """
    if not isinstance(rec, dict):
        return False
    state = rec.get("resolution_state")
    return isinstance(state, str) and state in _UNRESOLVED_STATES


def _classify_world_model_for_symbol(symbol_name: str) -> dict:
    """Run R28 classifiers for a single symbol.

    Returns a dict with keys ``side_effect_kinds``, ``idempotency_kind``,
    ``world_model_confidence``. On any failure (no DB, symbol not in index,
    classifier crash) returns the silent-fallback shape:
    ``{"side_effect_kinds": [], "idempotency_kind": "unknown",
       "world_model_confidence": "unknown"}``.

    Per-symbol queries are cheap (<100ms typical) because both classifiers
    accept ``symbol_name=`` and short-circuit the full scan.
    """
    fallback = {
        "side_effect_kinds": [],
        "idempotency_kind": "unknown",
        "world_model_confidence": "unknown",
    }
    if not symbol_name or not isinstance(symbol_name, str):
        return fallback
    try:
        from roam.world_model.side_effects import classify_side_effects
        from roam.world_model.idempotency import classify_idempotency
    except Exception:
        return fallback
    try:
        with open_db(readonly=True) as conn:
            se_list = classify_side_effects(conn, symbol_name=symbol_name)
            if not se_list:
                return fallback
            # classify_idempotency reuses the side-effects pass.
            idem_list = classify_idempotency(
                conn, symbol_name=symbol_name, side_effects=se_list
            )
    except Exception:
        return fallback
    se = se_list[0]
    kinds = list(se.kinds or [])
    # Merge confidence: lowest of the two classifications wins.
    se_conf = getattr(se, "confidence", "low") or "low"
    if idem_list:
        idem = idem_list[0]
        idem_kind = idem.kind or "unknown"
        idem_conf = getattr(idem, "confidence", "low") or "low"
    else:
        idem_kind = "unknown"
        idem_conf = "low"
    # W596: canonical confidence-LEVEL rank (includes ``unknown=0``).
    # ``min(...)`` picks the LEAST-confident label as the world-model
    # confidence floor; an "unknown" pair falls below every defined
    # level so the world-model classifier's missing signal dominates.
    chosen = min(
        (se_conf, idem_conf),
        key=lambda c: confidence_level_rank(c, fallback=-1),
    )
    return {
        "side_effect_kinds": kinds,
        "idempotency_kind": idem_kind,
        "world_model_confidence": chosen,
    }


def _derive_world_model_severity(
    side_effect_kinds: list, idempotency_kind: str
) -> str | None:
    """Derive the severity tag for an auto-added world-model risk.

    Returns ``None`` when no risk should be added (pure / unknown symbols).

    Severity matrix (locked in the W12.x integration spec):

    +------------------------+----------------+-------------+
    | side_effect_kinds      | idempotency    | severity    |
    +========================+================+=============+
    | io_write present       | non_idempotent | H           |
    | io_write present       | other          | M           |
    | mutation OR process    | any            | M           |
    | io_read only           | any            | L           |
    | mutation only (no io)  | any            | L           |
    | none / empty / unknown | any            | None (skip) |
    +------------------------+----------------+-------------+

    The "io_read only" -> L tier is a softer signal: read-only I/O is
    almost always safe, but agents still benefit from seeing it surface
    in the risks list when reviewing.
    """
    if not side_effect_kinds:
        return None
    kinds = set(side_effect_kinds)
    if kinds <= _TRIVIAL_SIDE_EFFECT_KINDS:
        return None
    has_write = "io_write" in kinds
    has_mut = "mutation" in kinds
    has_proc = "process" in kinds
    has_read = "io_read" in kinds
    non_idem = idempotency_kind == "non_idempotent"
    if has_write and non_idem:
        return "H"
    if has_write or non_idem:
        return "M"
    if has_proc:
        return "M"
    if has_mut and not has_read:
        return "L"
    if has_mut or has_read:
        return "L"
    return None


def _world_model_risk_id(symbol_name: str) -> str:
    """Canonical id for an auto-added world-model risk."""
    return f"side_effect_{symbol_name}"


def _has_existing_risk_for_symbol(bundle: dict, symbol_name: str) -> bool:
    """Return True iff bundle.risks already contains a risk keyed to ``symbol_name``.

    Matches either the canonical ``side_effect_<name>`` id, the older
    ``world_model_<name>`` id, the W15.3 ``causal_diff_{added,removed}_<name>_*``
    id pattern, the same name followed by ":" (causal-diff descriptions
    like ``"<sym>: new io_write path ..."``), or any risk whose description
    starts with the symbol name followed by a space (covers manually-added
    risks like ``add risk "useFoo writes config"``).
    """
    needles = (
        _world_model_risk_id(symbol_name),
        f"world_model_{symbol_name}",
    )
    causal_added_prefix = f"causal_diff_added_{symbol_name}_"
    causal_removed_prefix = f"causal_diff_removed_{symbol_name}_"
    desc_word_prefix = f"{symbol_name} "
    desc_colon_prefix = f"{symbol_name}:"
    for r in bundle.get("risks", []) or []:
        if not isinstance(r, dict):
            continue
        rid = r.get("id") or ""
        if rid in needles:
            return True
        if isinstance(rid, str) and (
            rid.startswith(causal_added_prefix)
            or rid.startswith(causal_removed_prefix)
        ):
            return True
        desc = r.get("description") or ""
        if isinstance(desc, str) and (
            desc.startswith(desc_word_prefix) or desc.startswith(desc_colon_prefix)
        ):
            return True
    return False


def _classify_and_annotate_affected(bundle: dict, symbol_name: str) -> None:
    """Classify ``symbol_name`` via R28 + annotate bundle in-place.

    1. Looks up the affected_symbol record for ``symbol_name`` and stamps
       it with ``side_effect_kinds`` / ``idempotency_kind`` /
       ``world_model_confidence``. If the record is missing the symbol is
       skipped (caller manages the affected list).
    2. If the classification surfaces a non-trivial finding AND no risk
       already exists for the symbol, appends a derived risk to
       ``bundle["risks"]`` with id ``side_effect_<symbol>`` and severity
       per :func:`_derive_world_model_severity`.

    NEVER raises. Best-effort.
    """
    if not symbol_name:
        return
    try:
        wm = _classify_world_model_for_symbol(symbol_name)
    except Exception:
        wm = {
            "side_effect_kinds": [],
            "idempotency_kind": "unknown",
            "world_model_confidence": "unknown",
        }
    # 1. Stamp the affected_symbol record (latest match by name).
    affected = bundle.get("affected_symbols") or []
    # W15.3 causal-graph snapshot: captured once at add-time so the emit-
    # time diff has a stable baseline. We compute it once per call (not
    # per record) — _classify_and_annotate_affected is called once per
    # add affected. Best-effort: a failed snapshot becomes
    # state="snapshot_failed" / state="no_index" rather than a crash.
    try:
        causal_snapshot = _snapshot_causal_graph_for_symbol(symbol_name)
    except Exception:
        causal_snapshot = {
            "edges": [],
            "snapshot_at": _utc_now(),
            "state": "snapshot_failed",
        }
    for rec in affected:
        if not isinstance(rec, dict):
            continue
        if rec.get("name") != symbol_name:
            continue
        # Don't clobber a previously-stamped record if classifier returned
        # the unknown-fallback (preserves legacy auto-collect data).
        if rec.get("side_effect_kinds") and wm["world_model_confidence"] == "unknown":
            continue
        rec["side_effect_kinds"] = list(wm["side_effect_kinds"])
        rec["idempotency_kind"] = wm["idempotency_kind"]
        rec["world_model_confidence"] = wm["world_model_confidence"]
        # W15.3: stamp the snapshot ONLY if no snapshot is already present
        # (preserve the original baseline across re-adds — re-snapshotting
        # would defeat the diff).
        if not rec.get("causal_snapshot"):
            rec["causal_snapshot"] = causal_snapshot

    # 2. Maybe add a derived risk (dedup by id + by symbol-prefixed
    # description).
    severity = _derive_world_model_severity(
        wm["side_effect_kinds"], wm["idempotency_kind"]
    )
    if severity is None:
        return
    if _has_existing_risk_for_symbol(bundle, symbol_name):
        return
    kinds_str = ",".join(wm["side_effect_kinds"]) or "no-effect"
    description = (
        f"{symbol_name} performs {kinds_str} ({wm['idempotency_kind']})"
    )
    bundle.setdefault("risks", []).append(
        {
            "id": _world_model_risk_id(symbol_name),
            "severity": severity,
            "description": description,
            "source_command": "auto:world-model",
        }
    )


def _classify_legacy_affected_symbols(bundle: dict) -> int:
    """During emit --auto-collect, classify any affected_symbol that doesn't
    yet carry world-model fields. Returns count classified.

    This catches symbols added BEFORE this integration shipped, plus
    symbols folded in via the response-envelope auto-collect path (those
    arrive without classification too).
    """
    affected = bundle.get("affected_symbols") or []
    n = 0
    for rec in list(affected):  # snapshot — _classify mutates the list
        if not isinstance(rec, dict):
            continue
        if "side_effect_kinds" in rec:
            continue
        name = rec.get("name") or ""
        if not name:
            continue
        try:
            _classify_and_annotate_affected(bundle, name)
        except Exception:
            continue
        n += 1
    return n


def _world_model_distributions(bundle: dict) -> dict:
    """Roll up side-effect / idempotency / risk-severity distributions.

    Returned dict has three keys:
    - ``side_effect_distribution``: ``{io_write: N, io_read: M, ...}``
    - ``idempotency_distribution``: ``{idempotent: N, non_idempotent: M, unknown: K}``
    - ``risk_severity_distribution``: ``{H: N, M: M, L: K}``
    - ``io_write_count``: convenience scalar surfaced in the verdict
    """
    se_dist: dict[str, int] = {}
    idem_dist: dict[str, int] = {}
    risk_dist: dict[str, int] = {"H": 0, "M": 0, "L": 0}
    io_write_count = 0
    for rec in bundle.get("affected_symbols") or []:
        if not isinstance(rec, dict):
            continue
        kinds = rec.get("side_effect_kinds") or []
        if isinstance(kinds, list):
            counted_io_write = False
            for k in kinds:
                if not isinstance(k, str):
                    continue
                se_dist[k] = se_dist.get(k, 0) + 1
                if k == "io_write" and not counted_io_write:
                    io_write_count += 1
                    counted_io_write = True
        idem = rec.get("idempotency_kind")
        if isinstance(idem, str) and idem:
            idem_dist[idem] = idem_dist.get(idem, 0) + 1
    for r in bundle.get("risks") or []:
        if not isinstance(r, dict):
            continue
        sev = r.get("severity")
        if isinstance(sev, str) and sev.upper() in risk_dist:
            risk_dist[sev.upper()] += 1
    return {
        "side_effect_distribution": se_dist,
        "idempotency_distribution": idem_dist,
        "risk_severity_distribution": risk_dist,
        "io_write_count": io_write_count,
    }


# ---------------------------------------------------------------------------
# W15.3 causal-graph diff integration
# ---------------------------------------------------------------------------
#
# When a symbol is added to the bundle, snapshot its R28 causal graph (which
# params / globals / env reads flow into which side-effects, returns, raises,
# mutations). At ``emit`` time, recompute the causal graph and diff against
# the snapshot — surface NEW or REMOVED ``*_to_effect → io_write:*`` edges
# as bundle risks.
#
# Rationale: knowing a symbol HAS io_write is redundant with the W12.1
# side-effects classification. Knowing the agent JUST ADDED a new io_write
# path (or REMOVED one — e.g. removed a validation guard) since the bundle
# was initialised is the load-bearing review signal.
#
# Snapshot format (per affected_symbol record):
#
#     "causal_snapshot": {
#         "edges": [
#             {"source": "param:path", "sink": "io_write:open",
#              "kind": "param_to_effect", "confidence": "high"},
#             ...
#         ],
#         "snapshot_at": "2026-05-13T12:34:56Z",
#         "state": "captured" | "no_index" | "snapshot_failed"
#     }
#
# Design choice: snapshot the FULL causal graph (all CAUSAL_KINDS), not just
# io_write edges. Cost is small (<5KB worst case per symbol, capped by R28's
# MAX_EDGES_PER_SYMBOL=50), and lossless storage means future analyses
# (validation-tightening detection, raise-flow diffs) don't require a
# re-snapshot of the pre-edit tree.
#
# Diff algorithm: O(N+M) set-difference keyed on ``(source, sink, kind)``.
# Confidence is excluded from the key — we treat a confidence promotion
# from medium→high as the SAME edge (no risk to surface).

# Edge kinds whose ADDITION since snapshot is review-significant. We
# surface NEW writes / mutations / processes as M-severity info-risks;
# the agent didn't have these paths when the bundle opened.
_CAUSAL_DIFF_INTERESTING_SINK_KINDS = frozenset({"io_write", "mutation", "process"})

# Edge kinds whose REMOVAL since snapshot is review-significant. A removed
# ``param_to_raise`` is potentially a removed validation guard (suspicious).
# A removed ``*_to_effect → io_write`` is also suspicious — the agent
# removed a write that callers may depend on. Symmetric severity = M.
_CAUSAL_DIFF_REMOVAL_SIGNIFICANT_KINDS = frozenset(
    {"param_to_effect", "global_to_effect", "env_to_effect", "param_to_raise"}
)


def _snapshot_causal_graph_for_symbol(symbol_name: str) -> dict:
    """Capture the R28 causal graph for ``symbol_name`` as a snapshot dict.

    Returns:
        ``{"edges": [...], "snapshot_at": "...", "state": "..."}``.

    States (Pattern 2 — explicit absence):
        - ``"captured"``: classifier ran and returned a graph (possibly empty)
        - ``"no_index"``: DB missing or symbol not in index
        - ``"snapshot_failed"``: classifier raised — snapshot lost

    NEVER raises. Per W15.3 the classifier is <100ms per symbol.
    """
    snapshot_at = _utc_now()
    if not symbol_name or not isinstance(symbol_name, str):
        return {"edges": [], "snapshot_at": snapshot_at, "state": "no_index"}
    try:
        from roam.world_model.causal_graph import classify_causal_graph
    except Exception:
        return {"edges": [], "snapshot_at": snapshot_at, "state": "snapshot_failed"}
    try:
        with open_db(readonly=True) as conn:
            graphs = classify_causal_graph(conn, symbol_name=symbol_name)
    except Exception:
        return {"edges": [], "snapshot_at": snapshot_at, "state": "snapshot_failed"}
    if not graphs:
        return {"edges": [], "snapshot_at": snapshot_at, "state": "no_index"}
    # The classifier returns one graph per matching symbol; pick the first.
    g = graphs[0]
    edges_out: list[dict] = []
    for e in g.edges:
        edges_out.append(
            {
                "source": e.source,
                "sink": e.sink,
                "kind": e.kind,
                "confidence": e.confidence,
            }
        )
    return {
        "edges": edges_out,
        "snapshot_at": snapshot_at,
        "state": "captured",
    }


def _edge_key(edge: dict) -> tuple[str, str, str]:
    """Canonical key for set-diff. Excludes confidence on purpose."""
    return (
        str(edge.get("source", "")),
        str(edge.get("sink", "")),
        str(edge.get("kind", "")),
    )


def _diff_causal_edges(
    snapshot_edges: list, current_edges: list
) -> tuple[list[dict], list[dict]]:
    """Return ``(added, removed)`` edges.

    ``added`` = in ``current`` but not in ``snapshot``.
    ``removed`` = in ``snapshot`` but not in ``current``.

    Keying is on ``(source, sink, kind)`` — confidence is ignored so a
    promotion / demotion isn't flagged as a meaningful change. O(N+M).
    """
    if not isinstance(snapshot_edges, list):
        snapshot_edges = []
    if not isinstance(current_edges, list):
        current_edges = []
    snap_set = {
        _edge_key(e) for e in snapshot_edges if isinstance(e, dict)
    }
    curr_set = {
        _edge_key(e) for e in current_edges if isinstance(e, dict)
    }
    added_keys = curr_set - snap_set
    removed_keys = snap_set - curr_set
    added = [e for e in current_edges if isinstance(e, dict) and _edge_key(e) in added_keys]
    removed = [e for e in snapshot_edges if isinstance(e, dict) and _edge_key(e) in removed_keys]
    return added, removed


def _causal_sink_kind(sink: str) -> str:
    """Extract the coarse sink kind from a sink label.

    ``io_write:open`` → ``io_write``; ``return`` → ``return``; ``raise:ValueError``
    → ``raise``; ``mutation:LOG`` → ``mutation``.
    """
    if not isinstance(sink, str) or not sink:
        return ""
    return sink.split(":", 1)[0]


def _causal_diff_risk_id(
    direction: str, symbol_name: str, edge: dict
) -> str:
    """Canonical id for an auto-added causal-diff risk.

    ``direction`` is ``"added"`` or ``"removed"``. The id includes source +
    sink so two distinct new edges on the same symbol don't collide.
    """
    src = str(edge.get("source", "")).replace(":", "_")
    snk = str(edge.get("sink", "")).replace(":", "_")
    return f"causal_diff_{direction}_{symbol_name}_{src}_{snk}"


def _slug_edge_for_description(edge: dict) -> tuple[str, str]:
    """Pull ``(param_label, sink_label)`` from an edge for the risk text.

    ``param:path`` → ``"path"``; ``global:CONFIG`` → ``"CONFIG"``;
    ``io_write:open`` → ``io_write:open`` (keep the prefix so reviewers see
    both the kind and the call). Defensive against malformed edges.
    """
    src = str(edge.get("source", ""))
    if ":" in src:
        _, _, rest = src.partition(":")
        param_label = rest or src
    else:
        param_label = src
    sink_label = str(edge.get("sink", ""))
    return param_label, sink_label


def _derive_causal_diff_risks(
    symbol_name: str, added: list, removed: list
) -> list[dict]:
    """Build risk records for added / removed causal edges.

    Rules:
        ADDED ``*_to_effect`` edges whose sink-kind is io_write / mutation /
        process → severity M. (The agent introduced a new write path.)

        ADDED ``param_to_return`` / ``param_to_raise`` / ``global_to_mutation``
        are NOT surfaced as risks — too noisy.

        REMOVED ``*_to_effect → io_write`` → severity M (removed write that
        downstream callers may depend on).

        REMOVED ``param_to_raise`` → severity M (potentially-removed
        validation guard).

        All other removals → skipped.
    """
    out: list[dict] = []
    for e in added:
        if not isinstance(e, dict):
            continue
        kind = e.get("kind", "")
        sink_kind = _causal_sink_kind(e.get("sink", ""))
        if kind not in (
            "param_to_effect",
            "global_to_effect",
            "env_to_effect",
        ):
            continue
        if sink_kind not in _CAUSAL_DIFF_INTERESTING_SINK_KINDS:
            continue
        param_label, sink_label = _slug_edge_for_description(e)
        description = (
            f"{symbol_name}: new {sink_kind} path '{param_label}' "
            f"-> {sink_label} introduced since bundle init"
        )
        out.append(
            {
                "id": _causal_diff_risk_id("added", symbol_name, e),
                "severity": "M",
                "description": description,
                "source_command": "auto:causal-diff",
            }
        )
    for e in removed:
        if not isinstance(e, dict):
            continue
        kind = e.get("kind", "")
        sink_kind = _causal_sink_kind(e.get("sink", ""))
        if kind not in _CAUSAL_DIFF_REMOVAL_SIGNIFICANT_KINDS:
            continue
        # Only surface io_write effect removals + raise removals.
        is_effect_removal = (
            kind in ("param_to_effect", "global_to_effect", "env_to_effect")
            and sink_kind == "io_write"
        )
        is_raise_removal = kind == "param_to_raise"
        if not (is_effect_removal or is_raise_removal):
            continue
        param_label, sink_label = _slug_edge_for_description(e)
        if is_raise_removal:
            description = (
                f"{symbol_name}: validation 'raise on {param_label}' "
                f"({sink_label}) REMOVED since bundle init"
            )
        else:
            description = (
                f"{symbol_name}: io_write path '{param_label}' "
                f"-> {sink_label} REMOVED since bundle init"
            )
        out.append(
            {
                "id": _causal_diff_risk_id("removed", symbol_name, e),
                "severity": "M",
                "description": description,
                "source_command": "auto:causal-diff",
            }
        )
    return out


def _existing_causal_diff_risk_ids(bundle: dict) -> set[str]:
    """Return all already-recorded causal_diff_*_<symbol>_*_* risk ids."""
    out: set[str] = set()
    for r in bundle.get("risks", []) or []:
        if not isinstance(r, dict):
            continue
        rid = r.get("id") or ""
        if isinstance(rid, str) and rid.startswith("causal_diff_"):
            out.add(rid)
    return out


def _run_causal_diff_pass(bundle: dict) -> dict:
    """Diff each affected_symbol's stored snapshot vs current causal graph.

    Mutates ``bundle`` in place — appends new ``causal_diff_*`` risks
    (skipping ones already present via id-dedup) and annotates each
    affected_symbol record with ``causal_diff_added`` / ``causal_diff_removed``
    arrays + a ``causal_diff_state`` field.

    Returns aggregate counts for the envelope summary:
        ``{added_total, removed_total, by_kind, high_severity_count,
           symbols_with_diff}``.

    Dedup with W12.1: if a ``side_effect_<symbol>`` risk already exists for
    a symbol, the agent has already been told to look at the symbol — we
    still emit causal-diff risks because they name a DIFFERENT load-bearing
    fact (the specific new edge), but they get a distinct id.

    NEVER raises. Best-effort throughout.
    """
    counts = {
        "added_total": 0,
        "removed_total": 0,
        "by_kind": {},
        "high_severity_count": 0,
        "symbols_with_diff": 0,
    }
    affected = bundle.get("affected_symbols") or []
    existing_ids = _existing_causal_diff_risk_ids(bundle)
    # Build the set of symbols that ALREADY have a world-model risk so we
    # can dedup at the symbol level (per spec: don't double-add a causal-
    # diff risk when a ``side_effect_<sym>`` risk is already in the bundle).
    world_model_symbols: set[str] = set()
    for r in bundle.get("risks", []) or []:
        if not isinstance(r, dict):
            continue
        rid = r.get("id") or ""
        if isinstance(rid, str) and rid.startswith("side_effect_"):
            world_model_symbols.add(rid[len("side_effect_") :])
    for rec in affected:
        if not isinstance(rec, dict):
            continue
        symbol_name = rec.get("name") or ""
        if not symbol_name:
            continue
        snap = rec.get("causal_snapshot")
        if not isinstance(snap, dict):
            # No snapshot recorded — explicit state (Pattern 2).
            rec["causal_diff_state"] = "snapshot_lost"
            continue
        snap_state = snap.get("state", "captured")
        snap_edges = snap.get("edges") or []
        # Re-snapshot the current causal graph. We deliberately re-run the
        # classifier rather than caching: emit-time is when the user has
        # made their edits.
        current = _snapshot_causal_graph_for_symbol(symbol_name)
        if current.get("state") != "captured" or snap_state != "captured":
            # Either the original snapshot was lost or we can't re-snapshot
            # now (e.g. file deleted). Explicit state, no risks.
            rec["causal_diff_state"] = (
                "snapshot_lost" if snap_state != "captured" else "rescan_failed"
            )
            continue
        added, removed = _diff_causal_edges(snap_edges, current.get("edges") or [])
        rec["causal_diff_added"] = added
        rec["causal_diff_removed"] = removed
        rec["causal_diff_state"] = "computed"
        if added or removed:
            counts["symbols_with_diff"] += 1
        # ALWAYS update the aggregate counters — they reflect the diff
        # itself, not the risk-add decision. Per-edge risk-add is
        # suppressed below for symbols that already carry a W12.1
        # ``side_effect_<sym>`` risk (we don't double-flag), but the
        # telemetry must still reflect what changed.
        counts["added_total"] += len(added)
        counts["removed_total"] += len(removed)
        for e in added:
            kind = e.get("kind", "")
            sink_kind = _causal_sink_kind(e.get("sink", ""))
            label = f"added.{kind}->{sink_kind}" if sink_kind else f"added.{kind}"
            counts["by_kind"][label] = counts["by_kind"].get(label, 0) + 1
            if (
                kind in ("param_to_effect", "global_to_effect", "env_to_effect")
                and sink_kind in _CAUSAL_DIFF_INTERESTING_SINK_KINDS
            ):
                counts["high_severity_count"] += 1
        for e in removed:
            kind = e.get("kind", "")
            sink_kind = _causal_sink_kind(e.get("sink", ""))
            label = f"removed.{kind}->{sink_kind}" if sink_kind else f"removed.{kind}"
            counts["by_kind"][label] = counts["by_kind"].get(label, 0) + 1
            if kind in _CAUSAL_DIFF_REMOVAL_SIGNIFICANT_KINDS:
                is_effect = (
                    kind in ("param_to_effect", "global_to_effect", "env_to_effect")
                    and sink_kind == "io_write"
                )
                if is_effect or kind == "param_to_raise":
                    counts["high_severity_count"] += 1
        # Build and dedup-append risks. Dedup rule (refines the spec):
        # When the symbol already has a W12.1 ``side_effect_<sym>`` risk,
        # suppress ADDED-edge per-edge risks (they piggyback on a finding
        # the agent has already been told to review). REMOVED edges still
        # surface — a removed io_write or removed validation guard is
        # NEW information that the world-model risk doesn't capture.
        if symbol_name in world_model_symbols:
            risks_iter = _derive_causal_diff_risks(symbol_name, [], removed)
        else:
            risks_iter = _derive_causal_diff_risks(symbol_name, added, removed)
        for risk in risks_iter:
            if risk["id"] in existing_ids:
                continue
            existing_ids.add(risk["id"])
            bundle.setdefault("risks", []).append(risk)
    return counts


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_bundle(
    bundle: dict, *, strict_resolved: bool = False
) -> tuple[list[str], str]:
    """Return ``(missing_proofs, state)``.

    ``state`` is ``"complete"`` or ``"incomplete"``. ``missing_proofs`` is
    a list of human-readable strings naming WHICH proofs are absent.
    Empty list + state=="complete" iff every rule passes.

    W21.4: when ``strict_resolved`` is True, also flag any
    ``affected_symbols`` record whose ``resolution_state`` is one of the
    unresolved markers (``no_db`` / ``not_found`` / ``lookup_failed``) as
    a missing proof. This makes ``--strict --strict-resolved`` block CI
    on ghost symbols. Default False preserves W21.3 behavior (additive).
    """
    missing: list[str] = []

    intent = bundle.get("intent", "")
    if not isinstance(intent, str) or not intent.strip():
        missing.append("intent (run: roam pr-bundle set intent <text>)")

    affected = bundle.get("affected_symbols") or []
    if not affected:
        missing.append(
            "affected_symbols (run: roam pr-bundle add affected <sym>, or run roam diff first)"
        )

    context = bundle.get("context_read") or {}
    commands_run = context.get("commands_run") if isinstance(context, dict) else []
    if not isinstance(commands_run, list):
        commands_run = []
    has_context_cmd = any(
        isinstance(c, str)
        and any(probe in c for probe in _CONTEXT_READING_COMMANDS)
        for c in commands_run
    )
    if not has_context_cmd:
        missing.append(
            "context_read.commands_run (must include one of: "
            + ", ".join(_CONTEXT_READING_COMMANDS)
            + ")"
        )

    tests_required = bundle.get("tests_required") or []
    tests_run = bundle.get("tests_run") or []
    if tests_required and not tests_run:
        missing.append(
            f"tests_run ({len(tests_required)} required test(s) declared but none recorded)"
        )

    roam_verdict = bundle.get("roam_verdict") or {}
    has_signal = False
    if isinstance(roam_verdict, dict):
        if (
            roam_verdict.get("blast_radius_high") is True
            or roam_verdict.get("complexity_increase") is True
            or (roam_verdict.get("fitness_violations") or [])
            or (roam_verdict.get("conventions_violations") or [])
        ):
            has_signal = True
    # ALSO accept signal when there's at least one affected_symbol with a
    # non-zero blast radius (proves the agent ran an impact-style check).
    if not has_signal:
        for s in affected:
            if isinstance(s, dict) and s.get("blast_radius", 0):
                has_signal = True
                break
    if not has_signal:
        missing.append(
            "roam_verdict (no signal -- run roam preflight / roam diff to populate)"
        )

    # W21.4: opt-in stricter gate -- when an agent passes --strict-resolved,
    # ghost symbols (resolution_state in _UNRESOLVED_STATES) count as a
    # missing proof. Without the flag, ghost symbols still surface in the
    # verdict and unresolved_affected_symbols_count, but do not block the
    # strict gate (preserves W21.3 behavior).
    if strict_resolved:
        n_unresolved = sum(
            1 for rec in affected if _affected_record_is_unresolved(rec)
        )
        if n_unresolved > 0:
            missing.append(
                f"unresolved_affected_symbols "
                f"({n_unresolved} affected symbol(s) not in index -- "
                f"run `roam init` then re-add, or remove ghost entries)"
            )

    state = "complete" if not missing else "incomplete"
    return missing, state


# ---------------------------------------------------------------------------
# W268 - on-disk permits / leases readers for the envelope
# ---------------------------------------------------------------------------
#
# The W252 producer-coverage matrix flagged ``authority`` as one of the
# most under-served evidence axes - only ``pr-bundle`` emitted an
# authority ref, and only for ``mode``. The W186 audit found permits and
# leases lived ONLY as verdict-level string facades on the bundle (no
# structured top-level array). Mirroring the W240 (approvals /
# accepted_risks) and W266 (environment_refs) "always emit, populate
# from disk when available" pattern: read ``.roam/permits/*.json`` and
# ``.roam/leases/*.json`` at envelope-build time, stamp them on the
# top-level envelope as ``permits[]`` / ``leases[]``, and let the
# collector lift each row into an ``AuthorityRef``.
#
# Both readers are best-effort: a missing directory yields ``[]`` (the
# Pattern 2 always-emit empty list); a malformed JSON file is silently
# skipped (no warning today - the directories are tooling-only and a
# producer that writes garbage is its own bug to fix).
#
# Permits note (W198): ``roam permit issue --persist`` (the W198
# writer) now writes ``.roam/permits/<permit_id>.json`` documents that
# this reader consumes. Pre-W198 the directory was always empty (the
# command was strictly a verdict facade); post-W198 it carries one
# document per issued permit. The default ``roam permit`` invocation
# (no subcommand) remains the verdict-facade path and never writes
# rows here, so the directory is still permitted to be empty.


# Deprecated W422: import from roam.permits.store instead. The wrapper is
# retained for one cycle so external code (and the W349 red-team test
# suite) can migrate without a churn-y rename. Internal callers in this
# repo were redirected at W422; do not add new callers here.
def _load_permits_from_disk(
    repo_root: Path | None,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Scan ``.roam/permits/*.json`` and return one dict per readable file.

    .. deprecated:: W422
        Import :func:`roam.permits.store.load_permits_from_disk` directly.
        This wrapper is kept for one cycle to avoid breaking external
        consumers (and the W349 red-team test suite) that import
        ``cmd_pr_bundle._load_permits_from_disk`` by name.

    See :func:`roam.permits.store.load_permits_from_disk` for the full
    contract (W379 duplicate dedup / W380 schema gate / W382 malformed
    warning surface; Pattern 2 always-emit; raw-dict pass-through).
    """
    # Local import avoids hard module-load cycle: ``permits.store`` is a
    # W198 substrate module, ``cmd_pr_bundle`` is its primary consumer.
    from roam.permits.store import load_permits_from_disk

    return load_permits_from_disk(repo_root, warnings_out=warnings_out)


def _load_leases_from_disk(
    repo_root: Path | None,
    warnings_out: list[str] | None = None,
) -> list[dict]:
    """Scan ``.roam/leases/*.json`` and return one dict per readable file.

    Delegates the file walk to :func:`roam.leases.list_leases` so the
    on-disk schema stays single-sourced - the substrate already knows
    how to parse a lease document. We pass ``include_expired=True`` /
    ``include_released=True`` because the envelope is an evidence
    snapshot: a recently-released or expired lease is still proof of
    "an agent claimed this scope during the change," which is exactly
    the authority signal an auditor wants.

    Each lease's ``to_dict()`` shape (lease_id, agent, subject_kind,
    subject, ttl_seconds, acquired_at, expires_at, state) flows
    verbatim onto the envelope; the collector's
    ``_build_authority_refs`` reads ``lease_id`` to mint an
    ``AuthorityRef(authority_kind="lease", ...)`` per row.

    **Shared between pr-bundle and pr-replay** (W272 reuse). Both
    producers stamp the resulting list onto their synth pr-bundle
    envelope's top-level ``leases[]`` so the collector materialises one
    ``AuthorityRef(authority_kind="lease", ...)`` per row. Keep this
    helper module-level so the import from
    ``roam.commands.cmd_pr_replay`` stays clean.

    W425: ``warnings_out`` threads into :func:`roam.leases.list_leases`
    so malformed / schema-invalid ``.roam/leases/*.json`` files surface
    as actionable warnings in the bundle's ``bundle_warnings`` bucket
    (mirrors the W377-batch permit reader contract).
    """
    if repo_root is None:
        return []
    try:
        from roam.leases import list_leases
    except Exception:
        return []
    try:
        leases = list_leases(
            Path(repo_root),
            include_expired=True,
            include_released=True,
            warnings_out=warnings_out,
        )
    except Exception:
        return []
    return [lease.to_dict() for lease in leases]


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------


def _build_envelope(
    bundle: dict,
    *,
    command_label: str = "pr-bundle",
    causal_diff_totals: dict | None = None,
    strict_resolved: bool = False,
    actor: dict | None = None,
    approvals: list | None = None,
    accepted_risks: list | None = None,
) -> dict:
    """Convert the on-disk bundle dict into a roam envelope ready for echo.

    ``causal_diff_totals``: optional pre-computed W15.3 causal-diff rollup
    from :func:`_run_causal_diff_pass`. Only emit threads this through
    today; other call sites (add affected, init, validate) leave it None
    and the summary fields are omitted.

    ``strict_resolved`` (W21.4): when True, ``_validate_bundle`` treats
    unresolved (ghost) affected symbols as a missing proof, so an
    otherwise-structurally-complete bundle with ghost names is marked
    ``state="incomplete"`` and gated by ``--strict``.

    ``actor`` / ``approvals`` / ``accepted_risks`` (W189): the
    agentic-assurance identity block + approval / risk-acceptance lists.
    When ``actor`` is None we resolve a fresh one from env + git config
    so EVERY call site (init / set / add / emit / validate) gets a
    populated actor block — the collector at
    ``src/roam/evidence/collector.py:551-569`` reads this field and the
    pr-bundle envelope was previously the only producer-side gap
    documented in the W186 audit. ``approvals`` and ``accepted_risks``
    default to empty lists so consumers can rely on the keys being
    present (Pattern 2 — never silent absence).
    """
    missing, state = _validate_bundle(bundle, strict_resolved=strict_resolved)
    affected = bundle.get("affected_symbols") or []
    n_aff = len(affected)
    n_risk = len(bundle.get("risks") or [])
    n_req = len(bundle.get("tests_required") or [])
    n_run = len(bundle.get("tests_run") or [])

    # W20.5: count affected_symbol records whose resolution_state flagged
    # them as ghosts (added before the symbol landed in the index, or
    # plain typos). Always surfaced -- zero is itself a positive signal
    # that every recorded symbol resolved cleanly.
    n_unresolved = sum(1 for rec in affected if _affected_record_is_unresolved(rec))

    # R28 world-model rollup (side-effect / idempotency / severity).
    distributions = _world_model_distributions(bundle)
    io_write_count = distributions.pop("io_write_count", 0)

    # W15.3 causal-diff rollup. When emit didn't compute one we still
    # surface zeros so consumers see explicit absence (Pattern 2).
    cd_totals = causal_diff_totals or {
        "added_total": 0,
        "removed_total": 0,
        "by_kind": {},
        "high_severity_count": 0,
        "symbols_with_diff": 0,
    }
    cd_added = int(cd_totals.get("added_total") or 0)
    cd_removed = int(cd_totals.get("removed_total") or 0)
    cd_high = int(cd_totals.get("high_severity_count") or 0)

    # LAW 6: verdict is standalone-readable. Mention io_write count when
    # the world-model classifier auto-flagged any symbol so reviewers see
    # the headline finding without needing the full envelope. Also mention
    # causal-diff additions when present (LAW 4 — concrete-noun anchor:
    # "1 io_write path added since init").
    io_write_phrase = (
        f" · {io_write_count} io_write symbol(s) auto-flagged"
        if io_write_count
        else ""
    )
    cd_phrase = ""
    if cd_high:
        cd_phrase = (
            f" · {cd_high} io_write path(s) changed since init"
        )
    # W20.5: surface unresolved-symbol count in the verdict so an agent
    # reading only the verdict sees the ghost-symbol warning. Empty
    # string when zero unresolved -- LAW 6 (verdict is standalone).
    unresolved_phrase = (
        f" · {n_unresolved} affected symbol(s) NOT in index"
        if n_unresolved
        else ""
    )
    if state == "complete":
        verdict = (
            f"PR proof bundle complete ({n_aff} affected · "
            f"{n_risk} risks · {n_run}/{n_req or n_run} tests run"
            f"{io_write_phrase}{cd_phrase}{unresolved_phrase})"
        )
    else:
        # Pattern 2 + LAW 6: even when incomplete, surface the causal-diff
        # headline ("N io_write paths changed since init") at the start of
        # the verdict so an agent reading only the verdict still sees the
        # load-bearing change-tracking signal.
        prefix = ""
        if cd_phrase:
            # Strip the leading " · " separator for the prefix form.
            prefix = cd_phrase.lstrip(" ·").strip() + "; "
        if unresolved_phrase:
            # Prepend the unresolved warning so agents see it before the
            # missing-proofs list (which can be long).
            prefix = unresolved_phrase.lstrip(" ·").strip() + "; " + prefix
        verdict = (
            f"PR proof bundle incomplete -- {prefix}missing: "
            f"{', '.join(missing)}"
        )

    summary = {
        "verdict": verdict,
        "state": state,
        "partial_success": state != "complete",
        "missing_proofs": missing,
        "affected_symbols_count": n_aff,
        # W20.5: count of affected_symbols whose resolution_state is one
        # of the unresolved markers (no_db / not_found / lookup_failed).
        # Always present so consumers see explicit absence (Pattern 2).
        "unresolved_affected_symbols_count": n_unresolved,
        "risks_count": n_risk,
        "tests_required_count": n_req,
        "tests_run_count": n_run,
        # R28 distributions surfaced flat in summary so agents can act on
        # them without descending into affected_symbols[].
        "side_effect_distribution": distributions["side_effect_distribution"],
        "idempotency_distribution": distributions["idempotency_distribution"],
        "risk_severity_distribution": distributions["risk_severity_distribution"],
        # W15.3 — diff of pre-edit vs post-edit causal graph for each
        # affected_symbol. Zero when no snapshots are stored OR no edges
        # changed.
        "causal_diff_distribution": {
            "added_total": cd_added,
            "removed_total": cd_removed,
            "by_kind": dict(cd_totals.get("by_kind") or {}),
            "symbols_with_diff": int(cd_totals.get("symbols_with_diff") or 0),
        },
        "causal_diff_high_severity_count": cd_high,
        # W21.4: flag in summary so consumers can tell at a glance which
        # gate ran. Always present (False by default) for Pattern 2.
        "strict_resolved": bool(strict_resolved),
    }

    # W189: resolve a default actor block when the caller didn't provide
    # one (init / set / add / validate all hit this path). The emit path
    # threads the CLI-flag-aware actor in via the ``actor`` kwarg so
    # ``--agent-id`` / ``--human-actor`` win over env + git config.
    if actor is None:
        try:
            repo_root = find_project_root()
        except Exception:
            repo_root = None
        actor = _resolve_actor_block(
            agent_id_override=None,
            human_actor_override=None,
            repo_root=repo_root,
        )
    if approvals is None:
        approvals = []
    if accepted_risks is None:
        accepted_risks = []

    # W236a / W232 producer-boundary secret scrub. Every string field on
    # the actor block AND the envelope-level ``verdict`` flow verbatim
    # through the collector into ``ChangeEvidence.verdict`` /
    # ``ActorRef.actor_id``; a planted PAT / OpenAI key / AWS key would
    # otherwise survive into the assurance-ref block. Scrub here and
    # stamp ``redactions: ["secret"]`` so consumers can tell that
    # redaction ran (Pattern 2 — explicit absence). The scrub is
    # idempotent on already-scrubbed values.
    redactions: list[str] = []
    scrubbed_actor, actor_had_secret = _scrub_actor_block(actor)
    scrubbed_verdict, verdict_had_secret = _redact_secrets(verdict)
    if scrubbed_verdict != verdict:
        verdict = scrubbed_verdict
        summary["verdict"] = verdict
    if actor_had_secret or verdict_had_secret:
        redactions.append("secret")

    # W224b — surface approval / accepted-risk records on the envelope.
    # The bundle file is the single source of truth (single-file design,
    # matching the rest of the schema). Caller-supplied approvals (e.g.
    # via the emit --approval flag) get merged on top of the persisted
    # rows. Each row is dict-copied (the collector reads dict-shaped
    # rows). Producer is permissive on shape; the collector validates
    # downstream.
    bundle_approvals = bundle.get("approvals") or []
    bundle_accepted = bundle.get("accepted_risks") or []
    if not isinstance(bundle_approvals, list):
        bundle_approvals = []
    if not isinstance(bundle_accepted, list):
        bundle_accepted = []
    approvals_out = list(bundle_approvals) + list(approvals)
    accepted_risks_out = list(bundle_accepted) + list(accepted_risks)

    # W224a / W219 — promote context_read.files_inspected to a top-level
    # context_files[] array of {path, content_hash} dicts. The collector
    # at ``src/roam/evidence/collector.py`` probes ``context_files``
    # directly; before this promotion the inspected files lived only
    # under ``context_read.files_inspected`` and were invisible to the
    # collector. Empty list when no files were inspected (Pattern 2 —
    # explicit absence so consumers can rely on the key being present).
    context_files_out: list[dict] = []
    context_read = bundle.get("context_read") or {}
    if isinstance(context_read, dict):
        inspected = context_read.get("files_inspected") or []
        if isinstance(inspected, list):
            for entry in inspected:
                if isinstance(entry, str) and entry:
                    context_files_out.append(
                        {"path": entry, "content_hash": None}
                    )
                elif isinstance(entry, dict):
                    path_value = entry.get("path") or entry.get("file") or ""
                    hash_value = (
                        entry.get("content_hash")
                        or entry.get("sha256")
                        or None
                    )
                    if path_value:
                        context_files_out.append(
                            {"path": path_value, "content_hash": hash_value}
                        )

    # W266 - materialise EnvironmentRef rows on the pr-bundle envelope.
    # The collector at ``src/roam/evidence/collector.py`` already
    # synthesises environment_refs from caller args + the envelope's git
    # block, but consumers reading the pr-bundle envelope DIRECTLY (e.g.
    # without going through the collector) previously saw no environment
    # signal at all. The W252 producer-coverage matrix flagged this as
    # the most under-served evidence axis. Each ref is dict-serialised
    # to match the rest of the envelope's JSON shape; the collector
    # rebuilds its own EnvironmentRef tuple independently so this is
    # additive and never overrides the collector's authoritative pass.
    git_block = bundle.get("git") or {}
    commit_range_for_env: str | None = None
    # W521 - promote the persisted ``git.head_sha`` (which pr-bundle init
    # stamps via ``_git_commit_sha`` so EVERY git-repo init carries a
    # real SHA) to a top-level ``commit_sha`` envelope field. The
    # ``emit_vsa.py`` collector at W509 reads ``envelope.get("commit_sha")``
    # for SLSA VSA subject digest resolution; before this promotion the
    # field was absent on every bundle envelope and the W509 fallback
    # had to redo ``git rev-parse HEAD`` on every emit. With this change
    # the producer-side identity stamp is canonical; the W509 fallback
    # stays for hand-crafted bundles (e.g. fixtures, third-party tools)
    # that bypass ``pr-bundle init``.
    commit_sha_top: str | None = None
    if isinstance(git_block, dict):
        head_sha = git_block.get("head_sha")
        if isinstance(head_sha, str) and head_sha:
            commit_range_for_env = head_sha
            commit_sha_top = head_sha
        # The persisted bundle MAY ALSO carry a ``commit_sha`` field
        # written by W521-aware init paths. Prefer it when present so
        # producers that resolve the SHA via the centralised
        # ``_git_commit_sha`` helper (not the legacy ``git_head_sha``
        # subprocess wrapper) get their value through verbatim.
        persisted_commit_sha = git_block.get("commit_sha")
        if isinstance(persisted_commit_sha, str) and persisted_commit_sha:
            commit_sha_top = persisted_commit_sha
    try:
        ws_root = find_project_root()
    except Exception:
        ws_root = None
    try:
        from roam.evidence.env_refs import build_environment_refs

        env_refs_tuple = build_environment_refs(
            commit_range=commit_range_for_env,
            workspace_root=str(ws_root) if ws_root else None,
        )
        environment_refs_out: list[dict] = [
            {"env_kind": r.env_kind, "env_id": r.env_id}
            for r in env_refs_tuple
        ]
    except Exception:
        # Best-effort - never block emit on env-ref construction.
        environment_refs_out = []

    # W268 - materialise authority producers (permits + leases) on the
    # pr-bundle envelope. The collector at
    # ``src/roam/evidence/collector.py:_build_authority_refs`` ALREADY
    # reads these top-level arrays and mints an AuthorityRef per row;
    # the W252 producer-coverage matrix flagged the missing producer
    # side. Both readers are best-effort (Pattern 2 - always emit empty
    # lists when no on-disk state exists, so consumers can rely on the
    # keys being present).
    #
    # W377-batch: the permit reader now surfaces actionable warnings for
    # malformed / schema-invalid / duplicate permit files (D3/D4/D6 from
    # the W349 red-team gaps). Warnings are accumulated into a local
    # ``bundle_warnings`` list and stamped on the envelope so an auditor
    # reviewing the bundle can see which permits were dropped and why.
    bundle_warnings: list[str] = []
    try:
        permits_out: list[dict] = _load_permits_from_disk(
            ws_root,
            warnings_out=bundle_warnings,
        )
    except Exception:
        permits_out = []
    try:
        # W425: thread the bundle_warnings bucket so malformed /
        # schema-invalid lease files surface alongside the permit
        # warnings already collected above.
        leases_out: list[dict] = _load_leases_from_disk(
            ws_root,
            warnings_out=bundle_warnings,
        )
    except Exception:
        leases_out = []

    return json_envelope(
        command_label,
        summary=summary,
        intent=bundle.get("intent", ""),
        context_read=bundle.get("context_read", {}),
        # W224a — top-level mirror of the inspected files so the
        # evidence collector picks them up (it probes context_files,
        # NOT context_read.files_inspected).
        context_files=context_files_out,
        affected_symbols=bundle.get("affected_symbols", []),
        risks=bundle.get("risks", []),
        tests_required=bundle.get("tests_required", []),
        tests_run=bundle.get("tests_run", []),
        known_non_goals=bundle.get("known_non_goals", []),
        roam_verdict=bundle.get("roam_verdict", {}),
        bundle_meta={
            "created_at": bundle.get("created_at"),
            "updated_at": bundle.get("updated_at"),
            "schema_version": bundle.get("schema_version"),
            "git": bundle.get("git", {}),
        },
        # W521 - top-level ``commit_sha`` for downstream collectors that
        # probe the envelope directly (e.g. ``roam.attest.emit_vsa`` for
        # SLSA VSA subject digest resolution). When absent (non-git
        # workspace OR pre-W521 hand-crafted bundle) consumers fall back
        # to ``bundle_meta.git.head_sha`` (Pattern 2 — multi-key probe)
        # OR the W509 ``git rev-parse HEAD`` belt-and-braces fallback.
        commit_sha=commit_sha_top,
        # W189 — agentic-assurance identity producers. The collector
        # at ``src/roam/evidence/collector.py:551-669`` reads each of
        # these top-level keys; before this change every ChangeEvidence
        # packet built from a real pr-bundle had empty agent_id /
        # human_actor / approvals / accepted_risks.
        actor=scrubbed_actor,
        approvals=approvals_out,
        accepted_risks=accepted_risks_out,
        # W266 — env signal materialised on the envelope itself (ci_job
        # / workspace / branch_range / local_run). Empty list ONLY when
        # the env_refs builder itself raised (Pattern 2 — explicit
        # absence; the builder is total so the empty path is rare).
        environment_refs=environment_refs_out,
        # W268 — authority producers (permits + leases). Each row
        # mirrors its on-disk JSON shape (.roam/permits/*.json,
        # .roam/leases/*.json). Empty list when no on-disk rows exist
        # (Pattern 2 — explicit absence; consumers can rely on the
        # keys being present). The collector lifts each row into an
        # AuthorityRef via _build_authority_refs.
        permits=permits_out,
        leases=leases_out,
        # W236a — closed-enum redactions trail. Empty list when no
        # secret-shaped substring was found (Pattern 2 — explicit
        # absence; consumers can rely on the key being present).
        redactions=redactions,
        # W377-batch — actionable warnings from the permit reader
        # (D3 / D4 / D6 of the W349 red-team gaps). Empty list when
        # every permit file parsed + validated + had a unique id
        # (Pattern 2 — consumers can rely on the key being present).
        bundle_warnings=bundle_warnings,
    )


def _finalise_envelope_redactions(env: dict) -> None:
    """Defensive scrub of the assembled envelope (W236a / W232).

    Some call sites mutate ``env["summary"]["verdict"]`` AFTER
    :func:`_build_envelope` returns (e.g. ``init`` rewrites the verdict
    with the user-supplied intent baked in). Running the scrub one last
    time right before echo guarantees no producer-side path leaks a
    secret-shaped substring out of pr-bundle. Idempotent on already-
    scrubbed values.
    """
    if not isinstance(env, dict):
        return
    redactions = list(env.get("redactions") or [])
    had_secret = False

    summary = env.get("summary")
    if isinstance(summary, dict):
        scrubbed_verdict, hit = _redact_secrets(summary.get("verdict"))
        if hit:
            summary["verdict"] = scrubbed_verdict
            had_secret = True

    actor = env.get("actor")
    if isinstance(actor, dict):
        scrubbed_actor, hit = _scrub_actor_block(actor)
        if hit:
            env["actor"] = scrubbed_actor
            had_secret = True

    if had_secret and "secret" not in redactions:
        redactions.append("secret")
    env["redactions"] = redactions


def _emit_envelope_and_log(
    env: dict,
    json_mode: bool,
    target: str = "",
    *,
    extra_event_fields: dict | None = None,
) -> None:
    """Shared echo path -- writes JSON or text, then auto-logs to the run ledger.

    W294 - ``extra_event_fields`` lets specific pr-bundle subcommands
    (notably ``add-approval``) stamp authority-shaped event fields on
    the auto-log call so the W292 collector harvester can corroborate
    the matching AuthorityRef. The kwarg is forwarded verbatim to
    :func:`roam.runs.helpers.auto_log` which enforces the closed
    whitelist; any non-whitelisted keys are silently dropped.
    """
    # W236a: belt-and-braces secret scrub at every echo point. Catches
    # call sites that post-mutate the verdict after _build_envelope.
    _finalise_envelope_redactions(env)
    auto_log(
        env,
        action="pr-bundle",
        target=target,
        extra_event_fields=extra_event_fields,
    )
    if json_mode:
        click.echo(to_json(env))
        return
    summary = env.get("summary", {})
    click.echo(f"VERDICT: {summary.get('verdict', '')}")
    click.echo(f"  state:            {summary.get('state', '?')}")
    click.echo(f"  affected symbols: {summary.get('affected_symbols_count', 0)}")
    click.echo(f"  risks:            {summary.get('risks_count', 0)}")
    click.echo(f"  tests required:   {summary.get('tests_required_count', 0)}")
    click.echo(f"  tests run:        {summary.get('tests_run_count', 0)}")
    missing = summary.get("missing_proofs") or []
    if missing:
        click.echo()
        click.echo("Missing proofs:")
        for m in missing:
            click.echo(f"  - {m}")


def _require_bundle(ctx, path: Path) -> dict:
    """Load the bundle or fail with a guided error envelope.

    ``ctx.exit(2)`` on failure -- the agent gets a clean errored envelope
    naming the init command to run.
    """
    bundle = _load_bundle(path)
    if bundle is not None:
        return bundle
    json_mode = ctx.obj.get("json") if ctx.obj else False
    verdict = (
        "no bundle on this branch -- run "
        "`roam pr-bundle init --intent <text>` first"
    )
    env = json_envelope(
        "pr-bundle",
        summary={
            "verdict": verdict,
            "state": "not_initialized",
            "partial_success": True,
            "missing_proofs": ["bundle not initialized"],
        },
        bundle_path=str(path),
        # W20.6 error-msg consistency
        agent_contract={
            "facts": ["no .roam/pr-bundles/<branch>.json for this branch"],
            "next_commands": ["roam pr-bundle init --intent <one-line claim>"],
        },
    )
    if json_mode:
        click.echo(to_json(env))
    else:
        click.echo(f"VERDICT: {verdict}")
    ctx.exit(2)


# ---------------------------------------------------------------------------
# Click group + subcommands
# ---------------------------------------------------------------------------


@roam_capability(
    name="pr-bundle",
    category="reviews",
    summary=(
        "Proof-carrying PR bundle: intent + context-read + affected + risks "
        "+ tests + non-goals + roam verdict. Reviewers can block on missing proof."
    ),
    inputs=[],
    outputs=["bundle", "missing_proofs"],
    examples=[
        "roam pr-bundle init --intent 'Add retry to S3 upload'",
        "roam pr-bundle add affected useRetry",
        "roam pr-bundle add risk 'blast radius high' --severity H",
        "roam pr-bundle emit --json",
        "roam pr-bundle validate --strict",
    ],
    tags=["pr", "review", "proof", "agent-os"],
    ai_safe=True,
    requires_index=False,
    maturity="stable",
    mcp_expose=False,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=False,
)
@click.group("pr-bundle")
@click.pass_context
def pr_bundle_group(ctx):
    """Proof-carrying PR bundle (R26 -- Roam Review MVP differentiator).

    Build a structured proof envelope an agent can attach to a PR
    demonstrating it understood AND verified its change. Stored at
    ``.roam/pr-bundles/<branch>.json`` and updated atomically.

    Typical flow:

    \b
      1. roam pr-bundle init --intent "Add retry to S3 upload"
      2. roam preflight useRetry        # agent reads context
      3. roam impact useRetry            # agent enumerates blast
      4. roam pr-bundle add affected useRetry
      5. roam pr-bundle add risk "blast radius high" --severity H
      6. roam pr-bundle add test-required tests/test_s3.py --reason "covers retry"
      7. roam pr-bundle add test-run tests/test_s3.py --passed
      8. roam pr-bundle emit             # auto-collects prior envelopes
      9. roam pr-bundle validate --strict   # exits 5 if incomplete
    """
    ctx.ensure_object(dict)


# ---------- init ----------


@pr_bundle_group.command("init")
@click.option("--intent", default="", help="One-line intent (what this PR claims to do).")
@click.pass_context
def pr_bundle_init(ctx, intent):
    """Start a fresh bundle on the current branch.

    Overwrites any existing bundle for this branch. Use ``set intent``
    later to amend the intent without touching the rest of the bundle.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _empty_bundle(intent=intent)
    # W540 - ``_git_fingerprint`` now resolves ``head_sha`` via the same
    # canonical ``_git_commit_sha`` helper as the W521 ``commit_sha``
    # stamp below. We thread the project root through + reuse the SHA
    # so init shells out to ``git rev-parse HEAD`` EXACTLY ONCE (was
    # twice pre-W540 — once via the legacy ``git_head_sha`` wrapper in
    # ``_git_fingerprint``, once via ``_git_commit_sha`` for the W521
    # stamp). Output shape stays byte-identical.
    bundle["git"] = _git_fingerprint(root)
    # W521 - populate ``git.commit_sha`` UNCONDITIONALLY when the
    # workspace is a git repo. Before W521 the bundle envelope's
    # top-level ``commit_sha`` (read by the W509 emit_vsa collector for
    # SLSA VSA subject digest resolution) was absent on every
    # ``--no-auto-collect`` run, forcing emit_vsa to re-derive identity
    # via ``git rev-parse HEAD``. Stamping at init time makes the
    # producer the source-of-truth for commit identity; the W509
    # fallback now serves only hand-crafted bundles (e.g. fixtures /
    # third-party tools that bypass ``pr-bundle init``).
    #
    # W540: ``head_sha`` and ``commit_sha`` now come from the SAME
    # subprocess invocation (``_git_fingerprint`` writes ``head_sha``;
    # we mirror that value into ``commit_sha`` here). Skip the extra
    # ``_git_commit_sha`` call entirely — the SHA is already in the
    # ``bundle["git"]`` block.
    #
    # Crash-safe: when the workspace is not a git repo OR ``git`` is
    # unavailable, ``head_sha`` is absent from the bundle and we stamp
    # a ``pre_warnings`` entry so reviewers can see why ``commit_sha``
    # is empty downstream (Pattern 2 — explicit absence beats silence).
    init_pre_warnings: list[str] = []
    resolved_sha = bundle["git"].get("head_sha") if isinstance(bundle.get("git"), dict) else None
    if resolved_sha:
        # Persist alongside ``head_sha`` (kept for back-compat with any
        # consumer reading the legacy field). ``_build_envelope`` prefers
        # ``commit_sha`` when present so the W521 producer value wins.
        bundle["git"]["commit_sha"] = resolved_sha
    else:
        init_pre_warnings.append(
            "commit_sha unresolved at init -- workspace is not a git repo "
            "OR `git rev-parse HEAD` returned no output; downstream "
            "consumers will fall back to bundle_meta.git.head_sha or to "
            "the emit-time git probe"
        )
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-init")
    env["summary"]["state"] = "initialized"
    # init never reports incomplete -- it just opened a bundle.
    env["summary"]["partial_success"] = False
    env["summary"]["verdict"] = f"pr-bundle initialised at {path.name} (intent={intent or '(unset)'})"
    env["summary"]["missing_proofs"] = []
    env["bundle_path"] = str(path)
    if init_pre_warnings:
        env["pre_warnings"] = init_pre_warnings
    _emit_envelope_and_log(env, json_mode, target=intent or "")


# ---------- set (group: set intent <text>) ----------


@pr_bundle_group.group("set")
def pr_bundle_set():
    """Set a single bundle field. Currently supports ``set intent``."""


@pr_bundle_set.command("intent")
@click.argument("text")
@click.pass_context
def pr_bundle_set_intent(ctx, text):
    """Update the bundle's intent line."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    bundle["intent"] = text
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-set-intent")
    env["bundle_path"] = str(path)
    _emit_envelope_and_log(env, json_mode, target=text)


# ---------- add (group) ----------


@pr_bundle_group.group("add")
def pr_bundle_add():
    """Append a record to a bundle section.

    Sub-verbs: ``affected``, ``risk``, ``test-required``, ``test-run``,
    ``non-goal``, ``context-cmd``, ``context-symbol``, ``context-file``.
    """


@pr_bundle_add.command("affected")
@click.argument("symbol")
@click.option("--kind", default="", help="Symbol kind (function / class / method).")
@click.option("--file", "file_path", default="", help="File path the symbol lives in.")
@click.option("--blast-radius", default=0, type=int, help="Number of dependents.")
@click.pass_context
def pr_bundle_add_affected(ctx, symbol, kind, file_path, blast_radius):
    """Record an affected symbol (the diff touches this)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    # W20.5: resolve the symbol in the indexed symbol table BEFORE stamping
    # the record so we can (a) fill in missing kind/file from the index when
    # the caller didn't pass --kind/--file, and (b) flip partial_success on
    # the envelope when the symbol is a ghost. The fix is ADDITIVE: we
    # always write the record, then warn.
    resolved, resolution_state = _resolve_symbol_in_index(symbol)
    enriched_kind = kind
    enriched_file = file_path
    if resolved is not None:
        # Prefer caller-supplied values; fall back to the indexed metadata
        # so an agent that wrote `pr-bundle add affected foo` (no flags)
        # gets a fully-populated record instead of empty strings.
        if not enriched_kind:
            enriched_kind = str(resolved.get("kind") or "")
        if not enriched_file:
            enriched_file = str(resolved.get("file_path") or "")
    record = {
        "name": symbol,
        "kind": enriched_kind,
        "file": enriched_file,
        "blast_radius": blast_radius,
        # Stamp the resolution state so emit/validate can roll it up
        # (W20.5). "ok" is the happy path; the rest are explicit-absence
        # markers per Pattern 2.
        "resolution_state": resolution_state,
    }
    _merge_dict_list(bundle["affected_symbols"], [record], ("name", "file"))
    # R28 integration: classify side-effects + idempotency for the just-added
    # symbol and (a) stamp the record with world-model fields, (b) surface a
    # derived risk when the symbol does io_write / mutation / process /
    # non_idempotent. Best-effort: never fails the add command.
    _classify_and_annotate_affected(bundle, symbol)
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-add-affected")
    env["bundle_path"] = str(path)
    # W20.5: when the symbol didn't resolve, override the verdict and add
    # an agent_contract surfacing the unresolved state. The record is still
    # written (additive fix) -- agents may legitimately track "I want to
    # address this symbol but it's not in the index yet" -- but they MUST
    # see the warning so they don't accumulate ghosts silently.
    if resolution_state in _UNRESOLVED_STATES:
        reason = {
            "no_db": "no roam index exists -- run `roam init` first",
            "not_found": "no symbol matches this name in the indexed symbol table",
            "lookup_failed": "symbol lookup crashed -- index may be corrupt",
        }.get(resolution_state, "symbol could not be resolved")
        env["summary"]["verdict"] = (
            f"WARNING: symbol '{symbol}' not in index ({resolution_state}); "
            "recorded but unresolved"
        )
        env["summary"]["partial_success"] = True
        env["summary"]["unresolved_affected_symbol"] = symbol
        env["summary"]["unresolved_affected_state"] = resolution_state
        next_cmds = [f"roam search-symbol {symbol}"]
        if resolution_state == "no_db":
            next_cmds.append("roam init")
        else:
            next_cmds.append("roam init --force")
        env["agent_contract"] = {
            "facts": [
                f"symbol '{symbol}' is not in the indexed symbol table ({reason})",
                "Record was written to the bundle anyway; downstream world-model "
                "classifiers will skip it",
                f"Verify the symbol name with `roam search-symbol {symbol}` OR "
                "refresh the index with `roam init --force`",
            ],
            "next_commands": next_cmds,
        }
    _emit_envelope_and_log(env, json_mode, target=symbol)


@pr_bundle_add.command("risk")
@click.argument("description")
@click.option(
    "--severity",
    type=click.Choice(["H", "M", "L"], case_sensitive=False),
    default="M",
    show_default=True,
)
@click.option("--id", "risk_id", default="", help="Optional stable id (e.g. 'R-001').")
@click.option(
    "--source-command",
    default="",
    help="Which roam command surfaced this risk (e.g. 'roam preflight X').",
)
@click.pass_context
def pr_bundle_add_risk(ctx, description, severity, risk_id, source_command):
    """Record a risk the agent considered (with source command + severity)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    record = {
        "id": risk_id,
        "severity": severity.upper(),
        "description": description,
        "source_command": source_command,
    }
    _merge_dict_list(bundle["risks"], [record], ("description",))
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-add-risk")
    env["bundle_path"] = str(path)
    _emit_envelope_and_log(env, json_mode, target=description[:80])


@pr_bundle_add.command("test-required")
@click.argument("test_file")
@click.option("--reason", default="", help="Why this test is required.")
@click.pass_context
def pr_bundle_add_test_required(ctx, test_file, reason):
    """Record a test file that MUST run before this PR is mergeable."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    record = {"test_file": test_file, "reason": reason}
    _merge_dict_list(bundle["tests_required"], [record], ("test_file",))
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-add-test-required")
    env["bundle_path"] = str(path)
    _emit_envelope_and_log(env, json_mode, target=test_file)


@pr_bundle_add.command("test-run")
@click.argument("test_file")
@click.option("--passed/--failed", default=True, help="Did the test pass? (default: passed)")
@click.option("--duration-ms", default=0, type=int, help="Run duration in milliseconds.")
@click.pass_context
def pr_bundle_add_test_run(ctx, test_file, passed, duration_ms):
    """Record that a test was actually run (with pass/fail + duration)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    record = {
        "test_file": test_file,
        "passed": bool(passed),
        "duration_ms": int(duration_ms),
        "ran_at": _utc_now(),
    }
    # Allow multiple runs of the same file -- key on (test_file, ran_at)
    _merge_dict_list(bundle["tests_run"], [record], ("test_file", "ran_at"))
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-add-test-run")
    env["bundle_path"] = str(path)
    _emit_envelope_and_log(env, json_mode, target=test_file)


@pr_bundle_add.command("non-goal")
@click.argument("text")
@click.pass_context
def pr_bundle_add_non_goal(ctx, text):
    """Record a known non-goal (something this PR deliberately doesn't address)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    _merge_str_list(bundle["known_non_goals"], [text])
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-add-non-goal")
    env["bundle_path"] = str(path)
    _emit_envelope_and_log(env, json_mode, target=text)


@pr_bundle_add.command("context-cmd")
@click.argument("command_string")
@click.pass_context
def pr_bundle_add_context_cmd(ctx, command_string):
    """Record a roam command the agent ran while reading context.

    Example: ``roam pr-bundle add context-cmd "roam preflight useRetry"``
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    _merge_str_list(bundle["context_read"]["commands_run"], [command_string])
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-add-context-cmd")
    env["bundle_path"] = str(path)
    _emit_envelope_and_log(env, json_mode, target=command_string)


@pr_bundle_add.command("context-symbol")
@click.argument("symbol")
@click.pass_context
def pr_bundle_add_context_symbol(ctx, symbol):
    """Record a symbol the agent inspected while reading context."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    _merge_str_list(bundle["context_read"]["symbols_inspected"], [symbol])
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-add-context-symbol")
    env["bundle_path"] = str(path)
    _emit_envelope_and_log(env, json_mode, target=symbol)


@pr_bundle_add.command("context-file")
@click.argument("file_path")
@click.pass_context
def pr_bundle_add_context_file(ctx, file_path):
    """Record a file the agent inspected while reading context."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    _merge_str_list(bundle["context_read"]["files_inspected"], [file_path])
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-add-context-file")
    env["bundle_path"] = str(path)
    _emit_envelope_and_log(env, json_mode, target=file_path)


# ---------- add-approval / add-accepted-risk (W224b) ----------
#
# W224b closes the gap surfaced by W219's producer/collector contract
# tests: the envelope schema includes ``approvals[]`` and
# ``accepted_risks[]`` arrays the collector reads into
# ``ChangeEvidence.approvals`` / ``.accepted_risks``, but no CLI surface
# existed for stamping rows. These two subcommands append a dict-shaped
# row to the bundle file's persistent ``approvals`` / ``accepted_risks``
# lists; emit then mirrors them onto the envelope's top-level array.


@pr_bundle_group.command("add-approval")
@click.option(
    "--approver",
    required=True,
    help="Email / identifier of the approver (e.g. alice@example.com).",
)
@click.option(
    "--scope",
    required=True,
    help="Approval scope (e.g. 'pr-42', 'auth-changes', 'module:billing').",
)
@click.option("--reason", default="", help="Free-form reason for the approval.")
@click.option(
    "--expiry",
    default="",
    help="ISO-8601 expiry timestamp (e.g. 2026-06-01T00:00:00Z).",
)
@click.option(
    "--id",
    "approval_id",
    default="",
    help="Optional stable id; auto-generated when omitted.",
)
@click.pass_context
def pr_bundle_add_approval(ctx, approver, scope, reason, expiry, approval_id):
    """Record an approval against the current bundle (W224b).

    The collector at ``src/roam/evidence/collector.py`` reads the
    envelope's top-level ``approvals[]`` array and materialises each row
    as an ``AuthorityRef(authority_kind="approval")`` on the resulting
    ``ChangeEvidence`` packet. Multiple approvals are allowed; each
    invocation appends one row.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    # Auto-generate a stable id when the caller didn't supply one so
    # downstream consumers can join on it. Format ``ap_<utc-stamp>``.
    if not approval_id:
        approval_id = f"ap_{_utc_now().replace(':', '').replace('-', '')}"
    # W293 — stamp ``provenance="cli_flag"`` at the producer/CLI ingestion
    # site: an operator explicitly ran ``roam pr-bundle add-approval`` to
    # record this approval. Best-effort: a helper-import failure leaves
    # the field absent so the collector's ``unknown`` fallback applies.
    record = {
        "approval_id": approval_id,
        "approver": approver,
        "scope": scope,
        "reason": reason,
        "expiry": expiry,
        "recorded_at": _utc_now(),
    }
    try:
        from roam.evidence.provenance import provenance_label
        record["provenance"] = provenance_label("cli_flag")
    except Exception:  # noqa: BLE001 - helper is supposed to never fail
        pass
    # Schema migration: older bundles may not have the field yet.
    bundle.setdefault("approvals", [])
    _merge_dict_list(bundle["approvals"], [record], ("approval_id",))
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(bundle, command_label="pr-bundle-add-approval")
    env["bundle_path"] = str(path)
    # W294 - stamp ``approval_id`` on the run-ledger event so the W292
    # collector harvester corroborates the matching approval
    # AuthorityRef and promotes it to ``provenance="run_ledger"``. The
    # whitelist filter in ``auto_log`` short-circuits when no active
    # run exists (mirroring W285's pattern); the emit always succeeds.
    _emit_envelope_and_log(
        env,
        json_mode,
        target=f"{approver}:{scope}",
        extra_event_fields={"approval_id": approval_id},
    )


@pr_bundle_group.command("add-accepted-risk")
@click.option(
    "--reviewer",
    required=True,
    help="Email / identifier of the reviewer accepting the risk.",
)
@click.option(
    "--scope",
    required=True,
    help="Risk scope (e.g. 'R-001', 'module:billing', 'blast-radius').",
)
@click.option("--reason", default="", help="Free-form rationale for accepting the risk.")
@click.option(
    "--expiry",
    default="",
    help="ISO-8601 expiry timestamp (e.g. 2026-06-01T00:00:00Z).",
)
@click.option(
    "--id",
    "risk_id",
    default="",
    help="Optional stable id; auto-generated when omitted.",
)
@click.pass_context
def pr_bundle_add_accepted_risk(ctx, reviewer, scope, reason, expiry, risk_id):
    """Record a risk acceptance against the current bundle (W224b).

    The collector materialises each row into
    ``ChangeEvidence.accepted_risks``. Use this when a reviewer has
    formally accepted a high-severity risk surfaced by ``add risk`` or
    by the world-model classifier; the row is then visible to anyone
    auditing the proof bundle.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)
    if not risk_id:
        risk_id = f"ar_{_utc_now().replace(':', '').replace('-', '')}"
    record = {
        "risk_id": risk_id,
        "reviewer": reviewer,
        "accepted_by": reviewer,
        "scope": scope,
        "reason": reason,
        "rationale": reason,
        "expiry": expiry,
        "recorded_at": _utc_now(),
    }
    bundle.setdefault("accepted_risks", [])
    _merge_dict_list(bundle["accepted_risks"], [record], ("risk_id",))
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(
        bundle, command_label="pr-bundle-add-accepted-risk"
    )
    env["bundle_path"] = str(path)
    _emit_envelope_and_log(env, json_mode, target=f"{reviewer}:{scope}")


# ---------- emit ----------


@pr_bundle_group.command("emit")
@click.option(
    "--auto-collect/--no-auto-collect",
    default=True,
    show_default=True,
    help="Fold envelopes from .roam/responses/ into the bundle before emitting.",
)
@click.option(
    "--strict/--no-strict",
    "strict",
    default=None,
    help=(
        "Exit with code 5 (gate-fail) when the bundle is structurally "
        "incomplete (missing intent / affected / context-cmd / "
        "tests / verdict signal). Default off, but `--ci` implies "
        "--strict; pass --no-strict to override."
    ),
)
@click.option(
    "--strict-resolved/--no-strict-resolved",
    "strict_resolved",
    default=None,
    help=(
        "Additive to --strict: also exit 5 if any affected symbol is "
        "unresolved (not in the index). Without --strict this flag is "
        "advisory -- the unresolved count still surfaces in the envelope. "
        "Default off, but `--ci` implies --strict-resolved; "
        "pass --no-strict-resolved to override."
    ),
)
@click.option(
    "--agent-id",
    "agent_id",
    default=None,
    help=(
        "Override the AI-agent identifier on the actor block (W189). "
        "Wins over ROAM_AGENT_ID and the active run-ledger agent. "
        "Default: resolved from env / run-ledger; empty when neither is set."
    ),
)
@click.option(
    "--human-actor",
    "human_actor",
    default=None,
    help=(
        "Override the human-actor identifier on the actor block (W189). "
        "Wins over ROAM_HUMAN_ACTOR and `git config user.email`. "
        "Default: resolved from env / git config; empty when neither is set."
    ),
)
@click.option(
    "--slsa-l3",
    "slsa_l3",
    is_flag=True,
    help=(
        "W451: also emit a SLSA v1 Verification Summary Attestation "
        "(VSA, predicateType https://slsa.dev/verification_summary/v1) "
        "projected from the bundle's ChangeEvidence + an in-toto "
        "statement attesting to the active run-ledger HMAC root. "
        "Outputs land alongside the bundle as `.roam/pr-bundle/<stem>.vsa.json` "
        "and `<stem>.run-ledger-root.json`. Pair with --sign --keyless (or "
        "--sign --key PATH) to cosign-sign both statements (Fulcio + Rekor)."
    ),
)
@click.option(
    "--sign",
    "sign",
    is_flag=True,
    help=(
        "Cosign-sign the emitted VSA + run-ledger root statements. "
        "Requires --slsa-l3. Pair with --key PATH for offline or "
        "--keyless for OIDC (Fulcio + Rekor). Graceful skip with a "
        "clear reason when cosign isn't on PATH."
    ),
)
@click.option(
    "--key",
    "sign_key",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Cosign private key. Pair with --sign for offline signing.",
)
@click.option(
    "--keyless",
    "sign_keyless",
    is_flag=True,
    help=(
        "Cosign keyless OIDC signing via Fulcio + Rekor. Requires "
        "ambient OIDC (GitHub Actions, GCP workload identity) or "
        "interactive browser flow. Pair with --sign."
    ),
)
@click.pass_context
def pr_bundle_emit(ctx, auto_collect, strict, strict_resolved, agent_id, human_actor, slsa_l3, sign, sign_key, sign_keyless):
    """Finalise + print the bundle envelope.

    With ``--auto-collect`` (default), any envelopes generated since
    bundle init under ``.roam/responses/`` are folded in. This is the
    feature that makes the workflow ergonomic: agents run ``init``,
    then run their normal roam commands, then call ``emit`` -- no manual
    bookkeeping required.

    ``--strict``: exit 5 if the bundle is structurally incomplete
    (intent / affected / context-cmd / tests / verdict signal).

    ``--strict-resolved``: also exit 5 if any affected symbol is
    unresolved (``resolution_state`` in ``no_db`` / ``not_found`` /
    ``lookup_failed``). Additive to ``--strict``: without it, the gate
    preserves W21.3 behavior. With it, ghost-symbol bundles fail CI.

    ``--agent-id`` / ``--human-actor`` (W189): override the agentic-
    assurance ``actor`` block on the emitted envelope. CLI flags win
    over the ``ROAM_AGENT_ID`` / ``ROAM_HUMAN_ACTOR`` env vars (LAW 11);
    when neither is provided we fall back to the active run-ledger
    agent (for ``agent_id``) and to ``git config user.email`` (for
    ``human_actor``). The full priority chain lives in
    :func:`_resolve_actor_block`.

    Without ``--strict`` the envelope is always echoed with exit 0 so
    reviewers can see the bundle even when proofs are missing.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    # W21.6 --ci composition: under --ci, default strict=True so CI fails
    # on incomplete bundles. strict is a tri-state Click option
    # (--strict/--no-strict/None=unset); explicit user flags ALWAYS win
    # over the --ci inference (LAW 11).
    # W22.3 --ci also implies --strict-resolved: a CI run that gates on
    # --strict but tolerates unresolved (ghost) symbols is half-measured;
    # if any blast-radius symbol failed to resolve, the bundle isn't
    # trustworthy enough to gate-pass. Same tri-state pattern: explicit
    # --no-strict-resolved beats --ci (LAW 11).
    ci_mode = ctx.obj.get("ci_mode", False) if ctx.obj else False
    if strict is None:
        strict = bool(ci_mode)
    if strict_resolved is None:
        strict_resolved = bool(ci_mode)
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)

    # W189: resolve the actor block ONCE for this emit invocation. CLI
    # flags win over env / git config / run-ledger (LAW 11). Used by
    # both the mode-blocked early-return and the normal-finalise paths
    # so a read-only-blocked bundle still carries the identity.
    resolved_actor = _resolve_actor_block(
        agent_id_override=agent_id,
        human_actor_override=human_actor,
        repo_root=root,
    )

    # W14.2 Synergy 2 — mode soft-gate. In read_only mode, refuse to
    # finalise the bundle but DO NOT clobber the on-disk file. Surface
    # an explicit ``mode_restricted`` state + upgrade verdict so the
    # agent sees exactly what to run next (LAWs 6 + 12, Pattern 2).
    blocked, active_mode, upgrade_to = _mode_blocks_emit(root)
    if blocked:
        verdict = (
            f"pr-bundle emit blocked: active mode is {active_mode}; "
            f"run `roam mode {upgrade_to}` to enable"
        )
        env = json_envelope(
            "pr-bundle",
            summary={
                "verdict": verdict,
                "state": _MODE_RESTRICTED_STATE,
                "partial_success": True,
                "active_mode": active_mode,
                "upgrade_mode": upgrade_to,
            },
            bundle_path=str(path),
            # W224c — always surface ``mode`` at the top level so the
            # evidence collector picks it up uniformly across blocked
            # and non-blocked paths (Pattern 2 — explicit absence).
            mode=active_mode or "unmoded",
            mode_block={
                "active_mode": active_mode,
                "upgrade_mode": upgrade_to,
                "reason": "active mode is read_only; emit refuses to finalise",
            },
            # W189: even when the bundle is mode-blocked we surface the
            # actor block + empty approvals / accepted_risks so an
            # external collector sees explicit absence (Pattern 2).
            actor=resolved_actor,
            approvals=[],
            accepted_risks=[],
        )
        _emit_envelope_and_log(env, json_mode, target=bundle.get("intent", "")[:80])
        if strict:
            ctx.exit(5)
        return

    collect_totals: dict[str, Any] = {"enabled": bool(auto_collect)}
    if auto_collect:
        collect_totals.update(_auto_collect(bundle, root))
    # W15.3 causal-diff pass — always runs at emit (independent of
    # --auto-collect). Diffs each affected_symbol's stored causal snapshot
    # against a freshly-computed causal graph; surfaces NEW or REMOVED
    # io_write paths as risks. Best-effort: never crashes emit.
    try:
        causal_diff_totals = _run_causal_diff_pass(bundle)
    except Exception:
        causal_diff_totals = {
            "added_total": 0,
            "removed_total": 0,
            "by_kind": {},
            "high_severity_count": 0,
            "symbols_with_diff": 0,
            "state": "diff_failed",
        }
    # Persist the bundle when either pass modified it. The causal-diff
    # pass always stamps ``causal_diff_state`` on every affected_symbol,
    # so we persist whenever there's at least one affected record (the
    # state field is new since W15.3 and useful for downstream consumers).
    _atomic_write_bundle(path, bundle)
    env = _build_envelope(
        bundle,
        command_label="pr-bundle",
        causal_diff_totals=causal_diff_totals,
        strict_resolved=strict_resolved,
        actor=resolved_actor,
        approvals=[],
        accepted_risks=[],
    )
    # W15.2 envelope reshape — auto_collect now lives under ``summary`` so it
    # sits alongside the other aggregate-data fields
    # (``side_effect_distribution``, ``risk_severity_distribution``, ...).
    # BREAKING (Pattern 3 consistency): consumers must read
    # ``envelope["summary"]["auto_collect"]`` instead of top-level
    # ``envelope["auto_collect"]``. Zero external consumers at ship time
    # (feature just landed); the top-level slot is gone, not aliased.
    env["summary"]["auto_collect"] = collect_totals
    env["bundle_path"] = str(path)
    # W224c — always emit ``mode`` (top-level) and ``summary.active_mode``.
    # Previously the producer only surfaced ``mode`` when the bundle was
    # blocked by ``read_only``; on a normal emit neither field was set
    # and the collector's mode probe came up empty (W219 gap). Default
    # to ``"unmoded"`` when no mode is declared rather than omitting
    # the key (Pattern 2 — explicit absence).
    env["mode"] = active_mode or "unmoded"
    env["summary"]["active_mode"] = active_mode or "unmoded"

    # W451 - SLSA SRC-L3 wire-up. When --slsa-l3 is set, project the
    # bundle envelope through the evidence collector into a
    # ChangeEvidence packet, wrap it in a SLSA VSA statement, AND
    # (when ROAM_RUN_ID is active) emit a second attestation rooted
    # at the run-ledger HMAC chain tip. Optional cosign-sign both.
    slsa_l3_result: dict[str, Any] | None = None
    if slsa_l3:
        slsa_l3_result = _emit_slsa_l3_attestations(
            root=root,
            envelope=env,
            sign=sign,
            sign_key=sign_key,
            sign_keyless=sign_keyless,
        )
        env["slsa_l3"] = slsa_l3_result

    _emit_envelope_and_log(env, json_mode, target=bundle.get("intent", "")[:80])
    if strict and env["summary"]["state"] != "complete":
        ctx.exit(5)


def _emit_slsa_l3_attestations(
    *,
    root: Path,
    envelope: dict,
    sign: bool,
    sign_key: str | None,
    sign_keyless: bool,
) -> dict[str, Any]:
    """W451 — emit SLSA VSA + run-ledger root attestations.

    W486: delegates to the shared :func:`roam.attest.emit_vsa.emit_pr_bundle_slsa_l3`
    helper. The wrapper stays in place so existing callers keep the
    same import path, and to localise any future pr-bundle-specific
    pre/post-processing.
    """
    from roam.attest.emit_vsa import emit_pr_bundle_slsa_l3

    return emit_pr_bundle_slsa_l3(
        root=root,
        envelope=envelope,
        sign=sign,
        sign_key=sign_key,
        sign_keyless=sign_keyless,
    )


# ---------- validate ----------


@pr_bundle_group.command("validate")
@click.option(
    "--strict/--no-strict",
    "strict",
    default=None,
    help=(
        "Exit with code 5 (gate-fail) when the bundle is structurally "
        "incomplete (missing intent / affected / context-cmd / "
        "tests / verdict signal). Default off, but `--ci` implies "
        "--strict; pass --no-strict to override."
    ),
)
@click.option(
    "--strict-resolved/--no-strict-resolved",
    "strict_resolved",
    default=None,
    help=(
        "Additive to --strict: also exit 5 if any affected symbol is "
        "unresolved (not in the index). Without --strict this flag is "
        "advisory -- the unresolved count still surfaces in the envelope. "
        "Default off, but `--ci` implies --strict-resolved; "
        "pass --no-strict-resolved to override."
    ),
)
@click.pass_context
def pr_bundle_validate(ctx, strict, strict_resolved):
    """Check the bundle is complete; flag missing proofs.

    ``--strict``: exit 5 if the bundle is structurally incomplete
    (intent / affected / context-cmd / tests / verdict signal).

    ``--strict-resolved``: also exit 5 if any affected symbol is
    unresolved (not in the index). Additive to ``--strict``: without it,
    the W21.3 gate behavior is preserved.

    Exits 0 when complete. Without ``--strict`` always exits 0 so
    reviewers can see WHICH proofs are missing without the gate
    short-circuiting.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    # W21.6 --ci composition: under --ci, default strict=True so CI fails
    # on incomplete bundles. Explicit --strict / --no-strict ALWAYS win
    # over the --ci inference (LAW 11).
    # W22.3 --ci also implies --strict-resolved: a CI run that gates on
    # --strict but tolerates unresolved (ghost) symbols is half-measured.
    # Same tri-state pattern: explicit --no-strict-resolved beats --ci.
    ci_mode = ctx.obj.get("ci_mode", False) if ctx.obj else False
    if strict is None:
        strict = bool(ci_mode)
    if strict_resolved is None:
        strict_resolved = bool(ci_mode)
    root = find_project_root()
    path = _bundle_path(root)
    bundle = _require_bundle(ctx, path)

    # W14.2 Synergy 2 — mode soft-gate mirrors emit. In read_only mode,
    # validate --strict refuses to certify completeness: an agent under
    # read_only cannot legitimately stamp a PR ready for merge. Bundle
    # state is preserved; exit code 5 fires only with --strict.
    blocked, active_mode, upgrade_to = _mode_blocks_emit(root)
    if blocked:
        verdict = (
            f"pr-bundle validate blocked: active mode is {active_mode}; "
            f"run `roam mode {upgrade_to}` to enable"
        )
        env = json_envelope(
            "pr-bundle-validate",
            summary={
                "verdict": verdict,
                "state": _MODE_RESTRICTED_STATE,
                "partial_success": True,
                "active_mode": active_mode,
                "upgrade_mode": upgrade_to,
            },
            bundle_path=str(path),
            mode_block={
                "active_mode": active_mode,
                "upgrade_mode": upgrade_to,
                "reason": "active mode is read_only; validate refuses to certify",
            },
        )
        _emit_envelope_and_log(env, json_mode, target="validate")
        if strict:
            ctx.exit(5)
        return

    env = _build_envelope(
        bundle,
        command_label="pr-bundle-validate",
        strict_resolved=strict_resolved,
    )
    env["bundle_path"] = str(path)
    _emit_envelope_and_log(env, json_mode, target="validate")
    if strict and env["summary"]["state"] != "complete":
        ctx.exit(5)
