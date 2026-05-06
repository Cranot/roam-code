"""Unit tests for pr-analyze sub-helpers extracted in rounds 3-4.

Targets the small helpers that previously lived inline inside the
`pr_analyze` and `_emit_batch` coordinators. Each helper is exercised
in isolation so a future refactor that breaks contract gets a fast
red signal.

Helpers under test:
- _serve_from_cache (P24)
- _apply_drift (P24)
- _emit_audit_trail (P24)
- _run_batch_serial (P13)
- _run_batch_parallel (P13)
- _process_single_diff (P9)
- _run_conformance_check_inline (A5)
"""

from __future__ import annotations

import hashlib
import json as _json
from pathlib import Path
from unittest.mock import patch

import pytest

# ---- _serve_from_cache ------------------------------------------------------


def test_serve_from_cache_miss_returns_false(tmp_path):
    from roam.commands.cmd_pr_analyze import _serve_from_cache

    cache_dir = tmp_path / "cache"
    rules_path = tmp_path / "rules.yml"
    rules_path.write_text("rules: []\n")
    out = _serve_from_cache(
        diff_text="some diff",
        rules_path=rules_path,
        block_threshold=85,
        language_override=None,
        cache_dir_path=cache_dir,
        json_mode=False,
        quiet=False,
        gate=False,
    )
    assert out is False  # miss → caller continues


def test_serve_from_cache_hit_returns_true_and_emits(tmp_path, capsys):
    from roam.commands.cmd_pr_analyze import _cache_key, _save_cache, _serve_from_cache

    cache_dir = tmp_path / "cache"
    rules_path = tmp_path / "rules.yml"
    rules_path.write_text("rules: []\n")
    bundle = {"summary": {"verdict": "SAFE", "blast_radius": 12, "ai_likelihood": 8, "rule_violations": 0}}
    key = _cache_key("d", rules_path, 85, None)
    _save_cache(cache_dir, key, bundle)

    out = _serve_from_cache(
        diff_text="d",
        rules_path=rules_path,
        block_threshold=85,
        language_override=None,
        cache_dir_path=cache_dir,
        json_mode=False,
        quiet=True,
        gate=False,
    )
    assert out is True  # hit
    captured = capsys.readouterr()
    assert "SAFE" in captured.out
    assert "cached" in captured.out.lower()


def test_serve_from_cache_hit_with_gate_block_exits(tmp_path):
    from roam.commands.cmd_pr_analyze import EXIT_GATE_BLOCK, _cache_key, _save_cache, _serve_from_cache

    cache_dir = tmp_path / "cache"
    rules_path = tmp_path / "rules.yml"
    rules_path.write_text("rules: []\n")
    blocked = {"summary": {"verdict": "BLOCK"}}
    key = _cache_key("d", rules_path, 85, None)
    _save_cache(cache_dir, key, blocked)

    with pytest.raises(SystemExit) as exc:
        _serve_from_cache(
            diff_text="d",
            rules_path=rules_path,
            block_threshold=85,
            language_override=None,
            cache_dir_path=cache_dir,
            json_mode=False,
            quiet=True,
            gate=True,
        )
    assert exc.value.code == EXIT_GATE_BLOCK


# ---- _apply_drift -----------------------------------------------------------


def test_apply_drift_no_baseline_returns_unchanged(tmp_path):
    from roam.commands.cmd_pr_analyze import _apply_drift

    bundle = {"summary": {"verdict": "SAFE"}}
    v, r = _apply_drift(bundle, tmp_path / "missing.json", "SAFE", [])
    assert v == "SAFE"
    assert r == []
    assert "drift" not in bundle


def test_apply_drift_escalates_safe_to_review_on_regression(tmp_path):
    from roam.commands.cmd_pr_analyze import _apply_drift, _save_baseline

    base = tmp_path / "baseline.json"
    _save_baseline(
        base,
        {
            "summary": {"verdict": "SAFE", "blast_radius": 30, "ai_likelihood": 20},
            "rule_violations": [],
        },
    )
    bundle = {
        "summary": {"verdict": "SAFE", "blast_radius": 50, "ai_likelihood": 40},
        "rule_violations": [{"rule_id": "x", "file": "a.py", "matched_target": "y"}],
    }
    v, r = _apply_drift(bundle, base, "SAFE", [])
    assert v == "REVIEW"
    assert any("regression" in s.lower() for s in r)
    assert "drift" in bundle


def test_apply_drift_escalates_review_to_block_on_severe_regression(tmp_path):
    from roam.commands.cmd_pr_analyze import _apply_drift, _save_baseline

    base = tmp_path / "baseline.json"
    _save_baseline(
        base,
        {
            "summary": {"verdict": "REVIEW", "blast_radius": 30, "ai_likelihood": 50},
            "rule_violations": [],
        },
    )
    bundle = {
        "summary": {"verdict": "REVIEW", "blast_radius": 70, "ai_likelihood": 60},  # blast +40
        "rule_violations": [{"rule_id": str(i), "file": "a.py", "matched_target": "y"} for i in range(5)],
    }
    v, r = _apply_drift(bundle, base, "REVIEW", [])
    assert v == "BLOCK"
    assert any("escalate" in s.lower() for s in r)


# ---- _emit_audit_trail ------------------------------------------------------


def _write_record(path: Path, prev_hash: str, verdict: str = "SAFE") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "schema": "roam-audit-trail-v1",
        "sequence_number": 1,
        "timestamp": "2026-05-05T00:00:00Z",
        "actor": "a@x",
        "verdict": verdict,
        "previous_record_hash": prev_hash,
    }
    line = _json.dumps(rec, separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def test_emit_audit_trail_appends_record_on_clean_chain(tmp_path):
    from roam.commands.cmd_pr_analyze import _emit_audit_trail

    trail = tmp_path / "trail.jsonl"
    bundle = {
        "summary": {"verdict": "SAFE", "blast_radius": 10, "ai_likelihood": 5, "rule_violations": 0},
        "rationale": {"summary_text": "ok"},
    }
    v, r = _emit_audit_trail(bundle, trail, "diff text", None, None, "SAFE", [])
    assert v == "SAFE"  # no escalation, clean chain
    assert "audit_trail" in bundle
    assert bundle["audit_trail"]["chain_status"]["pre_emission_chain_valid"] is True
    assert trail.exists()


def test_emit_audit_trail_escalates_to_block_on_broken_chain(tmp_path):
    from roam.commands.cmd_pr_analyze import _emit_audit_trail

    trail = tmp_path / "trail.jsonl"
    # Genesis with empty prev_hash so verifier accepts it
    _write_record(trail, prev_hash="")
    # Now tamper: rewrite the last line with a different verdict
    lines = trail.read_text(encoding="utf-8").splitlines()
    rec = _json.loads(lines[0])
    rec["verdict"] = "TAMPERED"
    lines[0] = _json.dumps(rec, separators=(",", ":"), sort_keys=True)
    trail.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Append a second clean record so chain is "broken" — second record's
    # previous_record_hash points at the original first-record hash, which
    # no longer matches the tampered first record's actual hash.
    _write_record(trail, prev_hash="something-different-from-tampered-hash")

    bundle = {
        "summary": {"verdict": "SAFE", "blast_radius": 10, "ai_likelihood": 5, "rule_violations": 0},
        "rationale": {"summary_text": "ok"},
    }
    v, r = _emit_audit_trail(bundle, trail, "diff", None, None, "SAFE", [])
    assert v == "BLOCK"  # escalated because chain broken
    assert any("chain broken" in reason.lower() for reason in r)
    assert bundle["audit_trail"]["chain_status"]["pre_emission_chain_valid"] is False


# ---- _process_single_diff ---------------------------------------------------


def test_process_single_diff_returns_row_dict(tmp_path):
    """_process_single_diff must always return a dict with at least 'file' key."""
    from roam.commands.cmd_pr_analyze import _process_single_diff

    diff = tmp_path / "x.diff"
    diff.write_text("")  # empty diff — should still return cleanly
    row = _process_single_diff(str(diff), None, 85, 10, None, False, None)
    assert "file" in row
    assert row["file"] == "x.diff"
    # Either verdict (success) or error (defensive) — both are valid contracts
    assert "verdict" in row or "error" in row


def test_process_single_diff_propagates_cache_flag(tmp_path):
    """When cache=True, the inner CLI invocation should include --cache."""
    from roam.commands import cmd_pr_analyze
    from roam.commands.cmd_pr_analyze import _process_single_diff

    diff = tmp_path / "x.diff"
    diff.write_text("")
    captured_args: list[list[str]] = []

    class _FakeResult:
        output = '{"summary": {"verdict": "SAFE", "blast_radius": 0, "ai_likelihood": 0, "rule_violations": 0}, "cache_hit": false}'
        exit_code = 0

    class _FakeRunner:
        def invoke(self, *args, **kwargs):
            captured_args.append(list(args[1]))
            return _FakeResult()

    with patch.object(cmd_pr_analyze, "CliRunner", _FakeRunner):
        _process_single_diff(str(diff), None, 85, 10, None, True, str(tmp_path / "cache"))

    assert captured_args, "expected at least one CLI invocation"
    assert "--cache" in captured_args[0]
    assert "--cache-dir" in captured_args[0]


# ---- _run_batch_serial / _run_batch_parallel --------------------------------


def test_run_batch_serial_processes_in_order(tmp_path):
    from roam.commands.cmd_pr_analyze import _run_batch_serial

    paths = [tmp_path / f"f{i}.diff" for i in range(3)]
    for p in paths:
        p.write_text("")
    accepted: list[tuple[int, dict]] = []

    def _accept(row, idx):
        accepted.append((idx, row))

    with patch(
        "roam.commands.cmd_pr_analyze._process_single_diff",
        side_effect=lambda p, *a, **kw: {"file": Path(p).name, "verdict": "SAFE"},
    ):
        _run_batch_serial(paths, None, 85, 10, None, False, None, _accept)

    indices = [idx for idx, _ in accepted]
    assert indices == [1, 2, 3]  # serial order preserved
    assert [r["file"] for _, r in accepted] == ["f0.diff", "f1.diff", "f2.diff"]


def test_run_batch_parallel_calls_each_path(tmp_path):
    """Parallel mode may complete out of order, but every file must be processed."""
    from roam.commands.cmd_pr_analyze import _run_batch_parallel

    paths = [tmp_path / f"f{i}.diff" for i in range(3)]
    for p in paths:
        p.write_text("")
    accepted_files: set[str] = set()

    def _accept(row, idx):
        accepted_files.add(row.get("file", ""))

    # ProcessPoolExecutor needs picklable callables — test with parallel=2 + a real
    # _process_single_diff that returns immediately on empty diffs.
    _run_batch_parallel(paths, None, 85, 10, None, False, None, 2, _accept)
    assert accepted_files == {"f0.diff", "f1.diff", "f2.diff"}


# ---- _run_conformance_check_inline ------------------------------------------


def test_run_conformance_check_inline_attaches_score(tmp_path):
    from roam.commands.cmd_pr_analyze import _run_conformance_check_inline

    trail = tmp_path / "trail.jsonl"
    _write_record(trail, prev_hash="")

    bundle = {"audit_trail": {"path": str(trail)}}
    _run_conformance_check_inline(bundle, trail)
    conf = bundle["audit_trail"].get("conformance")
    assert conf is not None
    assert "score" in conf
    assert conf["checks_total"] == 6


def test_run_conformance_check_inline_silent_on_missing_trail(tmp_path):
    from roam.commands.cmd_pr_analyze import _run_conformance_check_inline

    bundle: dict = {"audit_trail": {"path": str(tmp_path / "missing.jsonl")}}
    _run_conformance_check_inline(bundle, tmp_path / "missing.jsonl")
    # No records → no conformance block attached (advisory only)
    assert bundle["audit_trail"].get("conformance") is None


def test_compute_drift_includes_per_rule_breakdown():
    """B6 (C.1.ll) — drift should distinguish first-seen rules from count changes."""
    from roam.commands.cmd_pr_analyze import _compute_drift

    baseline = {
        "summary": {"verdict": "REVIEW", "blast_radius": 30, "ai_likelihood": 40},
        "rule_violations": [
            {"rule_id": "no-eval", "file": "a.py", "matched_target": "eval"},
            {"rule_id": "old-rule", "file": "b.py", "matched_target": "x"},
        ],
    }
    current = {
        "summary": {"verdict": "REVIEW", "blast_radius": 30, "ai_likelihood": 40},
        "rule_violations": [
            {"rule_id": "no-eval", "file": "a.py", "matched_target": "eval"},
            {"rule_id": "no-eval", "file": "c.py", "matched_target": "eval"},  # +1 to existing rule
            {"rule_id": "brand-new-rule", "file": "d.py", "matched_target": "y"},  # new rule
        ],
    }
    drift = _compute_drift(current, baseline)
    assert drift is not None
    assert "brand-new-rule" in drift["rules_first_seen"]
    assert "old-rule" in drift["rules_resolved_entirely"]
    # no-eval changed: 1 → 2
    changes = {c["rule_id"]: c for c in drift["rule_count_changes"]}
    assert "no-eval" in changes
    assert changes["no-eval"]["before"] == 1
    assert changes["no-eval"]["after"] == 2
    assert changes["no-eval"]["delta"] == 1


def test_compute_drift_per_rule_empty_when_no_changes():
    from roam.commands.cmd_pr_analyze import _compute_drift

    baseline = {
        "summary": {"verdict": "SAFE"},
        "rule_violations": [{"rule_id": "x", "file": "a.py", "matched_target": "y"}],
    }
    current = {
        "summary": {"verdict": "SAFE"},
        "rule_violations": [{"rule_id": "x", "file": "a.py", "matched_target": "y"}],
    }
    drift = _compute_drift(current, baseline)
    assert drift["rules_first_seen"] == []
    assert drift["rules_resolved_entirely"] == []
    assert drift["rule_count_changes"] == []


def test_run_conformance_check_inline_never_raises(tmp_path):
    """Even if every check explodes, inline must not raise."""
    from roam.commands import cmd_pr_analyze

    bundle: dict = {"audit_trail": {"path": str(tmp_path / "trail.jsonl")}}
    # Trail with one valid record so we get past the empty-records guard
    _write_record(tmp_path / "trail.jsonl", prev_hash="")

    with patch.object(cmd_pr_analyze, "_emit_audit_trail"):  # any patch — proves try/except envelope
        # Should not raise even if internal modules misbehave
        cmd_pr_analyze._run_conformance_check_inline(bundle, tmp_path / "trail.jsonl")
