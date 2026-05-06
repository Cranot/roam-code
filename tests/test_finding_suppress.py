"""Tests for the finding-suppression mechanism (M7) + `roam suppress` command."""

from __future__ import annotations

import json as _json

from click.testing import CliRunner

from roam.commands.finding_suppress import (
    _inline_match,
    _path_matches_glob,
    annotate_with_suppression,
    filter_suppressed,
    finding_id,
)


def test_finding_id_is_deterministic():
    a = finding_id("io-in-loop", "src/foo.py:42", "MyController.list")
    b = finding_id("io-in-loop", "src/foo.py:42", "MyController.list")
    assert a == b
    assert len(a) == 16


def test_finding_id_differs_for_different_locations():
    a = finding_id("io-in-loop", "src/foo.py:42", "fn")
    b = finding_id("io-in-loop", "src/foo.py:99", "fn")
    assert a != b


# ---- inline annotations -----------------------------------------------------


def test_inline_match_bare_command_covers_all_task_ids():
    """`# roam: ignore-math` (no [...]) suppresses every math task on the line."""
    line = "result = items[0]  # roam: ignore-math"
    assert _inline_match(line, command="math", task_id="sort-to-select")
    assert _inline_match(line, command="math", task_id="branching-recursion")


def test_inline_match_targeted_task_id_is_specific():
    line = "    return deepEqual(a, b)  # roam: ignore-math[branching-recursion]"
    assert _inline_match(line, command="math", task_id="branching-recursion")
    # A different task on the same command isn't covered:
    assert not _inline_match(line, command="math", task_id="sort-to-select")


def test_inline_match_wildcard_covers_all():
    line = "// roam: ignore-math[*]"
    assert _inline_match(line, command="math", task_id="anything-here")


def test_inline_match_wrong_command_no_match():
    line = "  # roam: ignore-math[io-in-loop]"
    assert not _inline_match(line, command="over-fetch", task_id="io-in-loop")


def test_inline_match_handles_no_annotation():
    assert not _inline_match("plain old code", command="math", task_id="x")
    assert not _inline_match("", command="math", task_id="x")


def test_inline_match_supports_all_four_commands():
    """Single regex must cover math, over-fetch, missing-index, auth-gaps."""
    for cmd in ("math", "over-fetch", "missing-index", "auth-gaps"):
        line = f"thing  # roam: ignore-{cmd}[some-id]"
        assert _inline_match(line, command=cmd, task_id="some-id"), f"failed for {cmd}"


# ---- glob path matching -----------------------------------------------------


def test_path_matches_glob_handles_backslashes():
    """Windows paths get normalised to forward slashes for fnmatch."""
    assert _path_matches_glob("src\\composables\\queries\\foo.ts", "src/composables/**/*.ts")


def test_path_matches_glob_strict():
    assert _path_matches_glob("src/foo.py", "src/*.py")
    assert not _path_matches_glob("lib/foo.py", "src/*.py")


# ---- annotate_with_suppression — the full layered resolver -----------------


def _make_finding(task_id: str, location: str, name: str = "fn") -> dict:
    return {
        "task_id": task_id,
        "location": location,
        "symbol_name": name,
        "confidence": "high",
    }


def test_annotate_no_sources_no_suppression(tmp_path):
    findings = [_make_finding("io-in-loop", "src/foo.py:10")]
    out, count = annotate_with_suppression(findings, command="math", project_root=tmp_path)
    assert count == 0
    assert out[0].get("suppressed") is None
    # finding_id is always stamped:
    assert "finding_id" in out[0]


def test_annotate_per_finding_suppression(tmp_path):
    """suppressions.json keyed by finding_id wins."""
    finding = _make_finding("io-in-loop", "src/foo.py:10")
    fid = finding_id(finding["task_id"], finding["location"], finding["symbol_name"])
    sup_dir = tmp_path / ".roam"
    sup_dir.mkdir()
    sup_path = sup_dir / "suppressions.json"
    sup_path.write_text(_json.dumps({fid: {"reason": "verified manually"}}), encoding="utf-8")

    out, count = annotate_with_suppression([finding], command="math", project_root=tmp_path)
    assert count == 1
    assert out[0]["suppressed"]["source"] == "suppressions.json"
    assert out[0]["suppressed"]["reason"] == "verified manually"


def test_annotate_ignore_findings_glob(tmp_path):
    """`.roamignore-findings` rules match by task_id + path glob."""
    rules = """
rules:
  - task_id: io-in-loop
    path_glob: "src/composables/**/*.ts"
    reason: "TanStack queries are cache reads"
"""
    (tmp_path / ".roamignore-findings").write_text(rules, encoding="utf-8")
    inside = _make_finding("io-in-loop", "src/composables/queries/foo.ts:20")
    outside = _make_finding("io-in-loop", "src/services/api.ts:5")
    out, count = annotate_with_suppression([inside, outside], command="math", project_root=tmp_path)
    assert count == 1
    assert out[0].get("suppressed", {}).get("source") == ".roamignore-findings"
    assert out[1].get("suppressed") is None


def test_annotate_inline_annotation(tmp_path):
    """Inline `# roam: ignore-math[task-id]` on the source line wins (lowest priority but most local)."""
    src = tmp_path / "src" / "foo.py"
    src.parent.mkdir(parents=True)
    src.write_text("def foo():\n    return items[0]  # roam: ignore-math[sort-to-select]\n    return items[1]\n")
    finding = _make_finding("sort-to-select", "src/foo.py:2")
    out, count = annotate_with_suppression([finding], command="math", project_root=tmp_path)
    assert count == 1
    assert out[0]["suppressed"]["source"] == "inline-annotation"


def test_filter_suppressed_drops_them():
    findings = [
        {"task_id": "x", "suppressed": {"source": "a"}},
        {"task_id": "y"},
    ]
    out = filter_suppressed(findings)
    assert len(out) == 1
    assert out[0]["task_id"] == "y"


# ---- `roam suppress` CLI ----------------------------------------------------


def test_cli_suppress_add_then_list(tmp_path):
    from roam.cli import cli

    runner = CliRunner()
    sup_path = tmp_path / "sup.json"
    add = runner.invoke(cli, ["suppress", "abc123def456abcd", "--reason", "verified", "--input", str(sup_path)])
    assert add.exit_code == 0, add.output
    assert sup_path.exists()
    data = _json.loads(sup_path.read_text(encoding="utf-8"))
    assert "abc123def456abcd" in data
    assert data["abc123def456abcd"]["reason"] == "verified"

    listed = runner.invoke(cli, ["suppress", "_", "--list", "--input", str(sup_path)])
    assert listed.exit_code == 0
    assert "1 suppression" in listed.output
    assert "abc123def456abcd" in listed.output


def test_cli_suppress_remove(tmp_path):
    from roam.cli import cli

    runner = CliRunner()
    sup_path = tmp_path / "sup.json"
    runner.invoke(cli, ["suppress", "abc", "--reason", "x", "--input", str(sup_path)])
    rm = runner.invoke(cli, ["suppress", "abc", "--remove", "--input", str(sup_path)])
    assert rm.exit_code == 0
    assert "removed abc" in rm.output
    assert _json.loads(sup_path.read_text()) == {}


def test_cli_suppress_remove_nonexistent_is_noop(tmp_path):
    from roam.cli import cli

    runner = CliRunner()
    rm = runner.invoke(cli, ["suppress", "nope", "--remove", "--input", str(tmp_path / "x.json")])
    assert rm.exit_code == 0
    assert "no-op" in rm.output


def test_cli_suppress_add_requires_reason(tmp_path):
    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["suppress", "abc", "--input", str(tmp_path / "x.json")])
    assert result.exit_code != 0
    assert "--reason" in result.output


def test_cli_suppress_json_mode(tmp_path):
    from roam.cli import cli

    runner = CliRunner()
    sup_path = tmp_path / "sup.json"
    result = runner.invoke(
        cli,
        ["--json", "suppress", "abc", "--reason", "test", "--input", str(sup_path)],
    )
    assert result.exit_code == 0
    env = _json.loads(result.output)
    assert env["summary"]["verdict"].startswith("suppressed")
    assert env["entry"]["reason"] == "test"
