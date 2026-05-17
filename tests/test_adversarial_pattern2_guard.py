"""Regression test for ``roam adversarial`` Pattern-2 silent-fallback guard.

Pre-fix bug: each ``_check_*`` helper in ``cmd_adversarial`` had a
silent ``except Exception: return challenges`` ladder (graceful
degradation for ``ImportError`` and graph-construction failures). If
a check silently degraded, its empty result was indistinguishable
from a clean check — and when ALL checks degraded the verdict read
``"No architectural challenges found -- changes look clean"``. This
is the canonical Pattern-2 (silent fallback) shape called out in
``CLAUDE.md`` and matches the X4 ``cmd_pr_prep`` / W832
``cmd_critique`` fix templates.

The guard now threads an optional ``status`` dict through each
helper. The orchestrator inspects it for ``errored:`` entries and:

- sets ``summary.partial_success: True``,
- exposes ``summary.failed_checks: [...]`` + ``summary.check_status``,
- refuses to emit a clean verdict — emits a ``PARTIAL`` verdict
  naming the failed checks instead.

This test patches one helper to raise and asserts the guard fires.
It does NOT need an indexed corpus.
"""

from __future__ import annotations

import json as _json

from click.testing import CliRunner

from roam.cli import cli


def test_adversarial_pattern2_partial_when_check_errors(monkeypatch, tmp_path):
    """A silently-erroring check must not yield a clean verdict.

    Before the guard, ``_check_new_cycles`` raising would be caught by
    the helper's internal ``except Exception: return []`` and the
    verdict would read ``"No architectural challenges found -- changes
    look clean"`` despite a check having silently failed. After the
    guard: ``partial_success: true``, ``failed_checks`` names the
    failed check, and the verdict starts with ``PARTIAL``.
    """
    import roam.commands.cmd_adversarial as mod

    # Force changed_files detection to return a non-empty list so the
    # orchestrator drops into the check-running branch instead of the
    # "no changes" early return.
    monkeypatch.setattr(mod, "get_changed_files", lambda *_a, **_kw: ["src/roam/cli.py"])

    # Map the changed path to a synthetic file id so ``file_map`` is
    # non-empty. Returning {} would route into the
    # "Changed files not found in index" early return.
    monkeypatch.setattr(mod, "resolve_changed_to_db", lambda _conn, _changed: {"src/roam/cli.py": 1})

    # ensure_index is a no-op when the workspace already has an index;
    # patch so the test doesn't need one.
    monkeypatch.setattr(mod, "ensure_index", lambda: None)

    # Patch one of the six checks so it records errored status without
    # actually raising (the helper's own ``except`` already caught the
    # raise; we shortcut to the post-condition the guard cares about).
    def fake_new_cycles(_conn, _ids, status=None):
        if status is not None:
            status["new_cycles"] = "errored:build_symbol_graph:RuntimeError"
        return []

    monkeypatch.setattr(mod, "_check_new_cycles", fake_new_cycles)

    # Leave the other 5 checks as-is — they run normally and produce no
    # challenges on the synthetic 1-file changeset (no symbols in the
    # synthetic file id 1 → all checks no-op). End-state: 5 of 6 checks
    # report "skipped:no_changed_symbols" / "ran", 1 reports "errored:".

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "adversarial"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    payload = _json.loads(result.output)
    summary = payload["summary"]

    assert summary["partial_success"] is True
    # ``new_cycles`` is the check we explicitly forced to error. On CI runners
    # without a built index, the W1259 ``symbol_lookup`` substrate also reports
    # ``errored:symbol_lookup:OperationalError`` (no ``symbols`` table). We
    # only assert the forced failure is present — environment-incidental
    # failures may co-occur and don't change the contract.
    assert "new_cycles" in summary["failed_checks"]
    assert summary["state"] == "partial_adversarial"
    # The verdict must signal the cascade so an agent reading only
    # ``verdict`` (LAW 6) sees the partial state, never "clean".
    assert summary["verdict"].startswith("PARTIAL"), summary["verdict"]
    assert "new_cycles" in summary["verdict"]
    assert "clean" not in summary["verdict"].lower() or "cannot certify clean" in summary["verdict"]


def test_adversarial_clean_path_unchanged(monkeypatch, tmp_path):
    """The clean-path verdict is unchanged when every check runs cleanly.

    Guard must not regress the happy path: when every check reports
    ``ran`` or a benign ``skipped:`` (e.g.
    ``skipped:no_changed_symbols``), ``partial_success`` is ``False``
    and the verdict takes the standard
    ``"No architectural challenges found"`` branch.
    """
    import roam.commands.cmd_adversarial as mod

    monkeypatch.setattr(mod, "get_changed_files", lambda *_a, **_kw: ["src/roam/cli.py"])
    monkeypatch.setattr(mod, "resolve_changed_to_db", lambda _conn, _changed: {"src/roam/cli.py": 1})
    monkeypatch.setattr(mod, "ensure_index", lambda: None)
    # W1259 added a symbol_lookup substrate that runs `batched_in` against the
    # `symbols` table. Without a built index (CI default), that query raises
    # `no such table: symbols` and pollutes failed_checks. Stub it so the
    # clean-path test reflects the contract it's testing.
    monkeypatch.setattr(mod, "batched_in", lambda _conn, _sql, _ids: [])

    # Every check returns clean — explicitly mark "ran" in the status
    # so the assertion can pin shape. (The real helpers also do this;
    # we're substituting them only to skip the graph-build cost in CI.)
    def make_clean_check(name):
        def fake(_conn, _ids_or_files, status=None):
            if status is not None:
                status[name] = "ran"
            return []

        return fake

    monkeypatch.setattr(mod, "_check_new_cycles", make_clean_check("new_cycles"))
    monkeypatch.setattr(mod, "_check_layer_violations", make_clean_check("layer_violations"))
    monkeypatch.setattr(mod, "_check_anti_patterns", make_clean_check("anti_patterns"))
    monkeypatch.setattr(mod, "_check_cross_cluster", make_clean_check("cross_cluster"))
    monkeypatch.setattr(mod, "_check_orphaned_symbols", make_clean_check("orphaned_symbols"))
    monkeypatch.setattr(mod, "_check_high_fan_out", make_clean_check("high_fan_out"))

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "adversarial"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    payload = _json.loads(result.output)
    summary = payload["summary"]
    assert summary["partial_success"] is False
    assert summary["failed_checks"] == []
    assert summary["state"] == "all_checks_ran"
    assert "clean" in summary["verdict"]
    assert not summary["verdict"].startswith("PARTIAL")
