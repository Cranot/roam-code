"""W1030-followup-A — surface ``load_yaml_with_warnings`` ``LoadStatus``
on ``cmd_alerts`` + ``cmd_budget`` envelopes.

Wave1030-followup-A wires the two tier-1 callers W1030 identified to
``return_status=True`` so agents reading the alerts / budget envelopes
can disambiguate:

* ``missing`` — file not on disk (use defaults silently).
* ``empty_file`` — zero-byte / whitespace-only file (use defaults, but
  flag the empty stub so agents know the user probably meant to
  configure something).
* ``empty_yaml`` — comments-only file (use defaults, flag the stub).
* ``parse_error`` / ``wrong_root_type`` / ``read_error`` /
  ``schema_invalid`` — broken config (defaults applied + warning emitted
  by the canonical loader + ``partial_success=True``).
* ``ok`` — file parsed cleanly (today's behaviour, no extra signal).

The canonical helper at
``src/roam/commands/_yaml_loader.py::load_yaml_with_warnings`` returns
the closed-enum status only when ``return_status=True``; before this
wave NO production caller opted in. Wiring the two tier-1 callers
proves the opt-in pattern + lets the next wave migrate the tier-2
callers (cmd_health._load_gate_config, cmd_check_rules, cmd_fitness)
without re-discovering the integration shape.

Cross-links:
- W1030 — ``return_status=True`` substrate + 14-test contract pin.
- W1019c — ``_load_budgets`` migration to ``load_yaml_with_warnings``.
- W972 / W1025 / W918 / W962 — alerts Pattern-2 silent-fallback fixes
  these tests must not regress.
- CLAUDE.md "Make fallback chains loud" — the lineage rule.
"""

from __future__ import annotations

import json as _json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests._helpers.repo_root import repo_root  # noqa: F401  -- ensures importable
from tests.conftest import (  # type: ignore[no-redef]
    git_init,
    index_in_process,
    invoke_cli,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_indexed_project(tmp_path: Path, name: str) -> Path:
    """Create a minimal indexed project under tmp_path/<name>.

    Layout: a single ``main.py`` + ``.gitignore`` that excludes ``.roam/``.
    Indexed in-process so roam can answer downstream commands.
    """
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text(
        'def main():\n    """Entry point."""\n    return 0\n',
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


def _write_alerts_yaml(proj: Path, body: str) -> Path:
    """Drop a ``.roam/alerts.yaml`` (raw bytes — caller decides shape)."""
    cfg_dir = proj / ".roam"
    cfg_dir.mkdir(exist_ok=True)
    cfg = cfg_dir / "alerts.yaml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def _write_budget_yaml(proj: Path, body: str) -> Path:
    """Drop a ``.roam/budget.yaml`` (raw bytes — caller decides shape)."""
    cfg_dir = proj / ".roam"
    cfg_dir.mkdir(exist_ok=True)
    cfg = cfg_dir / "budget.yaml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def _alerts_envelope(proj: Path) -> dict:
    """Invoke ``roam --json alerts`` from ``proj`` and parse the envelope."""
    runner = CliRunner()
    result = invoke_cli(runner, ["alerts"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, f"alerts failed:\n{result.output}"
    raw = getattr(result, "stdout", None) or result.output
    return _json.loads(raw)


def _budget_envelope(proj: Path) -> dict:
    """Invoke ``roam --json budget`` from ``proj`` and parse the envelope.

    ``budget`` exits non-zero on rule failure but we run on a fresh
    project with no snapshot baseline so every rule is SKIP -> exit 0.
    """
    runner = CliRunner()
    result = invoke_cli(runner, ["budget"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, f"budget failed:\n{result.output}"
    raw = getattr(result, "stdout", None) or result.output
    return _json.loads(raw)


# ---------------------------------------------------------------------------
# cmd_alerts: ``config_state`` envelope surfacing
# ---------------------------------------------------------------------------


def test_cmd_alerts_no_config_envelope_state(tmp_path: Path) -> None:
    """No ``.roam/alerts.yaml`` on disk -> ``config_state == "missing"``.

    The alerts command uses baseline defaults silently (no warning) but
    the envelope discloses the absent-config state so agents can tell
    "user has not configured thresholds yet" from "user's config is
    broken".
    """
    proj = _make_indexed_project(tmp_path, "alerts_missing")
    # Sanity: ensure no alerts.yaml exists.
    assert not (proj / ".roam" / "alerts.yaml").exists()

    data = _alerts_envelope(proj)
    summary = data["summary"]

    assert summary.get("config_state") == "missing", (
        f"Expected config_state='missing' on absent alerts.yaml, got: {summary!r}"
    )
    # Absent config is the default state — never a degraded run.
    assert summary.get("partial_success") is not True, (
        f"Missing alerts.yaml is NOT a degradation; partial_success must not be True, got summary: {summary!r}"
    )


def test_cmd_alerts_empty_config_envelope_state(tmp_path: Path) -> None:
    """``.roam/alerts.yaml`` is zero bytes -> ``config_state == "empty_file"``.

    Zero-byte file is a distinct on-disk state from missing: the user
    created the file but didn't write any thresholds. Defaults still
    apply (so the run is not degraded), but the envelope flags the
    empty stub so agents can suggest "did you mean to add thresholds?".
    """
    proj = _make_indexed_project(tmp_path, "alerts_empty_file")
    _write_alerts_yaml(proj, "")  # zero-byte stub

    data = _alerts_envelope(proj)
    summary = data["summary"]

    assert summary.get("config_state") == "empty_file", (
        f"Expected config_state='empty_file' on zero-byte alerts.yaml, got: {summary!r}"
    )
    # Empty file: defaults applied silently; partial_success stays False.
    assert summary.get("partial_success") is not True, (
        f"Empty alerts.yaml stub is NOT a degradation; partial_success must not be True, got summary: {summary!r}"
    )


def test_cmd_alerts_comment_only_config_envelope_state(tmp_path: Path) -> None:
    """Comments-only ``.roam/alerts.yaml`` -> ``config_state == "empty_yaml"``.

    File has bytes on disk but the parser returns None (e.g. user wrote
    documentation comments before configuring any thresholds). Distinct
    from ``empty_file`` (zero bytes) so agents can detect "user added
    documentation but no rules yet".

    The PyYAML path is required to disambiguate ``empty_yaml`` from
    ``empty_file`` — the tiny parser returns ``{}`` for both, which the
    canonical loader collapses to ``parse_error``. PyYAML's ``safe_load``
    returns ``None`` on comments-only input, hitting the ``empty_yaml``
    branch.
    """
    pytest.importorskip("yaml")

    proj = _make_indexed_project(tmp_path, "alerts_comment_only")
    _write_alerts_yaml(
        proj,
        "# this is a stub\n# author intends to configure thresholds later\n",
    )

    data = _alerts_envelope(proj)
    summary = data["summary"]

    assert summary.get("config_state") == "empty_yaml", (
        f"Expected config_state='empty_yaml' on comments-only alerts.yaml, got: {summary!r}"
    )
    assert summary.get("partial_success") is not True, (
        f"Comments-only alerts.yaml is NOT a degradation; partial_success must not be True, got summary: {summary!r}"
    )


def test_cmd_alerts_valid_config_clean_path(tmp_path: Path) -> None:
    """Well-formed ``.roam/alerts.yaml`` -> ``config_state == "ok"``.

    Happy path: the on-disk file parses cleanly + every threshold row is
    well-shaped. No warnings emitted, no partial_success flag.
    """
    pytest.importorskip("yaml")

    proj = _make_indexed_project(tmp_path, "alerts_ok")
    _write_alerts_yaml(
        proj,
        "thresholds:\n  cycles: { op: '>', value: 10, level: warning }\n",
    )

    data = _alerts_envelope(proj)
    summary = data["summary"]

    assert summary.get("config_state") == "ok", f"Expected config_state='ok' on valid alerts.yaml, got: {summary!r}"
    # The envelope still carries warnings_out (Pattern 2 invariant) but
    # the list is empty for a well-formed config.
    warnings_field = data.get("warnings_out", [])
    assert warnings_field == [], f"Well-formed alerts.yaml must emit no warnings, got: {warnings_field!r}"


def test_cmd_alerts_malformed_yaml_partial_success(tmp_path: Path) -> None:
    """Malformed YAML -> ``config_state == "parse_error"`` + partial_success.

    Broken file: defaults apply (so the run produces alerts) but the
    envelope flags the discard via ``partial_success=True`` AND the
    canonical loader's diagnostic surfaces on ``warnings_out``.
    """
    pytest.importorskip("yaml")

    proj = _make_indexed_project(tmp_path, "alerts_parse_error")
    # Unclosed brace -> PyYAML raises -> canonical loader emits warning.
    _write_alerts_yaml(proj, "thresholds: { broken\n")

    data = _alerts_envelope(proj)
    summary = data["summary"]

    assert summary.get("config_state") == "parse_error", (
        f"Expected config_state='parse_error' on malformed YAML, got: {summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"Malformed alerts.yaml MUST flip partial_success=True so agents see the config was discarded, got: {summary!r}"
    )
    warnings_field = data.get("warnings_out", [])
    assert warnings_field, f"Malformed alerts.yaml MUST emit a warning on warnings_out, got: {warnings_field!r}"


# ---------------------------------------------------------------------------
# cmd_budget: ``config_state`` envelope surfacing
# ---------------------------------------------------------------------------


def test_cmd_budget_no_config_envelope_state(tmp_path: Path) -> None:
    """No ``.roam/budget.yaml`` on disk -> ``config_state == "missing"``.

    The budget command uses default budget rules silently (no warning)
    but the envelope discloses the absent-config state.
    """
    proj = _make_indexed_project(tmp_path, "budget_missing")
    assert not (proj / ".roam" / "budget.yaml").exists()
    assert not (proj / ".roam" / "budget.yml").exists()

    data = _budget_envelope(proj)
    summary = data["summary"]

    assert summary.get("config_state") == "missing", (
        f"Expected config_state='missing' on absent budget.yaml, got: {summary!r}"
    )
    assert summary.get("partial_success") is not True, (
        f"Missing budget.yaml is NOT a degradation; partial_success must not be True, got summary: {summary!r}"
    )


def test_cmd_budget_empty_file_envelope_state(tmp_path: Path) -> None:
    """Zero-byte ``.roam/budget.yaml`` -> ``config_state == "empty_file"``.

    User created the file but didn't write any rules. Defaults apply
    silently — the envelope flags the empty stub.
    """
    proj = _make_indexed_project(tmp_path, "budget_empty_file")
    _write_budget_yaml(proj, "")  # zero-byte stub

    data = _budget_envelope(proj)
    summary = data["summary"]

    assert summary.get("config_state") == "empty_file", (
        f"Expected config_state='empty_file' on zero-byte budget.yaml, got: {summary!r}"
    )
    assert summary.get("partial_success") is not True, (
        f"Empty budget.yaml stub is NOT a degradation; partial_success must not be True, got summary: {summary!r}"
    )


def test_cmd_budget_valid_config_clean_path(tmp_path: Path) -> None:
    """Well-formed ``.roam/budget.yaml`` -> ``config_state == "ok"``.

    Happy path: file parses cleanly + every rule is well-shaped. No
    warnings, no partial_success.
    """
    pytest.importorskip("yaml")

    proj = _make_indexed_project(tmp_path, "budget_ok")
    _write_budget_yaml(
        proj,
        'version: "1"\nbudgets:\n  - name: "Health score floor"\n    metric: health_score\n    max_decrease: 5\n',
    )

    data = _budget_envelope(proj)
    summary = data["summary"]

    assert summary.get("config_state") == "ok", f"Expected config_state='ok' on valid budget.yaml, got: {summary!r}"
    # warnings_out absent or empty -> well-formed config.
    assert not summary.get("warnings_out"), f"Well-formed budget.yaml must emit no warnings, got: {summary!r}"


def test_cmd_budget_malformed_yaml_partial_success(tmp_path: Path) -> None:
    """Malformed ``.roam/budget.yaml`` -> ``config_state == "parse_error"`` +
    ``partial_success=True``.

    Verifies W1025 / W972 / W1019c didn't regress: the canonical loader
    emits a warning, the envelope flips partial_success, and the
    summary's ``warnings_out`` field carries the actionable diagnostic.
    """
    pytest.importorskip("yaml")

    proj = _make_indexed_project(tmp_path, "budget_parse_error")
    # Unclosed brace -> PyYAML raises -> canonical loader emits warning.
    _write_budget_yaml(proj, "budgets: { broken\n")

    data = _budget_envelope(proj)
    summary = data["summary"]

    assert summary.get("config_state") == "parse_error", (
        f"Expected config_state='parse_error' on malformed budget.yaml, got: {summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"Malformed budget.yaml MUST flip partial_success=True so agents see the config was discarded, got: {summary!r}"
    )
    warnings_field = summary.get("warnings_out", [])
    assert warnings_field, f"Malformed budget.yaml MUST emit a warning on warnings_out, got: {warnings_field!r}"


# ---------------------------------------------------------------------------
# Closed-enum coverage: ``config_state`` always one of LOAD_STATUSES
# ---------------------------------------------------------------------------


def test_alerts_config_state_is_closed_enum_member(tmp_path: Path) -> None:
    """The ``config_state`` field on the alerts envelope is always a
    member of :data:`LOAD_STATUSES`. Drift guard.
    """
    from roam.commands._yaml_loader import LOAD_STATUSES

    proj = _make_indexed_project(tmp_path, "alerts_enum")
    data = _alerts_envelope(proj)

    state = data["summary"].get("config_state")
    assert state in LOAD_STATUSES, f"config_state {state!r} must be a member of LOAD_STATUSES {LOAD_STATUSES!r}"


def test_budget_config_state_is_closed_enum_member(tmp_path: Path) -> None:
    """The ``config_state`` field on the budget envelope is always a
    member of :data:`LOAD_STATUSES`. Drift guard.
    """
    from roam.commands._yaml_loader import LOAD_STATUSES

    proj = _make_indexed_project(tmp_path, "budget_enum")
    data = _budget_envelope(proj)

    state = data["summary"].get("config_state")
    assert state in LOAD_STATUSES, f"config_state {state!r} must be a member of LOAD_STATUSES {LOAD_STATUSES!r}"


# Silence unused-import warnings for fixtures.
_ = os
_ = repo_root
