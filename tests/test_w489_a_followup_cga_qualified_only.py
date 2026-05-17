"""W489-A-followup: wire the W454/W479 `qualified_only` rule-load lint
warnings into the `roam cga emit --include-taint` envelope so out-of-tree
taint-rule packs get the same disclosure the `roam taint` envelope ships.

The W489-A canonical envelope shape — `summary.rules_lint` +
`qualified_only_violations[]` + `summary.partial_success` +
`summary.warnings_out` — is the source of truth (sealed by
``tests/test_w489_a_taint_qualified_only_lint.py``). This test file
proves cmd_cga mirrors that shape byte-for-byte at the field level.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import make_src_project as _make_src_project


@pytest.fixture(autouse=True)
def _enforcement_safe(monkeypatch):
    """Mirror the existing test_cga.py fixture so privileged `roam cga`
    runs under future ROAM_MODE_ENFORCEMENT default-on (W23.3)."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")


@pytest.fixture
def cga_project(tmp_path):
    """Match the canonical cga_project fixture (test_cga.py:339) — indexed
    tmp project with one Python file. Index is required so `ensure_index`
    inside cga_emit doesn't rebuild against the parent roam-code checkout."""
    proj = _make_src_project(
        tmp_path,
        {
            "auth.py": (
                "class UserSession:\n"
                "    def refresh(self):\n"
                "        return self.token\n"
                "def handle_login(user):\n"
                "    return UserSession()\n"
            ),
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


def _write_clean_rule(rules_dir: Path) -> None:
    """One rule with `qualified_only: true` and only dot-qualified entries —
    no warnings expected. Mirrors test_w489_a_taint_qualified_only_lint.py."""
    (rules_dir / "clean.yaml").write_text(
        "id: test-clean-rule\n"
        "description: synthetic\n"
        "severity: warning\n"
        "qualified_only: true\n"
        "languages:\n"
        "  - python\n"
        "sources:\n"
        "  - requests.get\n"
        "sinks:\n"
        "  - subprocess.run\n"
        "sanitizers:\n"
        "  - shlex.quote\n",
        encoding="utf-8",
    )


def _write_violating_rule(rules_dir: Path) -> None:
    """One rule with `qualified_only: true` and bare entries — fires
    W454/W479 lint. Mirrors test_w489_a_taint_qualified_only_lint.py."""
    (rules_dir / "violating.yaml").write_text(
        "id: test-violating-rule\n"
        "description: synthetic\n"
        "severity: warning\n"
        "qualified_only: true\n"
        "languages:\n"
        "  - python\n"
        "sources:\n"
        "  - requests.get\n"
        "  - bareSource\n"
        "sinks:\n"
        "  - subprocess.run\n"
        "  - bareSink\n",
        encoding="utf-8",
    )


def _run_cga_emit_json(rules_dir: Path) -> dict:
    """Invoke `roam --json cga emit --include-taint --taint-rules-dir <dir>
    --no-write --allow-dirty`. ``--no-write`` keeps the test hermetic;
    ``--allow-dirty`` works around the tmp-project's lack of a clean
    git tree."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "cga",
            "emit",
            "--include-taint",
            "--taint-rules-dir",
            str(rules_dir),
            "--no-write",
            "--allow-dirty",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"non-zero exit: {result.exit_code}\n{result.output}"
    return json.loads(result.output)


def test_w489_a_followup_cga_envelope_surfaces_violating_rule(cga_project, tmp_path):
    """Fixture A: 1 clean + 1 violating rule → cga envelope discloses the
    violation with `rule_id`, `kind`, `name`. Mirrors the cmd_taint
    canonical shape (test_w489_a_taint_qualified_only_lint.py line 138-159)."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    _write_clean_rule(rules_dir)
    _write_violating_rule(rules_dir)

    envelope = _run_cga_emit_json(rules_dir)

    summary = envelope["summary"]
    assert "rules_lint" in summary, f"missing rules_lint in summary: {summary!r}"
    rl = summary["rules_lint"]
    assert rl["qualified_only_violations"] == 2, f"expected 2 violations (bareSource + bareSink), got {rl!r}"
    assert rl["total_rules"] == 2, f"expected total_rules=2, got {rl!r}"
    assert summary.get("partial_success") is True, f"expected partial_success=True with violations, got {summary!r}"
    warnings_out = summary.get("warnings_out") or []
    assert any("qualified_only lint" in w for w in warnings_out), f"expected warnings_out entry, got {warnings_out!r}"

    violations = envelope.get("qualified_only_violations")
    assert violations, f"expected top-level qualified_only_violations list, got envelope keys: {sorted(envelope)!r}"
    assert len(violations) == 2

    by_name = {v["name"]: v for v in violations}
    assert "bareSource" in by_name and by_name["bareSource"]["rule_id"] == "test-violating-rule"
    assert by_name["bareSource"]["kind"] == "sources"
    assert "bareSink" in by_name and by_name["bareSink"]["kind"] == "sinks"
    # Sanity: message preserved verbatim for downstream consumers.
    assert "qualified_only=true" in by_name["bareSource"]["message"]


def test_w489_a_followup_cga_envelope_clean_no_top_level_list(cga_project, tmp_path):
    """Fixture B: all-clean rules dir → rules_lint reports 0 violations,
    no top-level list, no W489-A-followup-driven partial_success flip /
    warnings_out leak."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    _write_clean_rule(rules_dir)

    envelope = _run_cga_emit_json(rules_dir)

    summary = envelope["summary"]
    rl = summary["rules_lint"]
    assert rl["qualified_only_violations"] == 0, f"expected 0 violations, got {rl!r}"
    assert rl["total_rules"] == 1, f"expected total_rules=1, got {rl!r}"
    assert "qualified_only_violations" not in envelope, (
        f"top-level qualified_only_violations should be absent when N=0, got {sorted(envelope)!r}"
    )
    # W489-A-followup must NOT contribute a partial_success flip or a
    # warnings_out entry on the clean path.
    assert summary.get("partial_success") is not True, f"partial_success leaked on clean rules: {summary!r}"
    warnings_out = summary.get("warnings_out") or []
    assert not any("qualified_only lint" in w for w in warnings_out), (
        f"qualified_only warnings_out leaked on clean rules: {warnings_out!r}"
    )


def test_w489_a_followup_default_taint_pack_no_violations(cga_project):
    """Fixture C: cga emit --include-taint without --taint-rules-dir →
    lint runs against the shipped rule pack. Mirrors the cmd_taint
    canonical test_w489_a_default_rules_pack_surfaces_lint (per W479
    hygiene the shipped pack is clean — 0 violations expected). If the
    shipped pack regresses, this test AND test_w489_a_taint_*  fail in
    lockstep."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--json",
            "cga",
            "emit",
            "--include-taint",
            "--no-write",
            "--allow-dirty",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"non-zero exit: {result.exit_code}\n{result.output}"
    envelope = json.loads(result.output)

    summary = envelope["summary"]
    assert "rules_lint" in summary, f"shipped-pack path missing rules_lint disclosure: {summary!r}"
    rl = summary["rules_lint"]
    assert rl["total_rules"] > 0, f"shipped pack should have rules, got {rl!r}"
    assert rl["qualified_only_violations"] == 0, (
        f"SHIPPED RULE PACK BUG: qualified_only violations on default cga path: "
        f"{envelope.get('qualified_only_violations')!r}"
    )


def test_w489_a_followup_no_rules_lint_without_include_taint(cga_project):
    """Without `--include-taint`, the cga envelope must NOT emit
    `rules_lint` — no taint rules were loaded, so the disclosure would
    be a phantom field. Symmetric emission (W1101) is scoped to the
    "rules-were-loaded" path."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "cga", "emit", "--no-write", "--allow-dirty"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"non-zero exit: {result.exit_code}\n{result.output}"
    envelope = json.loads(result.output)
    summary = envelope["summary"]
    assert "rules_lint" not in summary, f"rules_lint must only appear under --include-taint; got {summary!r}"
    assert "qualified_only_violations" not in envelope, (
        f"top-level qualified_only_violations leaked without --include-taint: {sorted(envelope)!r}"
    )


def test_w489_a_followup_mirror_parity_with_taint_envelope_shape(cga_project, tmp_path):
    """Mirror parity: every field W489-A stamps on the cmd_taint envelope
    is stamped identically on the cmd_cga envelope. The W489-A canonical
    shape is the source of truth; cmd_cga is the second consumer."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    _write_violating_rule(rules_dir)

    # --- cmd_cga envelope ------------------------------------------------
    cga_envelope = _run_cga_emit_json(rules_dir)
    cga_summary = cga_envelope["summary"]

    # Field-shape parity assertions (mirrors the W489-A canonical shape):
    assert "rules_lint" in cga_summary
    assert set(cga_summary["rules_lint"].keys()) == {
        "qualified_only_violations",
        "total_rules",
    }
    assert isinstance(cga_summary["rules_lint"]["qualified_only_violations"], int)
    assert isinstance(cga_summary["rules_lint"]["total_rules"], int)
    assert cga_summary.get("partial_success") is True
    assert isinstance(cga_summary.get("warnings_out"), list)
    assert "qualified_only_violations" in cga_envelope
    for v in cga_envelope["qualified_only_violations"]:
        assert set(v.keys()) == {"rule_id", "kind", "name", "message"}
