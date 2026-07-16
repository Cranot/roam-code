"""Proof-oriented episode ledger for compiler savings measurement.

The hook boundary writes append-only JSONL because it must stay tiny,
dependency-free, and fail-open.  Analysis materializes those events plus
``compile-runs.jsonl`` into a local SQLite database so joins are idempotent,
deduplicated, and queryable.

This module deliberately separates:

* **measurement admissibility** — are prompt, compile, and terminal records
  joined with enough coverage to support analysis?
* **policy admissibility** — do we also know the execution-health context
  needed to turn repeated failures into routing or quarantine policy?

Frequency alone never promotes a savings claim.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from roam.procedure_mining import build_procedure_atlas, normalized_episode_tokens

EPISODE_SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION = 3
MIN_ADMISSIBLE_EPISODES = 30
MIN_COVERAGE_PCT = 95.0
TERMINAL_GRACE_SECONDS = 600
MIN_LIVE_HOOK_VERSION = 6

EVENT_LOG_NAME = "episodes.jsonl"
TRANSCRIPT_EVENT_LOG_NAME = "transcript-episodes.jsonl"
COMPILE_LOG_NAME = "compile-runs.jsonl"
LEDGER_DB_NAME = "episodes.sqlite"
VALIDATED_HEALTH_STATES = frozenset({"verification_passed", "verification_failed"})

EVENT_FIELD_DEFINITIONS: dict[str, str] = {
    "event_id": "stable unique event identifier used for idempotent ingestion",
    "episode_id": "join key spanning prompt compilation and terminal outcome",
    "event_type": (
        "closed event kind: prompt_submitted, stop_decision, stop_continuation, "
        "intervention_assignment, or intervention_observation"
    ),
    "ts": "UTC event timestamp in ISO-8601 form",
    "session_id": "host session identifier; local-only join metadata",
    "turn_seq": "monotonic prompt sequence within the host session",
    "terminal": "whether this event closes the episode",
    "outcome": "closed terminal or intermediate outcome classification",
    "duration_ms": "elapsed milliseconds from prompt submission to this event",
    "changed_files": "count of changed tracked and untracked files at stop time",
    "diff_sha256": "content hash of the local tracked diff plus untracked path identities",
    "health_state": "closed execution-health state; only explicit verification pass/fail is validated",
    "evidence_source": "live_hook for prospective evidence or transcript_backfill for historical discovery",
    "hook_version": "live hook body version that produced the event",
    "intervention_id": "closed repeated-transition identifier for prospective intervention events",
    "intervention_version": "versioned intervention behavior assigned to the episode",
    "eligibility_rule_version": "version of the pre-intervention eligibility rule",
    "assignment": "control, exposed, or shadow assignment made before intervention delivery",
    "assignment_cluster": "session-level cluster identifier used to prevent cross-arm contamination",
    "eligible_transition": "whether the pre-registered transition eligibility rule passed",
    "delivered": "whether the assigned intervention was actually surfaced",
    "adopted": "whether the agent used the surfaced intervention",
    "downstream_transition_count": "eligible repeated transitions observed after assignment",
}

_INTERVENTION_ASSIGNMENTS = frozenset({"control", "exposed", "shadow"})
_INTERVENTION_EVENT_TYPES = frozenset({"intervention_assignment", "intervention_observation"})


def _parse_ts(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    rows: list[dict[str, Any]] = []
    invalid = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line_number, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if isinstance(value, dict):
                value = dict(value)
                value["__source_line"] = line_number
                rows.append(value)
            else:
                invalid += 1
    return rows, invalid


def _canonical_hash(prefix: str, value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return prefix + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


def _open_ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    prior_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if prior_version and prior_version < LEDGER_SCHEMA_VERSION:
        conn.executescript(
            """
            DROP TABLE IF EXISTS episode_events;
            DROP TABLE IF EXISTS compile_records;
            DROP TABLE IF EXISTS ledger_meta;
            """
        )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS episode_events (
            event_id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            ts TEXT NOT NULL,
            session_id TEXT NOT NULL DEFAULT '',
            turn_seq INTEGER,
            terminal INTEGER NOT NULL DEFAULT 0,
            outcome TEXT NOT NULL DEFAULT '',
            duration_ms INTEGER,
            changed_files INTEGER,
            diff_sha256 TEXT NOT NULL DEFAULT '',
            health_state TEXT NOT NULL DEFAULT 'unknown',
            evidence_source TEXT NOT NULL DEFAULT 'live_hook',
            hook_version INTEGER,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_episode_events_episode
            ON episode_events(episode_id, ts);
        CREATE INDEX IF NOT EXISTS idx_episode_events_type
            ON episode_events(event_type, ts);

        CREATE TABLE IF NOT EXISTS compile_records (
            record_id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL DEFAULT '',
            ts TEXT NOT NULL,
            task_hash TEXT NOT NULL DEFAULT '',
            task_prefix TEXT NOT NULL DEFAULT '',
            procedure TEXT NOT NULL DEFAULT '',
            classifier_conf REAL,
            art_label TEXT NOT NULL DEFAULT '',
            envelope_bytes INTEGER,
            compile_ms REAL,
            injection_advice TEXT NOT NULL DEFAULT '',
            cache_hit INTEGER NOT NULL DEFAULT 0,
            agent_mode TEXT NOT NULL DEFAULT 'unknown',
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_compile_records_episode
            ON compile_records(episode_id, ts);
        CREATE INDEX IF NOT EXISTS idx_compile_records_task
            ON compile_records(task_hash, ts);

        CREATE TABLE IF NOT EXISTS ledger_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.execute(f"PRAGMA user_version={LEDGER_SCHEMA_VERSION}")
    return conn


def _ingest_events(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    rejected = 0
    for row in rows:
        episode_id = str(row.get("episode_id") or "").strip()
        event_type = str(row.get("event_type") or "").strip()
        ts = str(row.get("ts") or "").strip()
        if not episode_id or not event_type or _parse_ts(ts) is None:
            rejected += 1
            continue
        event_id = str(row.get("event_id") or "").strip() or _canonical_hash("evt_", row)
        payload = {key: value for key, value in row.items() if key != "__source_line"}
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO episode_events (
                event_id, episode_id, event_type, ts, session_id, turn_seq,
                terminal, outcome, duration_ms, changed_files, diff_sha256,
                health_state, evidence_source, hook_version, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                episode_id,
                event_type,
                ts,
                str(row.get("session_id") or ""),
                _int_or_none(row.get("turn_seq")),
                int(bool(row.get("terminal"))),
                str(row.get("outcome") or ""),
                _int_or_none(row.get("duration_ms")),
                _int_or_none(row.get("changed_files")),
                str(row.get("diff_sha256") or ""),
                str(row.get("health_state") or "unknown"),
                str(row.get("evidence_source") or "live_hook"),
                _int_or_none(row.get("hook_version")),
                json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
            ),
        )
        inserted += int(cur.rowcount > 0)
    return inserted, rejected


def _ingest_compiles(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    rejected = 0
    for row in rows:
        ts = str(row.get("ts") or "").strip()
        if _parse_ts(ts) is None:
            rejected += 1
            continue
        source_line = _int_or_none(row.get("__source_line"))
        payload = {key: value for key, value in row.items() if key != "__source_line"}
        record_id = _canonical_hash("cmp_", {"source_line": source_line, "payload": payload})
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO compile_records (
                record_id, episode_id, ts, task_hash, task_prefix, procedure,
                classifier_conf, art_label, envelope_bytes, compile_ms,
                injection_advice, cache_hit, agent_mode, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                str(row.get("episode_id") or ""),
                ts,
                str(row.get("task_hash") or ""),
                str(row.get("task_prefix") or ""),
                str(row.get("procedure") or ""),
                _float_or_none(row.get("classifier_conf")),
                str(row.get("art_label") or ""),
                _int_or_none(row.get("envelope_bytes")),
                _float_or_none(row.get("compile_ms")),
                str(row.get("injection_advice") or ""),
                int(row.get("cache_hit") is True),
                str(row.get("agent_mode") or "unknown"),
                json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
            ),
        )
        inserted += int(cur.rowcount > 0)
    return inserted, rejected


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def materialize_ledger(root: str | Path) -> dict[str, Any]:
    root_path = Path(root).resolve()
    roam_dir = root_path / ".roam"
    live_event_rows, invalid_live_events = _read_jsonl(roam_dir / EVENT_LOG_NAME)
    transcript_event_rows, invalid_transcript_events = _read_jsonl(roam_dir / TRANSCRIPT_EVENT_LOG_NAME)
    for row in live_event_rows:
        row.setdefault("evidence_source", "live_hook")
    for row in transcript_event_rows:
        row["evidence_source"] = "transcript_backfill"
    event_rows = [*live_event_rows, *transcript_event_rows]
    invalid_events = invalid_live_events + invalid_transcript_events
    compile_rows, invalid_compiles = _read_jsonl(roam_dir / COMPILE_LOG_NAME)
    conn = _open_ledger(roam_dir / LEDGER_DB_NAME)
    try:
        # transcript-episodes.jsonl is a replaceable derived snapshot, unlike
        # the append-only live hook stream. Reconcile it exactly on every run.
        conn.execute("DELETE FROM episode_events WHERE evidence_source='transcript_backfill'")
        event_inserted, event_rejected = _ingest_events(conn, event_rows)
        compile_inserted, compile_rejected = _ingest_compiles(conn, compile_rows)
        conn.execute(
            "INSERT OR REPLACE INTO ledger_meta(key, value) VALUES ('last_materialized_at', ?)",
            (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),),
        )
        conn.commit()
        totals = {
            "event_records": conn.execute("SELECT COUNT(*) FROM episode_events").fetchone()[0],
            "compile_records": conn.execute("SELECT COUNT(*) FROM compile_records").fetchone()[0],
        }
    finally:
        conn.close()
    return {
        "database": str(roam_dir / LEDGER_DB_NAME),
        "event_rows_read": len(event_rows),
        "live_event_rows_read": len(live_event_rows),
        "transcript_event_rows_read": len(transcript_event_rows),
        "compile_rows_read": len(compile_rows),
        "event_rows_inserted": event_inserted,
        "compile_rows_inserted": compile_inserted,
        "invalid_event_rows": invalid_events + event_rejected,
        "invalid_compile_rows": invalid_compiles + compile_rejected,
        **totals,
    }


def _known_answer_canaries() -> dict[str, Any]:
    """Run tiny deterministic fixtures through the classification assumptions."""
    fixtures = [
        {
            "name": "clean_no_edit_terminal",
            "events": [
                {"event_type": "prompt_submitted", "terminal": False},
                {"event_type": "stop_decision", "terminal": True, "outcome": "no_edit"},
            ],
            "expected": ("no_edit", True),
        },
        {
            "name": "blocked_then_completed",
            "events": [
                {"event_type": "prompt_submitted", "terminal": False},
                {"event_type": "stop_decision", "terminal": False, "outcome": "verification_blocked"},
                {"event_type": "stop_continuation", "terminal": True, "outcome": "continued_after_block"},
            ],
            "expected": ("continued_after_block", True),
        },
        {
            "name": "degraded_verify_terminal",
            "events": [
                {"event_type": "prompt_submitted", "terminal": False},
                {"event_type": "stop_decision", "terminal": True, "outcome": "verify_unavailable"},
            ],
            "expected": ("verify_unavailable", True),
        },
    ]
    failures: list[str] = []
    for fixture in fixtures:
        terminal = [event for event in fixture["events"] if event.get("terminal")]
        observed = (
            str(terminal[-1].get("outcome") or "") if terminal else "",
            bool(terminal),
        )
        if observed != fixture["expected"]:
            failures.append(fixture["name"])
    return {
        "state": "passed" if not failures else "failed",
        "passed": len(fixtures) - len(failures),
        "total": len(fixtures),
        "failures": failures,
    }


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 1)


def _episode_health_state(
    episode_events: list[dict[str, Any]],
    start: dict[str, Any],
    terminal: dict[str, Any] | None,
) -> str:
    states = [str(row.get("health_state") or "unknown") for row in episode_events]
    if "verification_failed" in states:
        return "verification_failed"
    if "verification_passed" in states:
        return "verification_passed"
    return str((terminal or start).get("health_state") or "unknown")


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _episode_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    events = [dict(row) for row in conn.execute("SELECT * FROM episode_events ORDER BY ts, event_id")]
    compiles = [dict(row) for row in conn.execute("SELECT * FROM compile_records ORDER BY ts, record_id")]
    events_by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    compiles_by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        events_by_episode[row["episode_id"]].append(row)
    for row in compiles:
        if row["episode_id"]:
            compiles_by_episode[row["episode_id"]].append(row)

    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for episode_id, episode_events in events_by_episode.items():
        starts = [row for row in episode_events if row["event_type"] == "prompt_submitted"]
        if not starts:
            continue
        start = starts[0]
        start_payload = _payload(start)
        start_ts = _parse_ts(start["ts"])
        terminals = [row for row in episode_events if row["terminal"]]
        terminal = terminals[-1] if terminals else None
        terminal_payload = _payload(terminal or {})
        age_s = (now - start_ts).total_seconds() if start_ts else None
        eligible = age_s is not None and age_s >= TERMINAL_GRACE_SECONDS
        compile_row = compiles_by_episode.get(episode_id, [None])[-1]
        out.append(
            {
                "episode_id": episode_id,
                "session_id": str(start.get("session_id") or ""),
                "started_at": start["ts"],
                "eligible": eligible,
                "terminal": terminal is not None,
                "outcome": str((terminal or {}).get("outcome") or ""),
                "duration_ms": (terminal or {}).get("duration_ms"),
                "changed_files": (terminal or {}).get("changed_files"),
                "health_state": _episode_health_state(episode_events, start, terminal),
                "evidence_source": str(start.get("evidence_source") or "live_hook"),
                "transcript_source": str(start_payload.get("transcript_source") or ""),
                "hook_version": start.get("hook_version"),
                "compile_expected": bool(start_payload.get("compile_expected", True)),
                "compile_joined": compile_row is not None,
                "project_id": str(start_payload.get("project_id") or ""),
                "project_identity_basis": str(start_payload.get("project_identity_basis") or ""),
                "intent_archetypes": (
                    start_payload.get("intent_archetypes")
                    if isinstance(start_payload.get("intent_archetypes"), list)
                    else []
                ),
                "intent_simhash64": str(start_payload.get("intent_simhash64") or ""),
                "prompt_hmac_sha256": str(start_payload.get("prompt_hmac_sha256") or ""),
                "prompt_tokens_bucket": (_int_or_none(start_payload.get("prompt_tokens_bucket")) or 0),
                "trajectory_fingerprint": str(terminal_payload.get("trajectory_fingerprint") or ""),
                "trajectory_template": str(terminal_payload.get("trajectory_template") or ""),
                "phase_sequence_template": str(terminal_payload.get("phase_sequence_template") or ""),
                "command_sequence_fingerprint": str(terminal_payload.get("command_sequence_fingerprint") or ""),
                "command_sequence_template": str(terminal_payload.get("command_sequence_template") or ""),
                "shell_templates": (
                    terminal_payload.get("shell_templates")
                    if isinstance(terminal_payload.get("shell_templates"), dict)
                    else {}
                ),
                "shell_ngrams": (
                    terminal_payload.get("shell_ngrams")
                    if isinstance(terminal_payload.get("shell_ngrams"), dict)
                    else {}
                ),
                "tool_ngrams": (
                    terminal_payload.get("tool_ngrams") if isinstance(terminal_payload.get("tool_ngrams"), dict) else {}
                ),
                "phase_ngrams": (
                    terminal_payload.get("phase_ngrams")
                    if isinstance(terminal_payload.get("phase_ngrams"), dict)
                    else {}
                ),
                "friction": (
                    terminal_payload.get("friction") if isinstance(terminal_payload.get("friction"), dict) else {}
                ),
                "shell_template_outcomes": (
                    terminal_payload.get("shell_template_outcomes")
                    if isinstance(terminal_payload.get("shell_template_outcomes"), dict)
                    else {}
                ),
                "phase_outcomes": (
                    terminal_payload.get("phase_outcomes")
                    if isinstance(terminal_payload.get("phase_outcomes"), dict)
                    else {}
                ),
                "command_class_outcomes": (
                    terminal_payload.get("command_class_outcomes")
                    if isinstance(terminal_payload.get("command_class_outcomes"), dict)
                    else {}
                ),
                "tool_calls": _int_or_none(terminal_payload.get("tool_calls")) or 0,
                "tool_errors": _int_or_none(terminal_payload.get("tool_errors")) or 0,
                "tool_result_bytes_bucket": (_int_or_none(terminal_payload.get("tool_result_bytes_bucket")) or 0),
                "assistant_messages": (_int_or_none(terminal_payload.get("assistant_messages")) or 0),
                "time_to_first_tool_ms": _int_or_none(terminal_payload.get("time_to_first_tool_ms")),
                "time_to_first_edit_ms": _int_or_none(terminal_payload.get("time_to_first_edit_ms")),
                "edit_actions": _int_or_none(terminal_payload.get("edit_actions")) or 0,
                "verification_attempts": (_int_or_none(terminal_payload.get("verification_attempts")) or 0),
                "verification_failures": (_int_or_none(terminal_payload.get("verification_failures")) or 0),
                "input_tokens": _int_or_none(terminal_payload.get("input_tokens")) or 0,
                "output_tokens": _int_or_none(terminal_payload.get("output_tokens")) or 0,
                "cached_input_tokens": (_int_or_none(terminal_payload.get("cached_input_tokens")) or 0),
                "cache_creation_tokens": (_int_or_none(terminal_payload.get("cache_creation_tokens")) or 0),
                "reasoning_output_tokens": (_int_or_none(terminal_payload.get("reasoning_output_tokens")) or 0),
                "correction_after": bool(terminal_payload.get("correction_after")),
                "task_hash": str((compile_row or {}).get("task_hash") or ""),
                "task_prefix": str((compile_row or {}).get("task_prefix") or ""),
                "procedure": str((compile_row or {}).get("procedure") or ""),
                "art_label": str((compile_row or {}).get("art_label") or ""),
                "classifier_conf": (compile_row or {}).get("classifier_conf"),
            }
        )
    return out


def _intervention_evidence(conn: sqlite3.Connection) -> dict[str, Any]:
    """Summarize pre-assignment/post-observation joins without estimating effects."""
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT event_id, episode_id, event_type, ts, session_id, payload_json
            FROM episode_events
            WHERE event_type IN ('intervention_assignment', 'intervention_observation')
            ORDER BY ts, event_id
            """
        )
    ]
    terminal_times = {
        str(row[0]): _parse_ts(row[1])
        for row in conn.execute(
            """
            SELECT episode_id, MAX(ts)
            FROM episode_events
            WHERE terminal=1
            GROUP BY episode_id
            """
        )
    }
    assignments: dict[tuple[str, str, str], dict[str, Any]] = {}
    observations: dict[tuple[str, str, str], dict[str, Any]] = {}
    invalid_rows = 0
    duplicate_rows = 0
    for row in rows:
        payload = _payload(row)
        intervention_id = str(payload.get("intervention_id") or "").strip()
        version = str(payload.get("intervention_version") or "").strip()
        key = (str(row.get("episode_id") or ""), intervention_id, version)
        if not all(key):
            invalid_rows += 1
            continue
        if row["event_type"] == "intervention_assignment":
            assignment = str(payload.get("assignment") or "")
            rule_version = str(payload.get("eligibility_rule_version") or "")
            assignment_cluster = str(payload.get("assignment_cluster") or "")
            eligible_transition = payload.get("eligible_transition")
            if (
                assignment not in _INTERVENTION_ASSIGNMENTS
                or not rule_version
                or not assignment_cluster
                or assignment_cluster != str(row.get("session_id") or "")
                or eligible_transition is not True
            ):
                invalid_rows += 1
                continue
            if key in assignments:
                duplicate_rows += 1
            assignments[key] = {
                **payload,
                "session_id": str(row.get("session_id") or ""),
                "assigned_at": str(row.get("ts") or ""),
            }
            continue
        transition_count = _int_or_none(payload.get("downstream_transition_count"))
        if transition_count is None or transition_count < 0:
            invalid_rows += 1
            continue
        if key in observations:
            duplicate_rows += 1
        observations[key] = {
            **payload,
            "observed_at": str(row.get("ts") or ""),
        }

    grouped: dict[tuple[str, str], list[tuple[tuple[str, str, str], dict[str, Any]]]] = defaultdict(list)
    for key, assignment in assignments.items():
        grouped[(key[1], key[2])].append((key, assignment))

    experiments = []
    for (intervention_id, version), assigned_rows in grouped.items():
        arm_counts = Counter(str(assignment.get("assignment") or "") for _, assignment in assigned_rows)
        session_arms: dict[str, set[str]] = defaultdict(set)
        joined = 0
        measured = 0
        delivered = 0
        adopted = 0
        ordering_violations = 0
        for key, assignment in assigned_rows:
            session_arms[str(assignment.get("assignment_cluster") or "")].add(str(assignment.get("assignment") or ""))
            observation = observations.get(key)
            assigned_at = _parse_ts(assignment.get("assigned_at"))
            observed_at = _parse_ts((observation or {}).get("observed_at"))
            terminal_at = terminal_times.get(key[0])
            if observation is None or terminal_at is None:
                continue
            if assigned_at is None or observed_at is None or assigned_at >= observed_at or observed_at > terminal_at:
                ordering_violations += 1
                continue
            joined += 1
            measured += int(_int_or_none(observation.get("downstream_transition_count")) is not None)
            delivered += int(observation.get("delivered") is True)
            adopted += int(observation.get("adopted") is True)
        contaminated_sessions = sum(len(arms) > 1 for session, arms in session_arms.items() if session)
        control = arm_counts["control"]
        exposed = arm_counts["exposed"]
        join_coverage = _pct(joined, len(assigned_rows))
        if ordering_violations:
            readiness = "event_ordering_violation"
        elif control < 30 or exposed < 30:
            readiness = "insufficient_sample"
        elif (join_coverage or 0.0) < MIN_COVERAGE_PCT:
            readiness = "incomplete_observation_join"
        elif contaminated_sessions:
            readiness = "assignment_contamination"
        else:
            readiness = "ready_for_preregistered_effect_estimation"
        experiments.append(
            {
                "intervention_id": intervention_id,
                "intervention_version": version,
                "assigned_episodes": len(assigned_rows),
                "assignment_counts": dict(sorted(arm_counts.items())),
                "terminal_observation_joins": joined,
                "observation_join_coverage_pct": join_coverage,
                "transition_measurement_coverage_pct": _pct(measured, len(assigned_rows)),
                "delivery_rate_pct": _pct(delivered, exposed),
                "adoption_rate_pct": _pct(adopted, exposed),
                "contaminated_sessions": contaminated_sessions,
                "event_ordering_violations": ordering_violations,
                "promotion_readiness": readiness,
                "effectiveness_state": "unmeasured",
                "causal_savings_claimed": False,
            }
        )
    experiments.sort(
        key=lambda row: (
            -row["assigned_episodes"],
            row["intervention_id"],
            row["intervention_version"],
        )
    )
    joined_total = sum(row["terminal_observation_joins"] for row in experiments)
    assignment_total = sum(row["assigned_episodes"] for row in experiments)
    ordering_violations = sum(row["event_ordering_violations"] for row in experiments)
    return {
        "summary": {
            "state": "instrumented" if assignment_total else "not_instrumented",
            "assignment_events": assignment_total,
            "terminal_observation_joins": joined_total,
            "observation_join_coverage_pct": _pct(joined_total, assignment_total),
            "invalid_intervention_events": invalid_rows,
            "duplicate_intervention_events": duplicate_rows,
            "event_ordering_violations": ordering_violations,
            "causal_savings_claimed": False,
        },
        "experiments": experiments,
        "event_contract": {
            "required_pair": [
                "intervention_assignment",
                "intervention_observation",
            ],
            "assignment_must_precede_delivery": True,
            "assignment_unit": "session_id",
            "analysis_population": "intent_to_treat",
        },
    }


def _repeat_candidates(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        if episode["task_hash"] and episode["terminal"]:
            grouped[episode["task_hash"]].append(episode)
    candidates: list[dict[str, Any]] = []
    for task_hash, rows in grouped.items():
        if len(rows) < 3:
            continue
        outcomes = Counter(row["outcome"] or "unknown" for row in rows)
        duration_values = [
            int(row["duration_ms"])
            for row in rows
            if isinstance(row.get("duration_ms"), (int, float)) and row["duration_ms"] >= 0
        ]
        health_known = sum(row["health_state"] in VALIDATED_HEALTH_STATES for row in rows)
        candidates.append(
            {
                "task_hash": task_hash,
                "task_prefix": rows[-1]["task_prefix"],
                "procedure": rows[-1]["procedure"],
                "episodes": len(rows),
                "outcomes": dict(outcomes.most_common()),
                "observed_wall_ms": sum(duration_values),
                "health_context_pct": _pct(health_known, len(rows)),
                "evidence_status": (
                    "candidate" if health_known == len(rows) else "candidate_only_health_context_missing"
                ),
            }
        )
    candidates.sort(key=lambda row: (-row["observed_wall_ms"], -row["episodes"], row["task_hash"]))
    return candidates[:20]


def _interpret_historical_pattern(kind: str, pattern: str) -> dict[str, str]:
    lowered = pattern.lower()
    if "roam " in lowered and any(token in lowered for token in (" | python", " | jq", "head -c")):
        return {
            "pattern_family": "roam_projection_postprocessing",
            "candidate_disposition": "projection_gap",
            "priority": "high",
            "automation_hypothesis": (
                "Return the exact compact projection from Roam so agents stop parsing or truncating it in shell"
            ),
            "existing_surface": "roam fetch-handle --jq or a command-native --summary projection",
            "next_experiment": "Add one native projection and measure disappearance of the post-processing shell n-gram",
        }
    if "roam " in lowered and "--help" in lowered:
        return {
            "pattern_family": "command_discoverability",
            "candidate_disposition": "discoverability_gap",
            "priority": "medium",
            "automation_hypothesis": (
                "Compile the relevant command contract before execution so agents stop opening help repeatedly"
            ),
            "existing_surface": "roam explain-command <command>",
            "next_experiment": "Inject the selected command signature and compare follow-up --help calls",
        }
    if "rg " in lowered and "sed -n" in lowered:
        return {
            "pattern_family": "search_then_slice",
            "candidate_disposition": "automation_candidate",
            "priority": "high",
            "automation_hypothesis": (
                "Collapse search plus manual line slicing into one ranked bounded-context result"
            ),
            "existing_surface": "roam grep <pattern> --group-by symbol or roam retrieve <task>",
            "next_experiment": "Route a held-out search cohort through one context-enriched call",
        }
    if lowered.count("sed -n") >= 2 or ("nl -ba" in lowered and "sed -n" in lowered):
        return {
            "pattern_family": "repeated_code_slicing",
            "candidate_disposition": "automation_candidate",
            "priority": "high",
            "automation_hypothesis": (
                "Replace repeated line-window reads with one location-aware structural context call"
            ),
            "existing_surface": "roam at FILE:LINE or roam context --for-file PATH",
            "next_experiment": "Compare repeated slice count after compiling roam at/context into the first turn",
        }
    if "git diff" in lowered and any(token in lowered for token in ("sed -n", "rg ", "pytest", "roam verify")):
        return {
            "pattern_family": "diff_drilldown",
            "candidate_disposition": "automation_candidate",
            "priority": "high",
            "automation_hypothesis": (
                "Bundle changed hunks, structural blast radius, and verification targets into one review envelope"
            ),
            "existing_surface": "roam diff --full and git diff | roam critique",
            "next_experiment": "Compile one diff-review envelope and measure follow-up git/sed/rg calls",
        }
    if "py_compile" in lowered and "pytest" in lowered:
        return {
            "pattern_family": "verification_ladder",
            "candidate_disposition": "automation_candidate",
            "priority": "high",
            "automation_hypothesis": (
                "Turn syntax checking and focused test selection into one deterministic verification recipe"
            ),
            "existing_surface": "roam syntax-check plus roam test-impact",
            "next_experiment": "Emit one executable verification recipe and compare retries and failed checks",
        }
    if "git status" in lowered and kind != "shell_template":
        return {
            "pattern_family": "workspace_orientation",
            "candidate_disposition": "compiler_context_candidate",
            "priority": "medium",
            "automation_hypothesis": (
                "Include the minimal dirty-tree state in compiled context so orientation does not require a shell turn"
            ),
            "existing_surface": "roam brief plus compile envelope facts",
            "next_experiment": "Add a compact dirty-tree fact and measure follow-up git status calls",
        }
    if kind == "tool_ngram" and "edit" in lowered and "shell" in lowered:
        return {
            "pattern_family": "edit_shell_loop",
            "candidate_disposition": "measurement_refinement",
            "priority": "medium",
            "automation_hypothesis": (
                "Distinguish verification shells from exploration shells before proposing loop automation"
            ),
            "existing_surface": "prospective hook verification outcomes",
            "next_experiment": "Join shell command class to live edit outcomes before promoting a recipe",
        }
    if lowered.startswith("roam ") and all(token not in lowered for token in ("=>", "|", "&&", ";")):
        return {
            "pattern_family": "already_compiled_operation",
            "candidate_disposition": "already_boring",
            "priority": "low",
            "automation_hypothesis": "Observe adoption and outcome quality; no new wrapper is justified by frequency alone",
            "existing_surface": pattern,
            "next_experiment": "Compare success and fallback rates before changing the command",
        }
    return {
        "pattern_family": "unclassified_repetition",
        "candidate_disposition": "candidate_only",
        "priority": "medium" if kind in {"shell_ngram", "shell_sequence"} else "low",
        "automation_hypothesis": "Inspect the repeated structure and identify the invariant input/output contract",
        "existing_surface": "",
        "next_experiment": "Label a held-out sample and test one deterministic replacement",
    }


def _historical_candidates(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank repeated transcript patterns without promoting them to savings facts."""
    groups: dict[str, dict[str, list[tuple[dict[str, Any], int]]]] = {
        "shell_ngram": defaultdict(list),
        "shell_sequence": defaultdict(list),
        "tool_ngram": defaultdict(list),
        "shell_template": defaultdict(list),
        "tool_trajectory": defaultdict(list),
    }
    for episode in episodes:
        if not episode["terminal"]:
            continue
        for source_field, kind in (
            ("shell_ngrams", "shell_ngram"),
            ("tool_ngrams", "tool_ngram"),
            ("shell_templates", "shell_template"),
        ):
            for pattern, count in episode.get(source_field, {}).items():
                try:
                    occurrences = int(count)
                except (TypeError, ValueError):
                    continue
                if pattern and occurrences > 0:
                    groups[kind][str(pattern)].append((episode, occurrences))
        trajectory = str(episode.get("trajectory_template") or "")
        if trajectory:
            groups["tool_trajectory"][trajectory].append((episode, 1))
        command_sequence = str(episode.get("command_sequence_template") or "")
        if command_sequence:
            groups["shell_sequence"][command_sequence].append((episode, 1))

    ranked_by_kind: dict[str, list[dict[str, Any]]] = {}
    for kind, patterns in groups.items():
        ranked: list[dict[str, Any]] = []
        for pattern, records in patterns.items():
            if len(records) < 3:
                continue
            if len(pattern) > 260 or pattern.count("<EXEC>") > 2:
                continue
            if kind == "shell_sequence" and "=>" not in pattern and "×" not in pattern:
                continue
            if kind == "tool_ngram":
                families = {part.strip() for part in pattern.split("=>")}
                if len(families) <= 1:
                    continue
            if kind == "tool_trajectory":
                families = {
                    re.sub(r"\*\d+$", "", part.strip())
                    for part in pattern.split(">")
                    if part.strip() and part.strip() != "<MORE>"
                }
                if len(families) <= 1:
                    continue
            rows = [episode for episode, _count in records]
            outcomes = Counter(row["outcome"] or "unknown" for row in rows)
            associated_tokens = sum(normalized_episode_tokens(row) for row in rows)
            ranked.append(
                {
                    "kind": kind,
                    "pattern": pattern,
                    "episodes": len(rows),
                    "occurrences": sum(count for _episode, count in records),
                    "associated_episode_wall_ms": sum(
                        int(row["duration_ms"])
                        for row in rows
                        if isinstance(row.get("duration_ms"), (int, float)) and row["duration_ms"] >= 0
                    ),
                    "associated_tool_calls": sum(int(row.get("tool_calls") or 0) for row in rows),
                    "associated_tokens": associated_tokens,
                    "correction_pct": _pct(
                        sum(bool(row.get("correction_after")) for row in rows),
                        len(rows),
                    ),
                    "outcomes": dict(outcomes.most_common()),
                    "evidence_status": "historical_candidate_only",
                    **_interpret_historical_pattern(kind, pattern),
                }
            )
        ranked.sort(
            key=lambda row: (
                -row["associated_episode_wall_ms"],
                -row["episodes"],
                -row["occurrences"],
                row["pattern"],
            )
        )
        ranked_by_kind[kind] = ranked

    priority = (
        "shell_ngram",
        "shell_sequence",
        "tool_ngram",
        "shell_template",
        "tool_trajectory",
    )
    interleaved: list[dict[str, Any]] = []
    for rank in range(12):
        for kind in priority:
            rows = ranked_by_kind.get(kind, [])
            if rank < len(rows):
                interleaved.append(rows[rank])
    return interleaved


def analyze_ledger(root: str | Path) -> dict[str, Any]:
    materialization = materialize_ledger(root)
    db_path = Path(materialization["database"])
    conn = _open_ledger(db_path)
    try:
        episodes = _episode_rows(conn)
        event_counts = dict(
            conn.execute(
                "SELECT event_type, COUNT(*) AS n FROM episode_events GROUP BY event_type ORDER BY n DESC"
            ).fetchall()
        )
        first_start_row = conn.execute(
            """
            SELECT MIN(ts) FROM episode_events
            WHERE event_type='prompt_submitted' AND evidence_source='live_hook'
            """
        ).fetchone()
        first_start = first_start_row[0] if first_start_row else None
        compile_window = []
        if first_start:
            compile_window = [
                dict(row)
                for row in conn.execute(
                    "SELECT episode_id, agent_mode FROM compile_records WHERE ts >= ?",
                    (first_start,),
                )
            ]
        intervention_evidence = _intervention_evidence(conn)
    finally:
        conn.close()

    canaries = _known_answer_canaries()
    prospective = [episode for episode in episodes if episode["evidence_source"] == "live_hook"]
    historical = [episode for episode in episodes if episode["evidence_source"] == "transcript_backfill"]
    eligible = [episode for episode in prospective if episode["eligible"]]
    terminal = [episode for episode in eligible if episode["terminal"]]
    compile_expected = [episode for episode in eligible if episode["compile_expected"]]
    compile_joined = [episode for episode in compile_expected if episode["compile_joined"]]
    fully_joined = [
        episode
        for episode in eligible
        if episode["terminal"] and (episode["compile_joined"] or not episode["compile_expected"])
    ]
    health_expected = [
        episode
        for episode in eligible
        if isinstance(episode.get("changed_files"), int) and episode["changed_files"] > 0
    ]
    health_known = [episode for episode in health_expected if episode["health_state"] in VALIDATED_HEALTH_STATES]
    current_hook = [
        episode
        for episode in eligible
        if isinstance(episode.get("hook_version"), int) and episode["hook_version"] >= MIN_LIVE_HOOK_VERSION
    ]
    production_compiles = [
        row
        for row in compile_window
        if (row.get("agent_mode") or "unknown")
        not in {"bench", "corpus", "trace", "envelope_diff", "compile_cache_build", "test"}
    ]
    identified_compiles = [row for row in production_compiles if row.get("episode_id")]

    coverage = {
        "prompt_starts": len(episodes),
        "prospective_prompt_starts": len(prospective),
        "historical_prompt_starts": len(historical),
        "eligible_prompt_starts": len(eligible),
        "terminal_outcomes": len(terminal),
        "compile_expected_episodes": len(compile_expected),
        "compile_joined_episodes": len(compile_joined),
        "fully_joined_episodes": len(fully_joined),
        "health_context_episodes": len(health_known),
        "health_expected_episodes": len(health_expected),
        "current_hook_episodes": len(current_hook),
        "terminal_coverage_pct": _pct(len(terminal), len(eligible)),
        "episode_join_coverage_pct": _pct(len(fully_joined), len(eligible)),
        "health_context_coverage_pct": _pct(len(health_known), len(health_expected)),
        "hook_version_coverage_pct": _pct(len(current_hook), len(eligible)),
        "compile_identity_coverage_pct": _pct(len(identified_compiles), len(production_compiles)),
        "terminal_coverage_definition": (
            "eligible prompt starts with a terminal stop event / prompt starts older than the grace window"
        ),
        "episode_join_coverage_definition": (
            "eligible prospective prompt starts with a terminal event and either the expected compile "
            "record or an explicit compile_expected=false skip / eligible prospective prompt starts"
        ),
        "compile_identity_coverage_definition": (
            "production compile rows with episode_id / production compile rows since the first prompt-start event"
        ),
        "health_context_coverage_definition": (
            "eligible edited episodes with explicit verification_passed or verification_failed state / "
            "eligible edited episodes"
        ),
        "hook_version_coverage_definition": (
            f"eligible prospective prompt starts emitted by hook version >= {MIN_LIVE_HOOK_VERSION} / "
            "eligible prospective prompt starts"
        ),
    }

    integrity_clean = materialization["invalid_event_rows"] == 0 and materialization["invalid_compile_rows"] == 0
    measurement_admissible = (
        canaries["state"] == "passed"
        and integrity_clean
        and len(eligible) >= MIN_ADMISSIBLE_EPISODES
        and (coverage["terminal_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
        and (coverage["episode_join_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
        and (coverage["compile_identity_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
        and (coverage["hook_version_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
    )
    policy_admissible = (
        measurement_admissible
        and bool(health_expected)
        and (coverage["health_context_coverage_pct"] or 0) >= MIN_COVERAGE_PCT
    )

    if not episodes:
        state = "not_initialized"
        verdict = "Savings ledger not initialized — install refreshed hooks and complete agent episodes"
    elif not measurement_admissible:
        state = "insufficient_evidence"
        if not integrity_clean:
            verdict = "Savings claims withheld — malformed telemetry rows failed the integrity gate"
        else:
            verdict = (
                f"Savings claims withheld — {len(fully_joined)}/{len(eligible)} eligible prospective "
                "episodes carry compile and terminal records"
            )
    elif not policy_admissible:
        state = "measurement_ready"
        verdict = "Episode joins are measurement-ready; routing savings remain gated on execution-health context"
    else:
        state = "policy_ready"
        verdict = "Episode ledger is admissible for outcome-conditioned savings experiments"

    candidates = _repeat_candidates(eligible) if measurement_admissible else []
    historical_candidates = _historical_candidates(historical)
    procedure_atlas = build_procedure_atlas(historical)
    return {
        "summary": {
            "verdict": verdict,
            "state": state,
            "partial_success": state != "policy_ready",
            "measurement_admissible": measurement_admissible,
            "policy_admissible": policy_admissible,
            "integrity_clean": integrity_clean,
            "north_star": "durable successful outcomes per unit of constrained resource",
        },
        "coverage": coverage,
        "sensor_canaries": canaries,
        "event_distribution": event_counts,
        "outcome_distribution": dict(Counter(ep["outcome"] or "open" for ep in episodes).most_common()),
        "repeat_candidates": candidates,
        "historical_candidates": historical_candidates,
        "procedure_atlas": procedure_atlas,
        "intervention_evidence": intervention_evidence,
        "materialization": materialization,
        "thresholds": {
            "minimum_eligible_episodes": MIN_ADMISSIBLE_EPISODES,
            "minimum_coverage_pct": MIN_COVERAGE_PCT,
            "terminal_grace_seconds": TERMINAL_GRACE_SECONDS,
        },
    }
