"""Cross-cutting drift guard: ``summary.config_state`` uniformity.

W1030-followup-A wired ``cmd_alerts`` + ``cmd_budget``.
W1030-followup-B wired ``cmd_health --gate``.
W1030-followup-C wired ``cmd_check_rules``.
W1030-followup-D wired ``cmd_fitness``.
W1030-followup-F wired ``cmd_rules`` (the directory-of-files variant —
``rules/engine.py`` exposes a per-file ``LoadStatus`` rollup so the
``cmd_rules`` envelope can disambiguate missing / empty / parse_error / ok).

Six emitters now share the same disclosure field
(``summary.config_state``) and value space (the closed-enum
:data:`roam.commands._yaml_loader.LOAD_STATUSES`). This file pins the
uniformity so a future rename or vocabulary drift fires in CI before it
diverges:

* The FIELD NAME stays ``config_state`` across all four envelopes (not
  ``loader_state`` / ``config_status`` / ``yaml_state`` / etc.).
* The VALUE SPACE stays a subset of ``LOAD_STATUSES`` — no command
  invents its own status alphabet.

The fixtures are deliberately minimal: each test just invokes the
command in ``--json`` mode on an empty / minimal project and asserts on
the envelope-level vocabulary. The per-command behavioral tests
(``test_w1030_followup_a_*`` / ``test_w1030_followup_b_*`` /
``test_w1030_followup_c_*`` / ``test_w1030_followup_d_*``) cover the
deeper semantics (empty-file short-circuit, partial_success flip,
facts wording); this file is the single-source vocabulary pin.

Cross-links:
- W1030 — ``return_status=True`` substrate.
- W1030-followup-A / -B / -C / -D — per-command wiring.
- CLAUDE.md "Six systemic anti-patterns" / Pattern 3a "metric divergence".
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
    """Create a minimal indexed project under tmp_path/<name>."""
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


def _extract_envelope(output: str) -> dict:
    """Pull the trailing JSON envelope out of mixed stdout.

    Walks back through lines to find the first line that begins at
    column 0 with ``{`` — that's the envelope's opening brace (every
    inner brace is indented by ``to_json``'s pretty-printer).
    """
    lines = output.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].startswith("{"):
            return _json.loads("\n".join(lines[idx:]))
    raise AssertionError(f"no JSON envelope found in output:\n{output}")


def _alerts_envelope(proj: Path) -> dict:
    runner = CliRunner()
    result = invoke_cli(runner, ["alerts"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, f"alerts failed:\n{result.output}"
    raw = getattr(result, "stdout", None) or result.output
    return _json.loads(raw)


def _budget_envelope(proj: Path) -> dict:
    runner = CliRunner()
    result = invoke_cli(runner, ["budget"], cwd=proj, json_mode=True)
    # budget exits 0 (all-skipped on a fresh project, no failing rules).
    assert result.exit_code in (0, 5), f"budget failed:\n{result.output}"
    raw = getattr(result, "stdout", None) or result.output
    return _json.loads(raw)


def _health_gate_envelope(proj: Path) -> dict:
    """Invoke ``roam --json health --gate``; tolerate both gate-pass and gate-fail.

    Both paths emit a JSON envelope; on gate-fail the click exit raises a
    GateFailureError after stdout flushes the envelope, so the parser
    walks brace depth to extract the JSON.
    """
    runner = CliRunner()
    result = invoke_cli(runner, ["health", "--gate"], cwd=proj, json_mode=True)
    raw = getattr(result, "stdout", None) or result.output
    text = raw.strip()
    if not text.startswith("{"):
        raise AssertionError(f"expected JSON envelope, got:\n{raw}")
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


def _check_rules_envelope(proj: Path) -> dict:
    runner = CliRunner()
    result = invoke_cli(runner, ["check-rules"], cwd=proj, json_mode=True)
    assert result.exit_code in (0, 1), f"check-rules failed:\n{result.output}"
    return _extract_envelope(result.output)


def _fitness_envelope(proj: Path) -> dict:
    runner = CliRunner()
    result = invoke_cli(runner, ["fitness"], cwd=proj, json_mode=True)
    # fitness exits 0 on no-rules / no-failure; 1 on rule failure. Both
    # emit an envelope; the drift guard only cares about envelope shape.
    assert result.exit_code in (0, 1), f"fitness failed:\n{result.output}"
    return _extract_envelope(result.output)


def _rules_envelope(proj: Path) -> dict:
    """W1030-followup-F: invoke ``roam --json rules`` and parse envelope.

    ``rules`` exits 0 when no rules dir is configured (the "missing" state
    the drift-guard fixture exercises). It can also exit 1 in --ci mode
    on error-severity violations; we don't set --ci so 0 is expected.
    """
    runner = CliRunner()
    result = invoke_cli(runner, ["rules"], cwd=proj, json_mode=True)
    assert result.exit_code in (0, 1), f"rules failed:\n{result.output}"
    return _extract_envelope(result.output)


# Parametrized command fixture: (command_name, envelope_getter). The
# envelope_getter accepts a project path and returns the parsed envelope.
_EMITTERS: list[tuple[str, callable]] = [
    ("alerts", _alerts_envelope),
    ("budget", _budget_envelope),
    ("health", _health_gate_envelope),
    ("check-rules", _check_rules_envelope),
    ("fitness", _fitness_envelope),
    ("rules", _rules_envelope),
]


# ---------------------------------------------------------------------------
# Per-emitter: ``summary.config_state`` is set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command,getter", _EMITTERS, ids=[c for c, _ in _EMITTERS])
def test_envelope_has_config_state(tmp_path: Path, command: str, getter) -> None:
    """Every W1030-followup-cohort envelope exposes ``summary.config_state``.

    The five commands wired by W1030-followup-A/-B/-C/-D must converge
    on the same field name. This test catches a rename drift (e.g. a
    future refactor renaming the field to ``loader_state``) at CI time.
    """
    proj = _make_indexed_project(tmp_path, f"vocab_{command}")
    data = getter(proj)
    summary = data.get("summary", {})
    assert "config_state" in summary, (
        f"{command} envelope MUST expose summary.config_state field, got summary: {summary!r}"
    )


def test_fitness_envelope_has_config_state(tmp_path: Path) -> None:
    """W1030-followup-D pin: the cmd_fitness envelope carries config_state.

    Explicit single-command test sibling to the parametrized
    ``test_envelope_has_config_state`` so a CI run that selects this
    file's tests by name (e.g. ``-k fitness``) still exercises the
    fitness-specific surface. Mirrors the original
    ``test_alerts_envelope_has_config_state`` / ``test_budget_*``
    convention as the cohort grows.
    """
    proj = _make_indexed_project(tmp_path, "vocab_fitness_explicit")
    data = _fitness_envelope(proj)
    summary = data.get("summary", {})
    assert "config_state" in summary, (
        f"fitness envelope MUST expose summary.config_state field, got summary: {summary!r}"
    )
    from roam.commands._yaml_loader import LOAD_STATUSES

    assert summary["config_state"] in LOAD_STATUSES, (
        f"fitness config_state {summary['config_state']!r} must be a member of LOAD_STATUSES {LOAD_STATUSES!r}"
    )


def test_rules_envelope_has_config_state(tmp_path: Path) -> None:
    """W1030-followup-F pin: the cmd_rules envelope carries config_state.

    Explicit single-command test sibling to the parametrized
    ``test_envelope_has_config_state``. cmd_rules is the directory-of-
    files variant: ``rules/engine.py`` exposes a per-file ``LoadStatus``
    rollup so the envelope can disambiguate missing (no .roam/rules/) /
    empty (directory exists but no .yaml files) / parse_error (some file
    is malformed) / ok.
    """
    proj = _make_indexed_project(tmp_path, "vocab_rules_explicit")
    data = _rules_envelope(proj)
    summary = data.get("summary", {})
    assert "config_state" in summary, f"rules envelope MUST expose summary.config_state field, got summary: {summary!r}"
    from roam.commands._yaml_loader import LOAD_STATUSES

    assert summary["config_state"] in LOAD_STATUSES, (
        f"rules config_state {summary['config_state']!r} must be a member of LOAD_STATUSES {LOAD_STATUSES!r}"
    )


# ---------------------------------------------------------------------------
# Cross-emitter: field name uniformity
# ---------------------------------------------------------------------------


def test_all_four_field_name_is_config_state(tmp_path: Path) -> None:
    """Drift guard for a field-name rename.

    Asserts the ID-string ``"config_state"`` (NOT ``"loader_state"`` /
    ``"config_status"`` / ``"yaml_state"``) appears in every envelope's
    summary keys. Single source of truth: if you rename the field in
    one command, this test fails for every other cohort member.

    Name kept ``_all_four_`` for compatibility with the W1030-followup-D
    historical naming; the actual scope is "every emitter in
    ``_EMITTERS``" (now 6 with W1030-followup-F).
    """
    found_field_names: dict[str, set[str]] = {}
    for command, getter in _EMITTERS:
        proj = _make_indexed_project(tmp_path, f"fieldname_{command}")
        data = getter(proj)
        summary = data.get("summary", {})
        found_field_names[command] = set(summary.keys())
    # Every command's summary must contain exactly "config_state".
    for command, keys in found_field_names.items():
        assert "config_state" in keys, (
            f"{command} envelope summary missing 'config_state' field. "
            f"Did you rename it? Got summary keys: {sorted(keys)!r}"
        )
    # Drift guard against silent alternate-name regressions.
    legacy_aliases = {"loader_state", "config_status", "yaml_state", "load_state"}
    for command, keys in found_field_names.items():
        leaked = keys & legacy_aliases
        assert not leaked, (
            f"{command} envelope summary leaked a legacy alias "
            f"{leaked!r} alongside config_state; pick one name and "
            f"stick with it (W1030-followup vocabulary uniformity)."
        )


# ---------------------------------------------------------------------------
# Cross-emitter: value-space uniformity
# ---------------------------------------------------------------------------


def test_all_four_values_subset_of_load_statuses(tmp_path: Path) -> None:
    """Every emitted ``config_state`` is a member of :data:`LOAD_STATUSES`.

    The closed enum lives at ``roam.commands._yaml_loader.LOAD_STATUSES``.
    No command may invent its own status alphabet (e.g. ``"absent"`` /
    ``"none"`` / ``"degraded"``); the value space stays the same shape
    across every cohort emitter.

    Name kept ``_all_four_`` for compatibility with the W1030-followup-D
    historical naming; the actual scope is "every emitter in
    ``_EMITTERS``" (now 6 with W1030-followup-F).
    """
    from roam.commands._yaml_loader import LOAD_STATUSES

    for command, getter in _EMITTERS:
        proj = _make_indexed_project(tmp_path, f"vocab_values_{command}")
        data = getter(proj)
        state = data.get("summary", {}).get("config_state")
        assert state in LOAD_STATUSES, (
            f"{command} emitted config_state={state!r}, which is NOT a "
            f"member of LOAD_STATUSES {LOAD_STATUSES!r}. Did you invent "
            f"a new status? Extend LOAD_STATUSES at "
            f"src/roam/commands/_yaml_loader.py first."
        )


# ---------------------------------------------------------------------------
# Sanity: the LOAD_STATUSES enum hasn't shrunk
# ---------------------------------------------------------------------------


def test_load_statuses_enum_has_canonical_members() -> None:
    """Pin the eight canonical LoadStatus members (W1030 contract).

    Belt-and-suspenders drift guard: even if a refactor accidentally
    drops a member from ``LOAD_STATUSES``, this test catches it before
    the per-emitter tests have a chance to fail in confusing ways.
    """
    from roam.commands._yaml_loader import LOAD_STATUSES

    canonical = {
        "ok",
        "missing",
        "empty_file",
        "empty_yaml",
        "read_error",
        "parse_error",
        "wrong_root_type",
        "schema_invalid",
    }
    assert canonical.issubset(set(LOAD_STATUSES)), (
        f"LOAD_STATUSES missing canonical members; expected superset of {canonical!r}, got {LOAD_STATUSES!r}"
    )
