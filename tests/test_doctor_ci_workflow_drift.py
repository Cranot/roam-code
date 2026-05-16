"""W482 — tests for the doctor CI-workflow-drift advisory check.

Covers the three principal states:

* **drift** — live workflow exists but diverges from the bundled template
* **clean** — live workflow matches the bundled template
* **not_emitted** — template exists but the user hasn't emitted the live
  workflow yet (advisory pass; recorded under ``missing``)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands.cmd_doctor import (
    _check_ci_workflow_drift,
    _extract_python_version_from_workflow,
    _github_template_registry,
    _normalize_workflow_yaml,
    doctor,
)

# --- Pure-function tests for normalization + registry ----------------


def test_normalize_workflow_strips_comments_and_blank_runs() -> None:
    """Comments + blank-line runs must be ignored for drift comparison."""
    a = "# header comment\nname: roam\n\n\non: pull_request\n# trailing comment\n"
    b = "name: roam\n\non: pull_request\n"
    assert _normalize_workflow_yaml(a) == _normalize_workflow_yaml(b)


def test_normalize_workflow_preserves_semantic_difference() -> None:
    """A real value change must not be normalized away."""
    a = "name: roam\nversion: 1\n"
    b = "name: roam\nversion: 2\n"
    assert _normalize_workflow_yaml(a) != _normalize_workflow_yaml(b)


def test_github_template_registry_returns_known_pairs() -> None:
    """Registry returns at least the W471 SLSA SRC-L3 pair."""
    pairs = _github_template_registry()
    assert any(t == "slsa-src-l3.yml" for t, _ in pairs)
    # Every entry maps under .github/workflows/.
    for _, live_rel in pairs:
        assert live_rel.startswith(".github/workflows/")


# --- Behaviour tests with synthetic workspace ------------------------


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Chdir into an empty tmp dir so no real workflows leak in."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_drift_check_no_workflows_is_not_applicable(isolated_cwd: Path) -> None:
    """Empty project — no live workflows + no drift = advisory pass, not_applicable."""
    result = _check_ci_workflow_drift()
    assert result["passed"] is True
    assert result["_state"] == "not_applicable"
    assert result["_checked"] == 0
    assert result["_drifted"] == []


def test_drift_check_detects_modified_workflow(isolated_cwd: Path) -> None:
    """A live workflow that diverges from its template fires the advisory."""
    workflows_dir = isolated_cwd / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)

    # Synthesize a clearly-divergent SLSA SRC-L3 workflow.
    drifted = (
        "name: NOT-the-real-slsa-template\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "jobs:\n"
        "  fake:\n"
        "    runs-on: ubuntu-latest\n"
    )
    (workflows_dir / "roam-slsa-src-l3.yml").write_text(drifted, encoding="utf-8")

    result = _check_ci_workflow_drift()
    assert result["passed"] is False
    assert result["_state"] == "drift"
    assert result["_checked"] == 1
    drifted_entries = result["_drifted"]
    assert len(drifted_entries) == 1
    entry = drifted_entries[0]
    assert entry["template"] == "slsa-src-l3.yml"
    assert entry["live_path"] == ".github/workflows/roam-slsa-src-l3.yml"
    assert entry["state"] == "drifted"
    assert "first divergence at line" in entry["diff_summary"]
    # LAW 4 anchor — terminal token is concrete ("template").
    assert "drifted from template" in result["detail"]


def test_drift_check_matches_template_exactly(isolated_cwd: Path) -> None:
    """Live workflow identical to template = clean state."""
    from roam.commands.cmd_ci_setup import (
        _get_python_version,
        _load_slsa_src_l3_template,
        _substitute_vars,
    )

    workflows_dir = isolated_cwd / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)

    rendered = _substitute_vars(
        _load_slsa_src_l3_template(),
        {"python_version": _get_python_version()},
    )
    (workflows_dir / "roam-slsa-src-l3.yml").write_text(rendered, encoding="utf-8")

    result = _check_ci_workflow_drift()
    # At least one workflow was checked and matched. (Other registered
    # workflows are absent — that's "not_emitted", not a failure.)
    assert result["passed"] is True
    assert result["_state"] == "clean"
    assert result["_checked"] >= 1
    assert result["_drifted"] == []
    # The absent live workflows show up under _missing as not_emitted.
    missing_states = {m["state"] for m in result["_missing"]}
    assert missing_states <= {"not_emitted"}


def test_drift_check_records_not_emitted_when_template_unused(isolated_cwd: Path) -> None:
    """No live workflow at all → templates surface under missing, not drifted."""
    # No .github/workflows/ exists at all.
    result = _check_ci_workflow_drift()
    # not_applicable is also valid here since checked == 0.
    assert result["passed"] is True
    assert result["_state"] in {"not_applicable", "clean"}


def test_drift_check_handles_comment_only_diff_as_clean(isolated_cwd: Path) -> None:
    """Comment-only edits in the live file must NOT register as drift."""
    from roam.commands.cmd_ci_setup import (
        _get_python_version,
        _load_slsa_src_l3_template,
        _substitute_vars,
    )

    workflows_dir = isolated_cwd / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)

    rendered = _substitute_vars(
        _load_slsa_src_l3_template(),
        {"python_version": _get_python_version()},
    )
    # Inject a user-added comment at the top — should still match.
    customised = "# Customised by Acme Corp 2026-05-14\n" + rendered
    (workflows_dir / "roam-slsa-src-l3.yml").write_text(customised, encoding="utf-8")

    result = _check_ci_workflow_drift()
    assert result["passed"] is True
    # Live workflow was checked, matched after normalization.
    assert result["_checked"] >= 1
    assert result["_drifted"] == []


# --- End-to-end doctor envelope wiring -------------------------------


def test_doctor_envelope_includes_ci_workflow_drift_block(isolated_cwd: Path) -> None:
    """The ``ci_workflow_drift`` top-level block must appear in --json output."""
    runner = CliRunner()
    # No index needed for this advisory — but doctor walks every check.
    result = runner.invoke(doctor, ["--strict"], obj={"json": True})
    # exit code may be 1 or 2 depending on other advisory failures in
    # this isolated dir (no index → multiple advisory FAILs are
    # expected); the envelope shape is what we're asserting.
    assert result.output, f"empty doctor output (exit {result.exit_code})"
    envelope = json.loads(result.output)
    assert "ci_workflow_drift" in envelope, envelope.keys()
    block = envelope["ci_workflow_drift"]
    assert "state" in block
    assert "templates_checked" in block
    assert "drifted" in block
    assert "missing" in block
    # Empty workspace, no .github/workflows/ → not_applicable.
    assert block["state"] in {"not_applicable", "clean", "drift"}


# --- W515 — python-version pin must not cause false drift ------------


def test_extract_python_version_handles_quote_variants() -> None:
    """Single-quote, double-quote, and bare scalar all parse."""
    assert _extract_python_version_from_workflow("          python-version: '3.11'\n") == "3.11"
    assert _extract_python_version_from_workflow('          python-version: "3.12"\n') == "3.12"
    assert _extract_python_version_from_workflow("          python-version: 3.13\n") == "3.13"


def test_extract_python_version_returns_none_when_absent() -> None:
    """Templates without a python-version line yield ``None``."""
    assert _extract_python_version_from_workflow("name: foo\non: push\n") is None


def test_drift_check_respects_live_python_version_pin(isolated_cwd: Path) -> None:
    """W515 — `ci-setup --python-version 3.11 --write` must not yield drift.

    Pre-W515: the drift check rendered the template with the default
    (3.12) regardless of the pin baked into the live file, so a user
    who explicitly chose 3.11 saw FALSE drift on every doctor run.
    """
    from roam.commands.cmd_ci_setup import (
        _load_slsa_src_l3_template,
        _substitute_vars,
    )

    workflows_dir = isolated_cwd / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)

    # Simulate `roam ci-setup --python-version 3.11 --write` by rendering
    # with a NON-default pin and writing the file.
    pinned = _substitute_vars(
        _load_slsa_src_l3_template(),
        {"python_version": "3.11"},
    )
    (workflows_dir / "roam-slsa-src-l3.yml").write_text(pinned, encoding="utf-8")

    result = _check_ci_workflow_drift()
    # Must be CLEAN — the only divergence (the pin) is what W515
    # explicitly normalizes away.
    assert result["passed"] is True
    assert result["_state"] == "clean", result
    assert result["_drifted"] == []


def test_drift_check_still_detects_real_drift_with_pin(isolated_cwd: Path) -> None:
    """W515 negative — pin handling must not hide real structural drift."""
    from roam.commands.cmd_ci_setup import (
        _load_slsa_src_l3_template,
        _substitute_vars,
    )

    workflows_dir = isolated_cwd / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)

    # Render with a non-default pin, then mutate a load-bearing line so
    # there's real drift in addition to the pin divergence.
    pinned = _substitute_vars(
        _load_slsa_src_l3_template(),
        {"python_version": "3.11"},
    )
    # Tamper with a structural line — flip runs-on so normalization
    # can't collapse the diff.
    tampered = pinned.replace("runs-on: ubuntu-latest", "runs-on: tampered-runner")
    assert tampered != pinned, "tamper failed — template shape changed upstream"
    (workflows_dir / "roam-slsa-src-l3.yml").write_text(tampered, encoding="utf-8")

    result = _check_ci_workflow_drift()
    assert result["passed"] is False
    assert result["_state"] == "drift"
    assert any(d["template"] == "slsa-src-l3.yml" for d in result["_drifted"]), result
