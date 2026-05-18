"""W1030-followup-C — surface ``load_yaml_with_warnings`` ``LoadStatus``
on ``cmd_check_rules`` envelope.

W1030-followup-A landed on ``cmd_alerts`` + ``cmd_budget``.
W1030-followup-B landed on ``cmd_health`` ``--gate``. This wave migrates
the tier-2 caller ``cmd_check_rules`` — a governance gate composed into
``for_compliance`` recipes — so agents reading the check-rules envelope
can disambiguate:

* ``missing`` — file not on disk (use baseline rules silently).
* ``empty_file`` — zero-byte / whitespace-only file (use baseline rules,
  but flag the empty stub so agents know the user probably meant to
  configure something).
* ``empty_yaml`` — comments-only file (use baseline rules, flag the stub).
* ``parse_error`` / ``wrong_root_type`` / ``read_error`` /
  ``schema_invalid`` — broken config (baseline applied + warning emitted
  by the canonical loader + ``partial_success=True``).
* ``ok`` — file parsed cleanly (today's behaviour, no extra signal).

Three sub-loaders feed the rollup
(``_load_raw_config_with_status`` / ``_load_user_config_with_status`` /
``_load_config_profile_with_status``); the worst-status wins
(:func:`roam.commands.cmd_check_rules._worst_status`). Today all three
read the SAME ``.roam-rules.yml`` so they converge on a single status;
the rollup is defensive against future divergence.

The legacy wrappers (``_load_raw_config``, ``_load_user_config``,
``_load_config_profile``) stay byte-identical: pre-W1030-followup-C
callers (the W1019d test suite + the SARIF callsite) keep returning
plain ``dict`` / ``list[dict]`` / ``str | None``.

Cross-links:
- W1030 — ``return_status=True`` substrate + 14-test contract pin.
- W1030-followup-A — alerts + budget (sibling waves).
- W1030-followup-B — health (sibling wave).
- W1019d — sub-loader migration to canonical helper.
- CLAUDE.md "Make fallback chains loud" — the lineage rule.
"""

from __future__ import annotations

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
    Indexed in-process so ``roam check-rules`` can run.
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


def _write_rules_yaml(proj: Path, body: str) -> Path:
    """Drop a ``.roam-rules.yml`` at the project root (raw bytes)."""
    cfg = proj / ".roam-rules.yml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def _extract_envelope(output: str) -> dict:
    """Pull the trailing JSON envelope out of mixed stdout.

    ``roam --json check-rules`` triggers an in-process auto-index whose
    progress lines are printed on stdout before the envelope. The
    envelope is always the last top-level ``{ ... }`` block in the output.
    """
    lines = output.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].startswith("{"):
            return _json.loads("\n".join(lines[idx:]))
    raise AssertionError(f"no JSON envelope found in output:\n{output}")


def _check_rules_envelope(proj: Path) -> dict:
    """Invoke ``roam --json check-rules`` from ``proj`` and parse envelope."""
    runner = CliRunner()
    result = invoke_cli(runner, ["check-rules"], cwd=proj, json_mode=True)
    # exit_code 0 = no failing error rules; 1 = error rules failed. Both
    # emit the envelope.
    assert result.exit_code in (0, 1), f"check-rules failed:\n{result.output}"
    return _extract_envelope(result.output)


# ---------------------------------------------------------------------------
# Direct sub-loader: _load_raw_config_with_status
# ---------------------------------------------------------------------------


def test_raw_loader_with_status_missing(tmp_path: Path) -> None:
    """Absent ``.roam-rules.yml`` -> ``({}, "missing")``."""
    from roam.commands.cmd_check_rules import _load_raw_config_with_status

    data, status = _load_raw_config_with_status(str(tmp_path / "missing.yml"))
    assert data == {}
    assert status == "missing"


def test_raw_loader_with_status_none_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``config_path=None`` + no default config on disk -> ``({}, "missing")``."""
    from roam.commands.cmd_check_rules import _load_raw_config_with_status

    monkeypatch.chdir(tmp_path)
    data, status = _load_raw_config_with_status(None)
    assert data == {}
    assert status == "missing"


def test_raw_loader_with_status_empty_file(tmp_path: Path) -> None:
    """Zero-byte ``.roam-rules.yml`` -> ``({}, "empty_file")``.

    Empty stub MUST short-circuit: no spurious "no `rules:` key" /
    "no `profile:` key" warning (mirrors cmd_budget / cmd_health
    W1030-followup behaviour).
    """
    from roam.commands.cmd_check_rules import _load_raw_config_with_status

    p = tmp_path / ".roam-rules.yml"
    p.write_text("", encoding="utf-8")
    warnings_out: list[str] = []
    data, status = _load_raw_config_with_status(str(p), warnings_out=warnings_out)
    assert data == {}
    assert status == "empty_file"
    assert warnings_out == [], f"empty .roam-rules.yml must not emit a warning, got: {warnings_out!r}"


def test_raw_loader_with_status_empty_yaml(tmp_path: Path) -> None:
    """Comments-only ``.roam-rules.yml`` -> ``({}, "empty_yaml")``."""
    pytest.importorskip("yaml")
    from roam.commands.cmd_check_rules import _load_raw_config_with_status

    p = tmp_path / ".roam-rules.yml"
    p.write_text(
        "# stub: author intends to configure rules later\n# next: add `rules:` key\n",
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    data, status = _load_raw_config_with_status(str(p), warnings_out=warnings_out)
    assert data == {}
    assert status == "empty_yaml"
    # Comments-only is NOT a degradation: no warning.
    assert warnings_out == []


def test_raw_loader_with_status_ok(tmp_path: Path) -> None:
    """Well-formed ``.roam-rules.yml`` -> ``(parsed_dict, "ok")``."""
    pytest.importorskip("yaml")
    from roam.commands.cmd_check_rules import _load_raw_config_with_status

    p = tmp_path / ".roam-rules.yml"
    p.write_text(
        "profile: strict-security\nrules:\n  - id: max-fan-out\n    threshold: 5\n",
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    data, status = _load_raw_config_with_status(str(p), warnings_out=warnings_out)
    assert status == "ok"
    assert data.get("profile") == "strict-security"
    assert warnings_out == []


def test_raw_loader_with_status_parse_error(tmp_path: Path) -> None:
    """Malformed YAML -> ``({}, "parse_error")`` + warning emitted."""
    pytest.importorskip("yaml")
    from roam.commands.cmd_check_rules import _load_raw_config_with_status

    p = tmp_path / ".roam-rules.yml"
    # Unmatched bracket forces PyYAML to raise.
    p.write_text("rules: [unterminated\n", encoding="utf-8")
    warnings_out: list[str] = []
    data, status = _load_raw_config_with_status(str(p), warnings_out=warnings_out)
    assert data == {}
    assert status == "parse_error"
    assert warnings_out, "parse_error MUST emit a canonical-loader warning"


def test_raw_loader_with_status_wrong_root_type(tmp_path: Path) -> None:
    """List-at-root -> ``({}, "wrong_root_type")`` + warning."""
    pytest.importorskip("yaml")
    from roam.commands.cmd_check_rules import _load_raw_config_with_status

    p = tmp_path / ".roam-rules.yml"
    p.write_text("- one\n- two\n", encoding="utf-8")
    warnings_out: list[str] = []
    data, status = _load_raw_config_with_status(str(p), warnings_out=warnings_out)
    assert data == {}
    assert status == "wrong_root_type"
    assert warnings_out, "wrong_root_type MUST emit a canonical-loader warning"


# ---------------------------------------------------------------------------
# Legacy wrappers stay byte-identical
# ---------------------------------------------------------------------------


def test_legacy_wrapper_raw_returns_dict_only(tmp_path: Path) -> None:
    """Legacy ``_load_raw_config(...) -> dict`` stays byte-identical.

    Pre-W1030-followup-C callers (the W1019d test suite + every other
    consumer) MUST see a plain ``dict`` return — never a tuple.
    """
    from roam.commands.cmd_check_rules import _load_raw_config

    p = tmp_path / ".roam-rules.yml"
    p.write_text("profile: minimal\n", encoding="utf-8")
    result = _load_raw_config(str(p))
    assert isinstance(result, dict)
    assert result.get("profile") == "minimal"


def test_legacy_wrapper_user_returns_list_only(tmp_path: Path) -> None:
    """Legacy ``_load_user_config(...) -> list[dict]`` stays byte-identical."""
    pytest.importorskip("yaml")
    from roam.commands.cmd_check_rules import _load_user_config

    p = tmp_path / ".roam-rules.yml"
    p.write_text(
        "rules:\n  - id: max-fan-out\n    threshold: 5\n",
        encoding="utf-8",
    )
    result = _load_user_config(str(p))
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].get("id") == "max-fan-out"


def test_legacy_wrapper_profile_returns_str_or_none(tmp_path: Path) -> None:
    """Legacy ``_load_config_profile(...) -> str | None`` stays byte-identical."""
    from roam.commands.cmd_check_rules import _load_config_profile

    # Missing file -> None
    assert _load_config_profile(str(tmp_path / "missing.yml")) is None
    # Present file with profile -> str
    p = tmp_path / ".roam-rules.yml"
    p.write_text("profile: minimal\n", encoding="utf-8")
    result = _load_config_profile(str(p))
    assert result == "minimal"


# ---------------------------------------------------------------------------
# Worst-status rollup
# ---------------------------------------------------------------------------


def test_worst_status_all_ok() -> None:
    """All ``ok`` -> ``ok``."""
    from roam.commands.cmd_check_rules import _worst_status

    assert _worst_status("ok", "ok", "ok") == "ok"


def test_worst_status_empty_args_returns_ok() -> None:
    """No statuses supplied -> defensive default ``ok``."""
    from roam.commands.cmd_check_rules import _worst_status

    assert _worst_status() == "ok"


def test_worst_status_missing_beats_ok() -> None:
    """``missing`` outranks ``ok`` (file absent is still a state)."""
    from roam.commands.cmd_check_rules import _worst_status

    assert _worst_status("ok", "missing", "ok") == "missing"


def test_worst_status_empty_file_beats_missing() -> None:
    """``empty_file`` outranks ``missing`` (stub indicates user intent)."""
    from roam.commands.cmd_check_rules import _worst_status

    assert _worst_status("missing", "empty_file") == "empty_file"


def test_worst_status_parse_error_beats_empty_file() -> None:
    """``parse_error`` outranks ``empty_file`` (broken config is worse)."""
    from roam.commands.cmd_check_rules import _worst_status

    assert _worst_status("empty_file", "parse_error") == "parse_error"


def test_three_loaders_worst_status_wins() -> None:
    """Stub the 3 sub-loaders returning (ok, ok, parse_error) -> ``parse_error``.

    Pins the rollup semantics: any single sub-loader divergence drags the
    rolled-up ``config_state`` down to the worst observed status. Today
    the three sub-loaders read the SAME file so divergence is impossible,
    but the rollup is defensive against a future loader migrating to a
    different file.
    """
    from roam.commands.cmd_check_rules import _worst_status

    # Symmetric: order should not affect the result.
    assert _worst_status("ok", "ok", "parse_error") == "parse_error"
    assert _worst_status("parse_error", "ok", "ok") == "parse_error"
    assert _worst_status("ok", "parse_error", "ok") == "parse_error"


# ---------------------------------------------------------------------------
# Envelope surfacing: ``roam --json check-rules``
# ---------------------------------------------------------------------------


def test_envelope_has_config_state_field(tmp_path: Path) -> None:
    """The check-rules envelope ALWAYS carries ``summary.config_state``.

    Pin the contract: even on the happy path (no config -> missing) the
    field is present so downstream consumers don't have to ``.get(...)``
    with a default.
    """
    proj = _make_indexed_project(tmp_path, "check_rules_envelope_has_state")
    data = _check_rules_envelope(proj)
    summary = data["summary"]
    assert "config_state" in summary, (
        f"summary.config_state field MUST be present on the check-rules envelope, got summary: {summary!r}"
    )


def test_missing_config_emits_missing_state(tmp_path: Path) -> None:
    """No ``.roam-rules.yml`` on disk -> ``config_state == "missing"``.

    Missing config is the default state — never a degraded run.
    """
    proj = _make_indexed_project(tmp_path, "check_rules_missing")
    assert not (proj / ".roam-rules.yml").exists()
    data = _check_rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "missing", (
        f"Expected config_state='missing' on absent .roam-rules.yml, got: {summary!r}"
    )
    assert summary.get("partial_success") is not True, (
        f"Missing .roam-rules.yml is NOT a degradation; partial_success must not be True, got summary: {summary!r}"
    )


def test_empty_file_emits_empty_file_state(tmp_path: Path) -> None:
    """Zero-byte ``.roam-rules.yml`` -> ``config_state == "empty_file"``.

    User created the file but didn't write rules. Baseline rules apply
    silently — the envelope flags the empty stub.
    """
    proj = _make_indexed_project(tmp_path, "check_rules_empty_file")
    _write_rules_yaml(proj, "")  # zero-byte stub
    data = _check_rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "empty_file", (
        f"Expected config_state='empty_file' on zero-byte .roam-rules.yml, got: {summary!r}"
    )
    # empty_file is not a degradation -> partial_success stays unset.
    assert summary.get("partial_success") is not True


def test_valid_config_emits_ok_state(tmp_path: Path) -> None:
    """Well-formed ``.roam-rules.yml`` -> ``config_state == "ok"``.

    Happy path: no warnings, no partial_success.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "check_rules_ok")
    _write_rules_yaml(proj, "rules:\n  - id: max-fan-out\n    threshold: 5\n")
    data = _check_rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "ok", f"Expected config_state='ok' on valid .roam-rules.yml, got: {summary!r}"
    assert summary.get("partial_success") is not True
    # No warnings on a well-formed config.
    assert not data.get("warnings_out")


def test_parse_error_emits_parse_error_state(tmp_path: Path) -> None:
    """Malformed ``.roam-rules.yml`` -> ``config_state == "parse_error"`` +
    ``partial_success=True``.

    Verifies W1019d didn't regress: the canonical loader emits a warning,
    the envelope flips partial_success, and ``warnings_out`` carries the
    actionable diagnostic.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "check_rules_parse_error")
    _write_rules_yaml(proj, "rules: [unterminated\n")
    data = _check_rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "parse_error", (
        f"Expected config_state='parse_error' on malformed .roam-rules.yml, got: {summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"Malformed .roam-rules.yml MUST flip partial_success=True so agents "
        f"see the config was discarded, got: {summary!r}"
    )
    warnings_field = data.get("warnings_out", [])
    assert warnings_field, f"Malformed .roam-rules.yml MUST emit a warning on warnings_out, got: {warnings_field!r}"


def test_partial_success_flips_on_degraded_state(tmp_path: Path) -> None:
    """``partial_success`` is True for every degraded ``config_state``.

    Drives the broadened flip rule (parse_error / wrong_root_type /
    read_error / schema_invalid). Cross-checks the cmd_alerts +
    cmd_budget + cmd_health vocabulary alignment.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "check_rules_wrong_root")
    # List root -> wrong_root_type degradation.
    _write_rules_yaml(proj, "- not\n- a\n- mapping\n")
    data = _check_rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "wrong_root_type"
    assert summary.get("partial_success") is True, (
        f"wrong_root_type MUST flip partial_success=True, got summary: {summary!r}"
    )


def test_agent_contract_facts_includes_state_disclosure(tmp_path: Path) -> None:
    """``agent_contract.facts`` carries the state-disclosure line.

    LAW 4 anchored on the concrete-noun terminal ``"rules"``. Mirrors
    cmd_alerts (anchors on "defaults") and cmd_health (anchors on
    "gates") by using the command's own subject-noun.
    """
    proj = _make_indexed_project(tmp_path, "check_rules_facts_missing")
    data = _check_rules_envelope(proj)
    contract = data.get("agent_contract")
    assert contract is not None, f"agent_contract MUST be emitted when config_state is disclosed, got: {data!r}"
    facts = contract.get("facts", [])
    assert any("no .roam-rules.yml configured" in f for f in facts), (
        f"missing state MUST disclose 'no .roam-rules.yml configured; using baseline rules', got facts: {facts!r}"
    )
    # LAW 4: terminal anchor on concrete noun ("rules").
    state_fact = next(f for f in facts if "no .roam-rules.yml configured" in f)
    assert state_fact.rstrip(".").endswith("rules"), (
        f"LAW 4: state fact must terminate on concrete-noun anchor ('rules'), got: {state_fact!r}"
    )


def test_agent_contract_facts_degraded_state(tmp_path: Path) -> None:
    """Degraded state -> facts disclose rejection + 'using baseline rules'."""
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "check_rules_facts_degraded")
    _write_rules_yaml(proj, "rules: [unterminated\n")
    data = _check_rules_envelope(proj)
    contract = data.get("agent_contract")
    assert contract is not None
    facts = contract.get("facts", [])
    assert any("check-rules config rejected" in f and "parse_error" in f for f in facts), (
        f"parse_error state MUST disclose 'check-rules config rejected "
        f"(parse_error); using baseline rules', got facts: {facts!r}"
    )
    # LAW 4 anchor.
    state_fact = next(f for f in facts if "check-rules config rejected" in f)
    assert state_fact.rstrip(".").endswith("rules"), (
        f"LAW 4: degraded fact must terminate on 'rules' anchor, got: {state_fact!r}"
    )


def test_state_field_subset_of_load_statuses(tmp_path: Path) -> None:
    """``config_state`` is always a member of :data:`LOAD_STATUSES`. Drift guard.

    Pins the cross-command vocabulary uniformity that W1030-followup-A
    + W1030-followup-B established for cmd_alerts / cmd_budget /
    cmd_health. cmd_check_rules joins the cohort here.
    """
    from roam.commands._yaml_loader import LOAD_STATUSES

    proj = _make_indexed_project(tmp_path, "check_rules_drift_guard")
    data = _check_rules_envelope(proj)
    state = data["summary"].get("config_state")
    assert state in LOAD_STATUSES, f"config_state {state!r} must be a member of LOAD_STATUSES {LOAD_STATUSES!r}"


def test_empty_file_no_spurious_missing_key_warning(tmp_path: Path) -> None:
    """Zero-byte ``.roam-rules.yml`` short-circuit: no missing-key warning.

    Mirror of cmd_budget + cmd_health W1030-followup behaviour: the
    empty-stub state is its own disclosure surface
    (``config_state=empty_file``), so the legacy "no `rules:` key" /
    "no `profile:` key" warnings would just confuse agents.

    Today the legacy sub-loader paths already don't emit those warnings
    (W1019d wired structured warnings for malformed shapes but kept
    "missing key" silent), so this test is a regression pin against any
    future migration adding a missing-key warning that fires on the
    empty-stub state.
    """
    proj = _make_indexed_project(tmp_path, "check_rules_empty_no_warn")
    _write_rules_yaml(proj, "")
    data = _check_rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "empty_file"
    warnings_field = data.get("warnings_out", [])
    assert not any("no `rules:` key" in w for w in warnings_field), (
        f"empty_file short-circuit failed — spurious 'no `rules:` key' warning emitted: {warnings_field!r}"
    )
    assert not any("no `profile:` key" in w for w in warnings_field), (
        f"empty_file short-circuit failed — spurious 'no `profile:` key' warning emitted: {warnings_field!r}"
    )


def test_no_state_disclosure_in_facts_on_ok_state(tmp_path: Path) -> None:
    """When ``config_state == "ok"`` the facts do not carry a state line.

    The state-disclosure facts only fire when there's something to
    disclose. A clean ``ok`` run lets the formatter's default
    agent_contract through unchanged (verdict + count facts), but MUST
    NOT inject "no .roam-rules.yml configured" / "check-rules config
    rejected" facts — those are reserved for degraded states.
    """
    pytest.importorskip("yaml")
    proj = _make_indexed_project(tmp_path, "check_rules_ok_no_state_fact")
    _write_rules_yaml(proj, "rules:\n  - id: max-fan-out\n    threshold: 5\n")
    data = _check_rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "ok"
    contract = data.get("agent_contract", {}) or {}
    facts = contract.get("facts", []) or []
    # No state-disclosure fact on the ok path. The formatter may still
    # synthesize verdict + count facts; we only police the W1030-followup-C
    # state-disclosure strings here.
    forbidden_substrings = (
        "no .roam-rules.yml configured",
        "empty .roam-rules.yml stub",
        "comment-only .roam-rules.yml",
        "check-rules config rejected",
    )
    for fact in facts:
        for sub in forbidden_substrings:
            assert sub not in fact, (
                f"ok config_state should NOT emit a state-disclosure fact; found {sub!r} in fact: {fact!r}"
            )
