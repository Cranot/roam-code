"""Tests for roam migration-plan."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from roam.commands.cmd_migration_plan import (
    _classify_layer,
    _parse_target_spec,
    _verdict,
    migration_plan_cmd,
)


def test_classify_layer_known_paths() -> None:
    assert _classify_layer("src/api/users.py") == "http"
    assert _classify_layer("src/views/login.tsx") == "ui"
    assert _classify_layer("src/domain/checkout.py") == "domain"
    assert _classify_layer("src/data/repository.py") == "data"
    assert _classify_layer("src/models/user.py") == "data"


def test_classify_layer_unknown_returns_none() -> None:
    assert _classify_layer("src/utils/helpers.py") is None
    assert _classify_layer("scripts/random_thing.sh") is None


def test_parse_inline_moves_only() -> None:
    moves = _parse_target_spec(None, ("UserService=src/services/user.py", "Foo=src/foo.py"))
    assert len(moves) == 2
    assert moves[0]["symbol"] == "UserService"
    assert moves[0]["target"] == "src/services/user.py"
    assert moves[1]["symbol"] == "Foo"


def test_parse_target_spec_yaml(tmp_path: Path) -> None:
    spec = tmp_path / "target.yml"
    spec.write_text(
        "moves:\n"
        "  - symbol: UserService\n"
        "    to: src/services/user.py\n"
        "  - symbol: PaymentProcessor\n"
        "    to: src/services/payment.py\n",
        encoding="utf-8",
    )
    moves = _parse_target_spec(str(spec), ())
    assert len(moves) == 2
    assert {m["symbol"] for m in moves} == {"UserService", "PaymentProcessor"}


def test_parse_yaml_plus_inline_combine(tmp_path: Path) -> None:
    spec = tmp_path / "target.yml"
    spec.write_text("moves:\n  - symbol: A\n    to: a.py\n", encoding="utf-8")
    moves = _parse_target_spec(str(spec), ("B=b.py",))
    assert len(moves) == 2


def test_verdict_no_plan() -> None:
    assert _verdict([], []) == "NO PLAN"


def test_verdict_all_high_risk_skipped() -> None:
    skipped = [{"risk": "high"}]
    assert _verdict([], skipped) == "ALL HIGH RISK"


def test_verdict_proceed_low_risk_only() -> None:
    plan = [{"risk": "low"}, {"risk": "low"}]
    assert _verdict(plan, []) == "PROCEED  (all low-risk)"


def test_verdict_proceed_with_care_high_risk_included() -> None:
    plan = [{"risk": "high"}, {"risk": "low"}]
    msg = _verdict(plan, [])
    assert "PROCEED WITH CARE" in msg
    assert "1 high-risk" in msg


def test_cli_no_target_emits_no_plan() -> None:
    runner = CliRunner()
    result = runner.invoke(migration_plan_cmd, [], obj={})
    assert result.exit_code == 0
    assert "NO PLAN" in result.output


def test_cli_inline_move_runs_against_index() -> None:
    """Smoke: --move directive parses and runs through the pipeline.

    Doesn't assert on caller counts because the index state varies; just
    confirms exit 0 and that the verdict line appears.
    """
    runner = CliRunner()
    result = runner.invoke(migration_plan_cmd, ["--move", "Nonexistent=src/x.py"], obj={})
    # Either NO PLAN or PROCEED (low risk for a symbol with 0 callers); both fine
    assert result.exit_code == 0
    assert "VERDICT:" in result.output
