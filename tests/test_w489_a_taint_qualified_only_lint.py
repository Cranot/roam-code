"""W489-A: wire the W454/W479 `qualified_only` rule-load lint warnings
into the `roam taint` envelope so out-of-tree rule packs loaded via
`--rules-dir` get the same disclosure the shipped pack does.

The W479 hygiene tests (`tests/test_taint_rule_hygiene.py`) prove the
lint warnings fire at `load_rules` call time. This file proves they
reach the `roam taint` envelope as:

- `summary.rules_lint.qualified_only_violations: N`
- `summary.rules_lint.total_rules: M`
- `qualified_only_violations: [{rule_id, kind, name, message}]`
  (top-level, only when N > 0)
- `summary.partial_success: True` (only when N > 0)
- `summary.warnings_out: ["..."]` (only when N > 0)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli


def _write_clean_rule(rules_dir: Path) -> None:
    """One rule with `qualified_only: true` and only dot-qualified entries —
    no warnings expected."""
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
    """One rule with `qualified_only: true` and bare entries —
    fires W454/W479 lint."""
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


def _extract_envelope(output: str) -> dict:
    """`ensure_index()` prints progress on stdout before the JSON
    envelope, so we slice from the first ``{`` to the matching ``}``
    rather than ``json.loads(result.output)`` directly."""
    start = output.find("{")
    assert start != -1, f"no JSON object found in output: {output!r}"
    # Find the matching closing brace by depth-tracking.
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(output[start:], start=start):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
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
                return json.loads(output[start : i + 1])
    raise AssertionError(f"unterminated JSON object in output: {output!r}")


def _run_taint_json(rules_dir: Path) -> dict:
    """Invoke `roam --json taint --rules-dir <dir>` in an indexed cwd
    so the `ensure_index()` gate doesn't redirect to the no-rules /
    empty-corpus branch. We don't need real findings — the envelope's
    `rules_lint` block is what's under test."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "taint", "--rules-dir", str(rules_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"non-zero exit: {result.exit_code}\n{result.output}"
    return _extract_envelope(result.output)


def test_w489_a_envelope_surfaces_violating_rule(tmp_path: Path) -> None:
    """Fixture A: 1 clean + 1 violating rule → envelope discloses the
    violation with `rule_id`, `kind`, `name`."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    _write_clean_rule(rules_dir)
    _write_violating_rule(rules_dir)

    # cwd into a fresh tmp project so `ensure_index()` doesn't pull
    # the parent roam-code index.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("def f(): pass\n", encoding="utf-8")

    cwd = os.getcwd()
    try:
        os.chdir(proj)
        envelope = _run_taint_json(rules_dir)
    finally:
        os.chdir(cwd)

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


def test_w489_a_envelope_clean_no_top_level_list(tmp_path: Path) -> None:
    """Fixture B: all-clean rules dir → rules_lint reports 0 violations,
    no top-level list, no partial_success bump from W489-A."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    _write_clean_rule(rules_dir)

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("def f(): pass\n", encoding="utf-8")

    cwd = os.getcwd()
    try:
        os.chdir(proj)
        envelope = _run_taint_json(rules_dir)
    finally:
        os.chdir(cwd)

    summary = envelope["summary"]
    rl = summary["rules_lint"]
    assert rl["qualified_only_violations"] == 0, f"expected 0 violations, got {rl!r}"
    assert rl["total_rules"] == 1, f"expected total_rules=1, got {rl!r}"
    assert "qualified_only_violations" not in envelope, (
        f"top-level qualified_only_violations should be absent when N=0, got {sorted(envelope)!r}"
    )
    # The empty_corpus branch sets partial_success=True for its own
    # reason (no symbols), so this assertion would conflict with that.
    # Instead: W489-A specifically must NOT contribute a warnings_out
    # entry, and qualified_only_violations[] must NOT appear at top
    # level.
    warnings_out = summary.get("warnings_out") or []
    assert not any("qualified_only lint" in w for w in warnings_out), (
        f"qualified_only warnings_out leaked on clean rules: {warnings_out!r}"
    )


def test_w489_a_default_rules_pack_surfaces_lint(tmp_path: Path) -> None:
    """Fixture C: no `--rules-dir` → lint runs against the shipped
    rule pack. If any shipped rule has a `qualified_only: true` + bare
    entry combination, this test surfaces the real bug rather than
    hiding it.

    Per `tests/test_taint_rule_hygiene.py`, the shipped pack is currently
    clean — so this asserts the default-path also reports 0 violations.
    If the shipped pack regresses, BOTH this test and the W479 hygiene
    test fail; the rule_id and entry name will appear in the envelope's
    `qualified_only_violations[]` list for diagnosis.
    """
    runner = CliRunner()

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("def f(): pass\n", encoding="utf-8")

    cwd = os.getcwd()
    try:
        os.chdir(proj)
        result = runner.invoke(cli, ["--json", "taint"], catch_exceptions=False)
        assert result.exit_code == 0, f"non-zero exit: {result.exit_code}\n{result.output}"
        envelope = _extract_envelope(result.output)
    finally:
        os.chdir(cwd)

    summary = envelope["summary"]
    assert "rules_lint" in summary, f"shipped-pack path missing rules_lint disclosure: {summary!r}"
    rl = summary["rules_lint"]
    assert rl["total_rules"] > 0, f"shipped pack should have rules, got {rl!r}"
    assert rl["qualified_only_violations"] == 0, (
        f"SHIPPED RULE PACK BUG: qualified_only violations on default path: "
        f"{envelope.get('qualified_only_violations')!r}"
    )
