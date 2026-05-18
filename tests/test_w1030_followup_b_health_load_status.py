"""W1030-followup-B — surface ``load_yaml_with_warnings`` ``LoadStatus``
on ``cmd_health --gate`` envelope.

W1030-followup-A wired ``cmd_alerts`` + ``cmd_budget``. This wave migrates
the tier-2 P1 caller: ``cmd_health._load_gate_config`` — the flagship CI
gate every agent invokes first. The on-disk config is ``.roam-gates.yml``
(NOT ``.roam/health.yaml``); the closed-enum status disambiguates:

* ``missing`` — file not on disk (use baseline gates silently).
* ``empty_file`` — zero-byte / whitespace-only file (use baseline gates,
  but flag the empty stub so agents know the user probably meant to
  configure something).
* ``empty_yaml`` — comments-only file (use baseline gates, flag the stub).
* ``parse_error`` / ``wrong_root_type`` / ``read_error`` /
  ``schema_invalid`` — broken config (baseline applied + warning emitted
  by the canonical loader + ``partial_success=True``).
* ``ok`` — file parsed cleanly (today's behaviour, no extra signal).

The new wrapper is ``_load_gate_config_with_status(...) -> (dict, str)``;
the legacy ``_load_gate_config(...) -> dict`` stays byte-identical so the
W1052 ``warnings_out`` tests AND the SARIF callsite keep working.

Cross-links:
- W1030 — ``return_status=True`` substrate + 14-test contract pin.
- W1030-followup-A — sibling waves landed on ``cmd_alerts``, ``cmd_budget``.
- W1052 — ``_load_gate_config`` previous migration to canonical helper.
- W834 — empty-corpus Pattern-2 silent-Healthy fix for the gate path.
- CLAUDE.md "Make fallback chains loud" — the lineage rule.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root  # noqa: F401 — ensures importable
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
    Indexed in-process so ``roam health --gate`` can run.
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


def _write_gates_yaml(proj: Path, body: str) -> Path:
    """Drop a ``.roam-gates.yml`` at the project root (raw bytes)."""
    cfg = proj / ".roam-gates.yml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def _health_gate_envelope(proj: Path) -> dict:
    """Invoke ``roam --json health --gate`` from ``proj`` and parse envelope.

    Uses a low ``health_min`` (10) so the gate passes on the synthetic
    1-symbol project regardless of computed score; we only care about
    the ``config_state`` disclosure shape, not gate pass/fail.

    NOTE: when ``proj`` already has a malformed / empty config on disk
    the helper installs baseline gates (health_min=60); on the 1-symbol
    fixture the computed health_score is high enough that the baseline
    still passes. Either way the envelope rides on exit 0 unless the
    user crafts a failing config explicitly.
    """
    from click.testing import CliRunner

    runner = CliRunner()
    result = invoke_cli(runner, ["health", "--gate"], cwd=proj, json_mode=True)
    # exit_code 0 = gates passed; 5 = gate failure. Both emit envelope.
    raw = getattr(result, "stdout", None) or result.output
    # On gate failure, output ends with traceback after the JSON; split
    # at the first '}\n' boundary and parse the leading JSON block.
    text = raw.strip()
    if not text.startswith("{"):
        raise AssertionError(f"expected JSON envelope, got:\n{raw}")
    # Walk braces to find the end of the JSON object.
    depth = 0
    end = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise AssertionError(f"unbalanced JSON in output:\n{raw}")
    return _json.loads(text[:end])


# ---------------------------------------------------------------------------
# Direct loader: ``_load_gate_config_with_status`` returns (dict, status)
# ---------------------------------------------------------------------------


def test_load_gate_config_with_status_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent ``.roam-gates.yml`` -> ``(defaults, "missing")``."""
    from roam.commands.cmd_health import _load_gate_config_with_status

    monkeypatch.chdir(tmp_path)
    cfg, status = _load_gate_config_with_status()
    assert cfg == {"health_min": 60}
    assert status == "missing"


def test_load_gate_config_with_status_empty_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero-byte ``.roam-gates.yml`` -> ``(defaults, "empty_file")``.

    Empty stub MUST short-circuit: no spurious "no `health:` key" warning
    (mirrors cmd_budget's W1030-followup-A behaviour).
    """
    from roam.commands.cmd_health import _load_gate_config_with_status

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam-gates.yml").write_text("", encoding="utf-8")
    warnings_out: list[str] = []
    cfg, status = _load_gate_config_with_status(warnings_out=warnings_out)
    assert cfg == {"health_min": 60}
    assert status == "empty_file"
    # W1030-followup-B short-circuit: empty stub MUST NOT emit the
    # "no `health:` key" warning (the user obviously knows the key is
    # missing — they wrote an empty file on purpose).
    assert warnings_out == [], (
        f"empty .roam-gates.yml must not emit spurious missing-key warning, got: {warnings_out!r}"
    )


def test_load_gate_config_with_status_empty_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Comments-only ``.roam-gates.yml`` -> ``(defaults, "empty_yaml")``.

    Distinct from ``empty_file`` (zero bytes): file has bytes but the
    YAML parser returns None. Requires PyYAML to disambiguate.
    """
    pytest.importorskip("yaml")
    from roam.commands.cmd_health import _load_gate_config_with_status

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam-gates.yml").write_text(
        "# stub: author intends to configure gates later\n# next: add `health:` key\n",
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    cfg, status = _load_gate_config_with_status(warnings_out=warnings_out)
    assert cfg == {"health_min": 60}
    assert status == "empty_yaml"
    # Comments-only is NOT a degradation: no warning.
    assert warnings_out == []


def test_load_gate_config_with_status_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Well-formed ``.roam-gates.yml`` -> ``(merged_cfg, "ok")``."""
    pytest.importorskip("yaml")
    from roam.commands.cmd_health import _load_gate_config_with_status

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam-gates.yml").write_text(
        "health:\n  health_min: 75\n  complexity_max: 30\n",
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    cfg, status = _load_gate_config_with_status(warnings_out=warnings_out)
    assert status == "ok"
    assert cfg["health_min"] == 75
    assert cfg["complexity_max"] == 30
    assert warnings_out == []


def test_load_gate_config_with_status_parse_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed YAML -> ``(defaults, "parse_error")`` + warning emitted."""
    pytest.importorskip("yaml")
    from roam.commands.cmd_health import _load_gate_config_with_status

    monkeypatch.chdir(tmp_path)
    # Unclosed brace forces PyYAML to raise.
    (tmp_path / ".roam-gates.yml").write_text("health: { broken\n", encoding="utf-8")
    warnings_out: list[str] = []
    cfg, status = _load_gate_config_with_status(warnings_out=warnings_out)
    assert cfg == {"health_min": 60}
    assert status == "parse_error"
    assert warnings_out, "parse_error MUST emit a canonical-loader warning"


def test_load_gate_config_legacy_wrapper_returns_dict_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy ``_load_gate_config(...) -> dict`` stays byte-identical.

    Pre-W1030-followup-B callers (the SARIF emit path + the existing
    W1052 test suite) MUST see a plain ``dict`` return — never a tuple.
    """
    from roam.commands.cmd_health import _load_gate_config

    monkeypatch.chdir(tmp_path)
    cfg = _load_gate_config()
    assert isinstance(cfg, dict)
    assert "health_min" in cfg
    assert cfg["health_min"] == 60


# ---------------------------------------------------------------------------
# Envelope surfacing: ``roam --json health --gate``
# ---------------------------------------------------------------------------


def test_envelope_has_config_state_field(tmp_path: Path) -> None:
    """The ``--gate`` envelope ALWAYS carries ``summary.config_state``.

    Pin the contract: even on the happy path (no config -> missing) the
    field is present so downstream consumers don't have to ``.get(...)``
    with a default.
    """
    proj = _make_indexed_project(tmp_path, "health_envelope_has_state")
    data = _health_gate_envelope(proj)
    summary = data["summary"]
    assert "config_state" in summary, (
        f"summary.config_state field MUST be present on the gate envelope, got summary: {summary!r}"
    )


def test_envelope_missing_config_emits_missing_state(tmp_path: Path) -> None:
    """No ``.roam-gates.yml`` on disk -> ``config_state == "missing"``.

    Missing config is the default state — never a degraded run.
    """
    proj = _make_indexed_project(tmp_path, "health_missing")
    assert not (proj / ".roam-gates.yml").exists()
    data = _health_gate_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "missing", (
        f"Expected config_state='missing' on absent .roam-gates.yml, got: {summary!r}"
    )
    assert summary.get("partial_success") is not True, (
        f"Missing .roam-gates.yml is NOT a degradation; partial_success must not be True, got summary: {summary!r}"
    )


def test_envelope_empty_file_emits_empty_file_state(tmp_path: Path) -> None:
    """Zero-byte ``.roam-gates.yml`` -> ``config_state == "empty_file"``.

    User created the file but didn't write thresholds. Baseline gates
    apply silently — the envelope flags the empty stub.
    """
    proj = _make_indexed_project(tmp_path, "health_empty_file")
    _write_gates_yaml(proj, "")  # zero-byte stub
    data = _health_gate_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "empty_file", (
        f"Expected config_state='empty_file' on zero-byte .roam-gates.yml, got: {summary!r}"
    )
    assert summary.get("partial_success") is not True


def test_envelope_empty_yaml_emits_empty_yaml_state(tmp_path: Path) -> None:
    """Comments-only ``.roam-gates.yml`` -> ``config_state == "empty_yaml"``.

    Requires PyYAML to disambiguate: the tiny parser collapses
    comments-only to ``{}`` which the canonical loader maps to
    ``parse_error``; PyYAML's ``safe_load`` returns ``None`` and routes
    to ``empty_yaml``.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "health_empty_yaml")
    _write_gates_yaml(proj, "# stub for future thresholds\n# add `health:` key\n")
    data = _health_gate_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "empty_yaml", (
        f"Expected config_state='empty_yaml' on comments-only .roam-gates.yml, got: {summary!r}"
    )
    assert summary.get("partial_success") is not True


def test_envelope_valid_config_emits_ok_state(tmp_path: Path) -> None:
    """Well-formed ``.roam-gates.yml`` -> ``config_state == "ok"``.

    Happy path: no warnings, no partial_success.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "health_ok")
    _write_gates_yaml(proj, "health:\n  health_min: 10\n")
    data = _health_gate_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "ok", f"Expected config_state='ok' on valid .roam-gates.yml, got: {summary!r}"
    assert summary.get("partial_success") is not True
    # No warnings on a well-formed config.
    assert not summary.get("warnings_out")


def test_envelope_parse_error_emits_parse_error_state(tmp_path: Path) -> None:
    """Malformed ``.roam-gates.yml`` -> ``config_state == "parse_error"`` +
    ``partial_success=True``.

    Verifies W1052 didn't regress: the canonical loader emits a warning,
    the envelope flips partial_success, and ``summary.warnings_out``
    carries the actionable diagnostic.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "health_parse_error")
    # Unclosed brace -> PyYAML raises -> canonical loader warns.
    _write_gates_yaml(proj, "health: { broken\n")
    data = _health_gate_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "parse_error", (
        f"Expected config_state='parse_error' on malformed .roam-gates.yml, got: {summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"Malformed .roam-gates.yml MUST flip partial_success=True so agents "
        f"see the config was discarded, got: {summary!r}"
    )
    warnings_field = summary.get("warnings_out", [])
    assert warnings_field, f"Malformed .roam-gates.yml MUST emit a warning on warnings_out, got: {warnings_field!r}"


def test_partial_success_flips_on_degraded_state(tmp_path: Path) -> None:
    """``partial_success`` is True for every degraded ``config_state``.

    Drives the broadened flip rule (parse_error / wrong_root_type /
    read_error / schema_invalid). Cross-checks the cmd_alerts +
    cmd_budget vocabulary alignment.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "health_wrong_root")
    # List root -> wrong_root_type degradation.
    _write_gates_yaml(proj, "- not\n- a\n- mapping\n")
    data = _health_gate_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "wrong_root_type"
    assert summary.get("partial_success") is True, (
        f"wrong_root_type MUST flip partial_success=True, got summary: {summary!r}"
    )


def test_agent_contract_facts_includes_state_disclosure_missing(
    tmp_path: Path,
) -> None:
    """``agent_contract.facts`` carries the state-disclosure line.

    LAW 4 anchored on concrete-noun terminals ("gates"). Mirrors
    cmd_alerts vocabulary (which anchors on "defaults"). Pinned for
    every disclosed state so a future rewording drift fires the test.
    """
    proj = _make_indexed_project(tmp_path, "health_facts_missing")
    data = _health_gate_envelope(proj)
    contract = data.get("agent_contract")
    assert contract is not None, f"agent_contract MUST be emitted when config_state is disclosed, got: {data!r}"
    facts = contract.get("facts", [])
    assert any("no .roam-gates.yml configured" in f for f in facts), (
        f"missing state MUST disclose 'no .roam-gates.yml configured; using baseline gates', got facts: {facts!r}"
    )
    # LAW 4: terminal anchor on concrete noun ("gates").
    state_fact = next(f for f in facts if "no .roam-gates.yml configured" in f)
    assert state_fact.rstrip(".").endswith("gates"), (
        f"LAW 4: state fact must terminate on concrete-noun anchor ('gates'), got: {state_fact!r}"
    )


def test_agent_contract_facts_includes_state_disclosure_degraded(
    tmp_path: Path,
) -> None:
    """Degraded state -> facts disclose rejection + 'using baseline gates'."""
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "health_facts_degraded")
    _write_gates_yaml(proj, "health: { broken\n")
    data = _health_gate_envelope(proj)
    contract = data.get("agent_contract")
    assert contract is not None
    facts = contract.get("facts", [])
    assert any("health config rejected" in f and "parse_error" in f for f in facts), (
        f"parse_error state MUST disclose 'health config rejected "
        f"(parse_error); using baseline gates', got facts: {facts!r}"
    )
    # LAW 4 anchor.
    state_fact = next(f for f in facts if "health config rejected" in f)
    assert state_fact.rstrip(".").endswith("gates"), (
        f"LAW 4: degraded fact must terminate on 'gates' anchor, got: {state_fact!r}"
    )


def test_state_field_subset_of_load_statuses(tmp_path: Path) -> None:
    """``config_state`` is always a member of :data:`LOAD_STATUSES`. Drift guard.

    Pins the cross-command vocabulary uniformity that W1030-followup-A
    established for cmd_alerts + cmd_budget. cmd_health joins the
    cohort here.
    """
    from roam.commands._yaml_loader import LOAD_STATUSES

    proj = _make_indexed_project(tmp_path, "health_drift_guard")
    data = _health_gate_envelope(proj)
    state = data["summary"].get("config_state")
    assert state in LOAD_STATUSES, f"config_state {state!r} must be a member of LOAD_STATUSES {LOAD_STATUSES!r}"


def test_empty_file_no_spurious_missing_key_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero-byte ``.roam-gates.yml`` short-circuit: no missing-key warning.

    Mirror of cmd_budget._load_budgets_with_status W1030-followup-A
    behaviour: the empty-stub state is its own disclosure surface
    (``config_state=empty_file``), so the legacy "no `health:` key"
    warning would just confuse agents reading the warnings_out list.
    """
    from roam.commands.cmd_health import _load_gate_config_with_status

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roam-gates.yml").write_text("", encoding="utf-8")
    warnings_out: list[str] = []
    cfg, status = _load_gate_config_with_status(warnings_out=warnings_out)
    assert status == "empty_file"
    assert cfg == {"health_min": 60}
    # The W1030-followup-B short-circuit MUST suppress the legacy
    # missing-key warning. If this regresses, agents would see a
    # confusing "no `health:` key" warning for a file the user
    # intentionally left blank.
    assert not any("no `health:` key" in w for w in warnings_out), (
        f"empty_file short-circuit failed — spurious missing-key warning emitted: {warnings_out!r}"
    )
