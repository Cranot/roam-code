"""W1036 — Pattern 2 (silent fallback) tests for ``roam.rules.engine``.

Drives the ``warnings_out`` accumulator plumbed in W1036 through
:func:`roam.rules.engine._load_yaml` / :func:`load_rules` /
:func:`evaluate_all`, and through the ``roam rules`` Click command's
envelope. Mirrors the W1051 (cmd_fitness) + W1052 (cmd_health)
sibling migrations.

Cross-links:
- W706 — canonical ``warnings_out`` plumb-through for
  ``_load_ignore_findings_file``.
- W1019d — sibling migration for ``cmd_check_rules`` (the
  ``.roam-rules.yml`` config loader).
- W1051 — sibling migration for ``cmd_fitness._load_rules``.
- W1052 — sibling migration for ``cmd_health._load_gate_config``.
- ``src/roam/commands/_yaml_loader.py`` — the shared helper.
- ``(internal memo)`` — the survey + rationale.
- CLAUDE.md "Six systemic anti-patterns" / Pattern 2 "Silent fallback".
"""

from __future__ import annotations

import json as _json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli as roam_cli
from roam.rules.engine import _load_yaml, load_rules

# ---------------------------------------------------------------------------
# _load_yaml — direct loader behaviour
# ---------------------------------------------------------------------------


def test_load_yaml_missing_file_no_warning(tmp_path: Path) -> None:
    """Absent file is the default state — never warn (would spam every run)."""
    warnings_out: list[str] = []
    data = _load_yaml(tmp_path / "missing.yaml", warnings_out=warnings_out)
    assert data is None
    assert warnings_out == []


def test_load_yaml_valid_no_warning(tmp_path: Path) -> None:
    """Happy path: well-formed file, no warnings emitted, all keys returned."""
    p = tmp_path / "rule.yaml"
    p.write_text(
        'name: "No cycles"\nseverity: error\nmatch:\n  kind: function\n',
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    data = _load_yaml(p, warnings_out=warnings_out)
    assert warnings_out == []
    assert isinstance(data, dict)
    assert data["name"] == "No cycles"
    assert data["severity"] == "error"


def test_load_yaml_malformed_warns(tmp_path: Path) -> None:
    """Truly malformed YAML — the helper appends a warning naming the file."""
    p = tmp_path / "rule.yaml"
    # Unbalanced bracket / unparseable in both PyYAML and the tiny parser.
    p.write_text("name: bad\nmatch: [unterminated\n", encoding="utf-8")
    warnings_out: list[str] = []
    data = _load_yaml(p, warnings_out=warnings_out)
    assert data is None
    assert len(warnings_out) >= 1
    msg = warnings_out[0]
    assert "rules-yaml" in msg
    assert "rule.yaml" in msg


def test_load_yaml_top_level_list_returns_list(tmp_path: Path) -> None:
    """Top-level list is a wrong-shape signal — surfaces via the
    list-root branch (helper accepts it, downstream load_rules turns
    it into an `_error` placeholder).
    """
    p = tmp_path / "rule.yaml"
    p.write_text("- one\n- two\n- three\n", encoding="utf-8")
    warnings_out: list[str] = []
    data = _load_yaml(p, warnings_out=warnings_out)
    # The helper accepts list-root via allow_list_root=True, so no warning
    # is emitted at the helper layer; load_rules surfaces this downstream
    # as an `_error` placeholder rule (see test_load_rules_top_level_list_*).
    assert isinstance(data, list)
    assert warnings_out == []


def test_load_yaml_warnings_out_none_byte_identical(tmp_path: Path) -> None:
    """Pre-W1036 callers (no accumulator) get the legacy None on parse
    failure and a dict on the happy path.
    """
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: bad\nmatch: [unterminated\n", encoding="utf-8")
    assert _load_yaml(bad) is None

    good = tmp_path / "good.yaml"
    good.write_text("name: ok\nseverity: warning\n", encoding="utf-8")
    out = _load_yaml(good)
    assert isinstance(out, dict)
    assert out["name"] == "ok"


# ---------------------------------------------------------------------------
# load_rules — directory-level behaviour with placeholders for parse errors
# ---------------------------------------------------------------------------


def test_load_rules_happy_path_no_warnings(tmp_path: Path) -> None:
    """Multi-file directory with all-valid rules — no warnings."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "rule1.yaml").write_text('name: "Rule One"\nseverity: error\n', encoding="utf-8")
    (rules_dir / "rule2.yml").write_text('name: "Rule Two"\nseverity: warning\n', encoding="utf-8")
    warnings_out: list[str] = []
    rules = load_rules(rules_dir, warnings_out=warnings_out)
    assert len(rules) == 2
    assert warnings_out == []


def test_load_rules_one_bad_one_good(tmp_path: Path) -> None:
    """A bad file produces a structured warning AND an `_error` placeholder
    rule; the good file still loads cleanly.
    """
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "bad.yaml").write_text("name: bad\nmatch: [unterminated\n", encoding="utf-8")
    (rules_dir / "good.yaml").write_text('name: "OK"\nseverity: error\n', encoding="utf-8")
    warnings_out: list[str] = []
    rules = load_rules(rules_dir, warnings_out=warnings_out)
    # One warning from the bad file; the good file passes silently.
    assert len(warnings_out) >= 1
    assert any("bad.yaml" in w for w in warnings_out)
    # Both files produce a rule record (one `_error` placeholder, one real).
    assert len(rules) == 2
    by_name = {r.get("name"): r for r in rules}
    assert "bad.yaml" in by_name  # placeholder uses filename as name
    assert by_name["bad.yaml"].get("_error", "").startswith("failed to parse")
    assert by_name["OK"]["severity"] == "error"


def test_load_rules_top_level_list_is_error_placeholder(tmp_path: Path) -> None:
    """A top-level list is a wrong-root-shape error; loader surfaces it as
    an `_error` placeholder so the rules envelope shows which file failed.
    """
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "listroot.yaml").write_text("- one\n- two\n- three\n", encoding="utf-8")
    warnings_out: list[str] = []
    rules = load_rules(rules_dir, warnings_out=warnings_out)
    assert len(rules) == 1
    assert rules[0]["name"] == "listroot.yaml"
    assert "top-level list" in rules[0]["_error"]


def test_load_rules_warnings_out_none_byte_identical(tmp_path: Path) -> None:
    """Pre-W1036 callers (no accumulator) still get the legacy `_error`
    placeholder for unparseable files and a real rule for valid files.
    """
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "bad.yaml").write_text("name: bad\nmatch: [unterminated\n", encoding="utf-8")
    (rules_dir / "good.yaml").write_text('name: "OK"\nseverity: warning\n', encoding="utf-8")
    rules = load_rules(rules_dir)
    by_name = {r.get("name"): r for r in rules}
    assert "_error" in by_name["bad.yaml"]
    assert by_name["OK"]["severity"] == "warning"


# ---------------------------------------------------------------------------
# No-PyYAML fallback path
# ---------------------------------------------------------------------------


def test_load_yaml_no_pyyaml_json_falls_back_cleanly(
    tmp_path: Path,
    no_pyyaml: None,
) -> None:
    """If PyYAML import fails, JSON-shaped file still loads cleanly."""
    p = tmp_path / "rule.yaml"
    p.write_text(
        _json.dumps({"name": "from-json", "severity": "error"}),
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    data = _load_yaml(p, warnings_out=warnings_out)
    assert warnings_out == []
    assert isinstance(data, dict)
    assert data["name"] == "from-json"


def test_load_yaml_no_pyyaml_tiny_parser_handles_yaml(
    tmp_path: Path,
    no_pyyaml: None,
) -> None:
    """Without PyYAML, the tiny parser still loads simple key:value YAML."""
    p = tmp_path / "rule.yaml"
    p.write_text("name: tiny-parser-ok\nseverity: error\n", encoding="utf-8")
    warnings_out: list[str] = []
    data = _load_yaml(p, warnings_out=warnings_out)
    assert warnings_out == []
    assert isinstance(data, dict)
    assert data["name"] == "tiny-parser-ok"


# ---------------------------------------------------------------------------
# Click integration — envelope surfaces accumulated warnings
# ---------------------------------------------------------------------------


def _setup_git_repo(cwd: Path) -> None:
    """Initialise a minimal git repo so ``roam init`` accepts the tree."""
    subprocess.run(["git", "init", "-q", str(cwd)], check=False)
    subprocess.run(["git", "-C", str(cwd), "config", "user.email", "t@t"], check=False)
    subprocess.run(["git", "-C", str(cwd), "config", "user.name", "t"], check=False)


def _git_add_commit(cwd: Path) -> None:
    subprocess.run(["git", "-C", str(cwd), "add", "-A"], check=False)
    subprocess.run(["git", "-C", str(cwd), "commit", "-m", "init", "-q"], check=False)


def _extract_envelope(output: str) -> dict:
    """Pull the trailing JSON envelope out of mixed stdout."""
    lines = output.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].startswith("{"):
            return _json.loads("\n".join(lines[idx:]))
    raise AssertionError(f"no JSON envelope found in output:\n{output}")


def test_rules_envelope_surfaces_warnings_on_malformed_rule_file(
    tmp_path: Path,
) -> None:
    """End-to-end: malformed .roam/rules/*.yaml surfaces in envelope warnings_out."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        cwd_p = Path(cwd)
        _setup_git_repo(cwd_p)
        # Minimal Python source so init has something to index.
        (cwd_p / "sample.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
        # Malformed rule file under .roam/rules/.
        rules_dir = cwd_p / ".roam" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "bad.yaml").write_text("name: bad\nmatch: [unterminated\n", encoding="utf-8")
        _git_add_commit(cwd_p)
        runner.invoke(roam_cli, ["init"])
        result = runner.invoke(roam_cli, ["--json", "rules"])
        # Exit may be 0 or 1 depending on whether the placeholder _error
        # rule is treated as a CI failure.
        assert result.exit_code in (0, 1), result.output
        payload = _extract_envelope(result.output)
        summary = payload.get("summary", {})
        warnings_out = summary.get("warnings_out", [])
        assert isinstance(warnings_out, list)
        assert any("bad.yaml" in w for w in warnings_out), warnings_out
        assert summary.get("partial_success") is True


def test_rules_envelope_clean_on_well_formed_rules(tmp_path: Path) -> None:
    """Happy path: well-formed rule files produce no warnings on the envelope."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        cwd_p = Path(cwd)
        _setup_git_repo(cwd_p)
        (cwd_p / "sample.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
        rules_dir = cwd_p / ".roam" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "ok.yaml").write_text(
            'name: "Some rule"\nseverity: warning\nmatch:\n  kind: function\n',
            encoding="utf-8",
        )
        _git_add_commit(cwd_p)
        runner.invoke(roam_cli, ["init"])
        result = runner.invoke(roam_cli, ["--json", "rules"])
        assert result.exit_code in (0, 1), result.output
        payload = _extract_envelope(result.output)
        summary = payload.get("summary", {})
        warnings_out = summary.get("warnings_out", [])
        assert warnings_out == [], warnings_out
