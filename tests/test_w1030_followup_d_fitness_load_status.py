"""W1030-followup-D — surface ``load_yaml_with_warnings`` ``LoadStatus``
on ``cmd_fitness`` envelope.

W1030-followup-A landed on ``cmd_alerts`` + ``cmd_budget``.
W1030-followup-B landed on ``cmd_health --gate``.
W1030-followup-C landed on ``cmd_check_rules``. This wave migrates the
fifth tier-2 caller ``cmd_fitness`` — the architectural fitness gate
loaded from ``.roam/fitness.yaml`` — so agents reading the fitness
envelope can disambiguate:

* ``missing`` — file not on disk (use baseline rules silently).
* ``empty_file`` — zero-byte / whitespace-only file (use baseline rules,
  but flag the empty stub so agents know the user probably meant to
  configure something).
* ``empty_yaml`` — comments-only file (use baseline rules, flag the stub).
* ``parse_error`` / ``wrong_root_type`` / ``read_error`` /
  ``schema_invalid`` — broken config (baseline applied + warning emitted
  by the canonical loader + ``partial_success=True``).
* ``ok`` — file parsed cleanly (today's behaviour, no extra signal).

A single sub-loader feeds the envelope (``_load_rules_with_status``);
the legacy ``_load_rules(...) -> list[dict]`` stays byte-identical so
the W1051 ``warnings_out`` tests AND the external callers
(``cmd_diff._fitness_intersection`` / ``cmd_preflight._collect_fitness_signal``)
keep working.

Cross-links:
- W1030 — ``return_status=True`` substrate + 14-test contract pin.
- W1030-followup-A — sibling waves landed on ``cmd_alerts``, ``cmd_budget``.
- W1030-followup-B — sibling wave landed on ``cmd_health``.
- W1030-followup-C — sibling wave landed on ``cmd_check_rules``.
- W1051 — ``_load_rules`` previous migration to canonical helper.
- W1058 — shared ``_parse_rule_list`` substrate (must not be modified).
- CLAUDE.md "Make fallback chains loud" — the lineage rule.
"""

from __future__ import annotations

import ast as _ast
import json as _json
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
    Indexed in-process so ``roam fitness`` can run.
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


def _write_fitness_yaml(proj: Path, body: str) -> Path:
    """Drop a ``.roam/fitness.yaml`` at the project (raw bytes)."""
    cfg_dir = proj / ".roam"
    cfg_dir.mkdir(exist_ok=True)
    cfg = cfg_dir / "fitness.yaml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def _extract_envelope(output: str) -> dict:
    """Pull the trailing JSON envelope out of mixed stdout.

    ``roam --json fitness`` triggers an in-process auto-index whose
    progress lines may print on stdout before the envelope. The envelope
    is always the last top-level ``{ ... }`` block in the output.
    """
    lines = output.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].startswith("{"):
            return _json.loads("\n".join(lines[idx:]))
    raise AssertionError(f"no JSON envelope found in output:\n{output}")


def _fitness_envelope(proj: Path) -> dict:
    """Invoke ``roam --json fitness`` from ``proj`` and parse envelope.

    ``fitness`` exits non-zero on rule failure but we run on a fresh
    project with no fitness.yaml so the "no rules" path is taken,
    which exits 0.
    """
    runner = CliRunner()
    result = invoke_cli(runner, ["fitness"], cwd=proj, json_mode=True)
    # exit_code 0 = no failures; 1 = rule failure. Both emit envelope.
    assert result.exit_code in (0, 1), f"fitness failed:\n{result.output}"
    return _extract_envelope(result.output)


# ---------------------------------------------------------------------------
# Envelope surfacing: ``roam --json fitness``
# ---------------------------------------------------------------------------


def test_envelope_has_config_state_field(tmp_path: Path) -> None:
    """The fitness envelope ALWAYS carries ``summary.config_state``.

    Pin the contract: even on the happy path (no config -> missing) the
    field is present so downstream consumers don't have to ``.get(...)``
    with a default.
    """
    proj = _make_indexed_project(tmp_path, "fitness_envelope_has_state")
    data = _fitness_envelope(proj)
    summary = data["summary"]
    assert "config_state" in summary, (
        f"summary.config_state field MUST be present on the fitness envelope, got summary: {summary!r}"
    )


def test_missing_config_emits_missing_state(tmp_path: Path) -> None:
    """No ``.roam/fitness.yaml`` on disk -> ``config_state == "missing"``.

    Missing config is the default state — never a degraded run.
    """
    proj = _make_indexed_project(tmp_path, "fitness_missing")
    assert not (proj / ".roam" / "fitness.yaml").exists()
    assert not (proj / ".roam" / "fitness.yml").exists()
    data = _fitness_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "missing", (
        f"Expected config_state='missing' on absent fitness.yaml, got: {summary!r}"
    )
    assert summary.get("partial_success") is not True, (
        f"Missing fitness.yaml is NOT a degradation; partial_success must not be True, got summary: {summary!r}"
    )


def test_empty_file_emits_empty_file_state(tmp_path: Path) -> None:
    """Zero-byte ``.roam/fitness.yaml`` -> ``config_state == "empty_file"``.

    User created the file but didn't write rules. Baseline rules apply
    silently — the envelope flags the empty stub.
    """
    proj = _make_indexed_project(tmp_path, "fitness_empty_file")
    _write_fitness_yaml(proj, "")  # zero-byte stub
    data = _fitness_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "empty_file", (
        f"Expected config_state='empty_file' on zero-byte fitness.yaml, got: {summary!r}"
    )
    # empty_file is not a degradation -> partial_success stays unset.
    assert summary.get("partial_success") is not True


def test_valid_config_emits_ok_state(tmp_path: Path) -> None:
    """Well-formed ``.roam/fitness.yaml`` -> ``config_state == "ok"``.

    Happy path: no warnings, no partial_success.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "fitness_ok")
    _write_fitness_yaml(
        proj,
        'rules:\n  - name: "No cycles"\n    type: metric\n    metric: cycles\n    max: 0\n',
    )
    data = _fitness_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "ok", f"Expected config_state='ok' on valid fitness.yaml, got: {summary!r}"
    assert summary.get("partial_success") is not True
    # No warnings on a well-formed config.
    assert not summary.get("warnings_out")


def test_parse_error_emits_parse_error_state(tmp_path: Path) -> None:
    """Malformed ``.roam/fitness.yaml`` -> ``config_state == "parse_error"`` +
    ``partial_success=True``.

    Verifies W1051 didn't regress: the canonical loader emits a warning,
    the envelope flips partial_success, and ``warnings_out`` carries the
    actionable diagnostic.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "fitness_parse_error")
    # Unmatched bracket -> PyYAML raises -> canonical loader emits warning.
    _write_fitness_yaml(proj, "rules: [unterminated\n")
    data = _fitness_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "parse_error", (
        f"Expected config_state='parse_error' on malformed fitness.yaml, got: {summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"Malformed fitness.yaml MUST flip partial_success=True so agents "
        f"see the config was discarded, got: {summary!r}"
    )
    warnings_field = summary.get("warnings_out", [])
    assert warnings_field, f"Malformed fitness.yaml MUST emit a warning on warnings_out, got: {warnings_field!r}"


def test_partial_success_flips_on_degraded_state(tmp_path: Path) -> None:
    """``partial_success`` is True for every degraded ``config_state``.

    Drives the broadened flip rule (parse_error / wrong_root_type /
    read_error / schema_invalid). Cross-checks the cmd_alerts +
    cmd_budget + cmd_health + cmd_check_rules vocabulary alignment.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "fitness_wrong_root")
    # List root -> wrong_root_type degradation.
    _write_fitness_yaml(proj, "- not\n- a\n- mapping\n")
    data = _fitness_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "wrong_root_type"
    assert summary.get("partial_success") is True, (
        f"wrong_root_type MUST flip partial_success=True, got summary: {summary!r}"
    )


def test_agent_contract_facts_includes_state_disclosure(tmp_path: Path) -> None:
    """``agent_contract.facts`` carries the state-disclosure line.

    LAW 4 anchored on the concrete-noun terminal ``"rules"``. Mirrors
    cmd_check_rules (anchors on "rules"), cmd_alerts (anchors on
    "defaults"), and cmd_health (anchors on "gates") by using the
    command's own subject-noun.
    """
    proj = _make_indexed_project(tmp_path, "fitness_facts_missing")
    data = _fitness_envelope(proj)
    contract = data.get("agent_contract")
    assert contract is not None, f"agent_contract MUST be emitted when config_state is disclosed, got: {data!r}"
    facts = contract.get("facts", [])
    assert any("no .roam/fitness.yaml configured" in f for f in facts), (
        f"missing state MUST disclose 'no .roam/fitness.yaml configured; using baseline rules', got facts: {facts!r}"
    )
    # LAW 4: terminal anchor on concrete noun ("rules").
    state_fact = next(f for f in facts if "no .roam/fitness.yaml configured" in f)
    assert state_fact.rstrip(".").endswith("rules"), (
        f"LAW 4: state fact must terminate on concrete-noun anchor ('rules'), got: {state_fact!r}"
    )


def test_state_field_subset_of_LOAD_STATUSES(tmp_path: Path) -> None:
    """``config_state`` is always a member of :data:`LOAD_STATUSES`. Drift guard.

    Pins the cross-command vocabulary uniformity that W1030-followup-A
    + W1030-followup-B + W1030-followup-C established for cmd_alerts /
    cmd_budget / cmd_health / cmd_check_rules. cmd_fitness joins the
    cohort here.
    """
    from roam.commands._yaml_loader import LOAD_STATUSES

    proj = _make_indexed_project(tmp_path, "fitness_drift_guard")
    data = _fitness_envelope(proj)
    state = data["summary"].get("config_state")
    assert state in LOAD_STATUSES, f"config_state {state!r} must be a member of LOAD_STATUSES {LOAD_STATUSES!r}"


def test_legacy_wrapper_returns_list_only(tmp_path: Path) -> None:
    """Legacy ``_load_rules(...) -> list[dict]`` stays byte-identical.

    Pre-W1030-followup-D callers (cmd_diff._fitness_intersection /
    cmd_preflight._collect_fitness_signal + the W1051 warnings_out tests)
    MUST see a plain ``list`` return — never a tuple.
    """
    from roam.commands.cmd_fitness import _load_rules

    # Missing file -> empty list.
    result = _load_rules(tmp_path)
    assert isinstance(result, list)
    assert result == []

    # Present file with rules -> list[dict].
    pytest.importorskip("yaml")
    (tmp_path / ".roam").mkdir(parents=True, exist_ok=True)
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text(
        'rules:\n  - name: "No cycles"\n    type: metric\n    metric: cycles\n    max: 0\n',
        encoding="utf-8",
    )
    result = _load_rules(tmp_path)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "No cycles"


def test_empty_file_no_spurious_missing_key_warning(tmp_path: Path) -> None:
    """Zero-byte ``.roam/fitness.yaml`` short-circuit: no missing-key warning.

    Mirror of cmd_budget + cmd_health + cmd_check_rules W1030-followup
    behaviour: the empty-stub state is its own disclosure surface
    (``config_state=empty_file``), so the legacy "no `rules:` key"
    warning would just confuse agents.
    """
    proj = _make_indexed_project(tmp_path, "fitness_empty_no_warn")
    _write_fitness_yaml(proj, "")
    data = _fitness_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "empty_file"
    warnings_field = summary.get("warnings_out", []) or []
    assert not any("no `rules:` key" in w for w in warnings_field), (
        f"empty_file short-circuit failed — spurious 'no `rules:` key' warning emitted: {warnings_field!r}"
    )


# ---------------------------------------------------------------------------
# Direct sub-loader: _load_rules_with_status
# ---------------------------------------------------------------------------


def test_with_status_missing(tmp_path: Path) -> None:
    """Absent ``.roam/fitness.yaml`` -> ``([], "missing")``."""
    from roam.commands.cmd_fitness import _load_rules_with_status

    rules, status = _load_rules_with_status(tmp_path)
    assert rules == []
    assert status == "missing"


def test_with_status_empty_file(tmp_path: Path) -> None:
    """Zero-byte ``.roam/fitness.yaml`` -> ``([], "empty_file")``.

    Empty stub MUST short-circuit: no spurious "no `rules:` key"
    warning (mirrors cmd_budget / cmd_check_rules W1030-followup
    behaviour).
    """
    from roam.commands.cmd_fitness import _load_rules_with_status

    (tmp_path / ".roam").mkdir(parents=True, exist_ok=True)
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text("", encoding="utf-8")
    warnings_out: list[str] = []
    rules, status = _load_rules_with_status(tmp_path, warnings_out=warnings_out)
    assert rules == []
    assert status == "empty_file"
    assert warnings_out == [], f"empty fitness.yaml must not emit a warning, got: {warnings_out!r}"


def test_with_status_ok(tmp_path: Path) -> None:
    """Well-formed ``.roam/fitness.yaml`` -> ``(rules, "ok")``."""
    pytest.importorskip("yaml")
    from roam.commands.cmd_fitness import _load_rules_with_status

    (tmp_path / ".roam").mkdir(parents=True, exist_ok=True)
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text(
        'rules:\n  - name: "No cycles"\n    type: metric\n    metric: cycles\n    max: 0\n',
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    rules, status = _load_rules_with_status(tmp_path, warnings_out=warnings_out)
    assert status == "ok"
    assert len(rules) == 1
    assert rules[0]["name"] == "No cycles"
    assert warnings_out == []


def test_with_status_parse_error(tmp_path: Path) -> None:
    """Malformed YAML -> ``([], "parse_error")`` + warning emitted."""
    pytest.importorskip("yaml")
    from roam.commands.cmd_fitness import _load_rules_with_status

    (tmp_path / ".roam").mkdir(parents=True, exist_ok=True)
    p = tmp_path / ".roam" / "fitness.yaml"
    p.write_text("rules: [unterminated\n", encoding="utf-8")
    warnings_out: list[str] = []
    rules, status = _load_rules_with_status(tmp_path, warnings_out=warnings_out)
    assert rules == []
    assert status == "parse_error"
    assert warnings_out, "parse_error MUST emit a canonical-loader warning"


# ---------------------------------------------------------------------------
# W1058 shared substrate untouched
# ---------------------------------------------------------------------------


def test_w1058_shared_parse_rule_list_untouched() -> None:
    """AST-check that the shared ``parse_rule_list`` helper is unmodified.

    W1058 hoisted rule-list parsing to a shared helper used by BOTH
    ``cmd_budget`` and ``cmd_fitness``. Modifying it during the W1030
    arc would risk regressing the W1030-followup-A budget tests as a
    side-effect of the fitness migration. This guard ensures the
    fitness wave touches only its own surface.

    We verify the shared helper still exists in
    ``roam.commands._yaml_loader``, is still a public function (no
    accidental rename), and that both consumer modules
    (cmd_fitness._parse_simple_yaml_dict + cmd_budget._parse_simple_yaml_dict)
    still call it.
    """
    # 1. Helper exists at the canonical name.
    from roam.commands._yaml_loader import parse_rule_list

    assert callable(parse_rule_list), f"parse_rule_list must be a callable; got {parse_rule_list!r}"

    # 2. AST-scan cmd_fitness for the `parse_rule_list` call.
    repo_root_path = repo_root()
    fitness_src = (repo_root_path / "src" / "roam" / "commands" / "cmd_fitness.py").read_text(encoding="utf-8")
    fitness_tree = _ast.parse(fitness_src)
    found_fitness_call = False
    for node in _ast.walk(fitness_tree):
        if isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name) and func.id == "parse_rule_list":
                found_fitness_call = True
                break
            if isinstance(func, _ast.Attribute) and func.attr == "parse_rule_list":
                found_fitness_call = True
                break
    assert found_fitness_call, (
        "cmd_fitness must still call parse_rule_list (W1058 shared helper); "
        "the W1030-followup-D wave should not have inlined the parser."
    )

    # 3. AST-scan cmd_budget for the same call (verifies the helper is
    # still SHARED, not just fitness-local).
    budget_src = (repo_root_path / "src" / "roam" / "commands" / "cmd_budget.py").read_text(encoding="utf-8")
    budget_tree = _ast.parse(budget_src)
    found_budget_call = False
    for node in _ast.walk(budget_tree):
        if isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name) and func.id == "parse_rule_list":
                found_budget_call = True
                break
            if isinstance(func, _ast.Attribute) and func.attr == "parse_rule_list":
                found_budget_call = True
                break
    assert found_budget_call, (
        "cmd_budget must still call parse_rule_list (W1058 shared helper); "
        "the W1030-followup-D wave must not break the cross-module share."
    )
