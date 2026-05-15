"""W1019d — Pattern 2 (silent fallback) tests for `cmd_check_rules`.

Drives the `warnings_out` accumulator plumbed through W1019d into the
three sub-loaders (`_load_raw_config`, `_load_user_config`,
`_load_config_profile`) and the Click command's envelope.

Mirrors the W706 / W918 / W989 disciplines: every silent-fallback path
surfaces a structured warning when an accumulator is supplied; when no
accumulator is supplied, behaviour is byte-identical to pre-W1019d
(silent empty container).

Cross-links:
- W1019a — the pilot migration (finding_suppress).
- ``src/roam/commands/_yaml_loader.py`` — the shared helper.
- CLAUDE.md "Six systemic anti-patterns" / Pattern 2 "Silent fallback".
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli as roam_cli
from roam.commands.cmd_check_rules import (
    _load_config_profile,
    _load_raw_config,
    _load_user_config,
)


# ---------------------------------------------------------------------------
# _load_raw_config — direct loader behaviour
# ---------------------------------------------------------------------------


def test_raw_missing_file_no_warning(tmp_path: Path) -> None:
    """Absent file is the default state — never warn."""
    warnings_out: list[str] = []
    data = _load_raw_config(str(tmp_path / "missing.yml"), warnings_out=warnings_out)
    assert data == {}
    assert warnings_out == []


def test_raw_none_path_no_warning() -> None:
    """``None`` path = no config configured, never warn."""
    warnings_out: list[str] = []
    data = _load_raw_config(None, warnings_out=warnings_out)
    assert data == {}
    assert warnings_out == []


def test_raw_valid_yaml_no_warning(tmp_path: Path) -> None:
    """Happy path: well-formed file, no warnings emitted."""
    body = (
        "profile: strict-security\n"
        "rules:\n"
        "  - id: max-fan-out\n"
        "    threshold: 5\n"
    )
    p = tmp_path / ".roam-rules.yml"
    p.write_text(body, encoding="utf-8")
    warnings_out: list[str] = []
    data = _load_raw_config(str(p), warnings_out=warnings_out)
    assert warnings_out == []
    assert isinstance(data, dict)
    assert data.get("profile") == "strict-security"


def test_raw_malformed_yaml_warns(tmp_path: Path) -> None:
    """Truly malformed YAML — the helper appends a warning naming the file."""
    p = tmp_path / ".roam-rules.yml"
    # Unmatched bracket / unparseable.
    p.write_text("rules: [unterminated\n", encoding="utf-8")
    warnings_out: list[str] = []
    data = _load_raw_config(str(p), warnings_out=warnings_out)
    assert data == {}
    # The helper produced exactly one warning; the loader did not pile a
    # second on top.
    assert len(warnings_out) == 1
    assert "roam-rules" in warnings_out[0]


def test_raw_non_dict_root_warns(tmp_path: Path) -> None:
    """List-at-root is a wrong-shape config — surface the type mismatch."""
    p = tmp_path / ".roam-rules.yml"
    p.write_text("- one\n- two\n", encoding="utf-8")
    warnings_out: list[str] = []
    data = _load_raw_config(str(p), warnings_out=warnings_out)
    assert data == {}
    assert len(warnings_out) == 1
    assert "expected a mapping" in warnings_out[0]


def test_raw_byte_identical_when_no_accumulator(tmp_path: Path) -> None:
    """Pre-W1019d callers (no accumulator) get the legacy empty-dict result."""
    p = tmp_path / ".roam-rules.yml"
    p.write_text("- malformed\n- list-root\n", encoding="utf-8")
    assert _load_raw_config(str(p)) == {}
    # And the happy path still returns the parsed dict:
    p2 = tmp_path / "ok.yml"
    p2.write_text("profile: minimal\n", encoding="utf-8")
    assert _load_raw_config(str(p2)) == {"profile": "minimal"}


# ---------------------------------------------------------------------------
# _load_user_config — list extraction + non-list disclosure
# ---------------------------------------------------------------------------


def test_user_missing_file_no_warning(tmp_path: Path) -> None:
    warnings_out: list[str] = []
    rules = _load_user_config(str(tmp_path / "missing.yml"), warnings_out=warnings_out)
    assert rules == []
    assert warnings_out == []


def test_user_valid_rules_no_warning(tmp_path: Path) -> None:
    body = (
        "rules:\n"
        "  - id: max-fan-out\n"
        "    threshold: 5\n"
        "  - id: test-file-exists\n"
        "    enabled: false\n"
    )
    p = tmp_path / ".roam-rules.yml"
    p.write_text(body, encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_user_config(str(p), warnings_out=warnings_out)
    assert warnings_out == []
    assert len(rules) == 2


def test_user_malformed_yaml_propagates_warning(tmp_path: Path) -> None:
    """File-level warning flows up; no second warning from _load_user_config."""
    p = tmp_path / ".roam-rules.yml"
    p.write_text("rules: [unterminated\n", encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_user_config(str(p), warnings_out=warnings_out)
    assert rules == []
    assert len(warnings_out) == 1
    assert "roam-rules" in warnings_out[0]


def test_user_non_list_rules_warns(tmp_path: Path) -> None:
    """`rules:` set to a mapping or scalar — surface the type mismatch."""
    p = tmp_path / ".roam-rules.yml"
    p.write_text("rules:\n  id: not-a-list\n", encoding="utf-8")
    warnings_out: list[str] = []
    rules = _load_user_config(str(p), warnings_out=warnings_out)
    assert rules == []
    assert len(warnings_out) == 1
    assert "expected a list" in warnings_out[0]


def test_user_byte_identical_when_no_accumulator(tmp_path: Path) -> None:
    """Pre-W1019d callers (no accumulator) get the legacy empty-list result."""
    p = tmp_path / ".roam-rules.yml"
    p.write_text("rules:\n  id: not-a-list\n", encoding="utf-8")
    assert _load_user_config(str(p)) == []
    # Happy path unchanged.
    p2 = tmp_path / "ok.yml"
    p2.write_text("rules:\n  - id: max-fan-out\n    threshold: 5\n", encoding="utf-8")
    rules = _load_user_config(str(p2))
    assert len(rules) == 1


# ---------------------------------------------------------------------------
# _load_config_profile — string extraction + non-string disclosure
# ---------------------------------------------------------------------------


def test_profile_missing_file_no_warning(tmp_path: Path) -> None:
    warnings_out: list[str] = []
    result = _load_config_profile(str(tmp_path / "missing.yml"), warnings_out=warnings_out)
    assert result is None
    assert warnings_out == []


def test_profile_valid_string_no_warning(tmp_path: Path) -> None:
    p = tmp_path / ".roam-rules.yml"
    p.write_text("profile: strict-security\n", encoding="utf-8")
    warnings_out: list[str] = []
    result = _load_config_profile(str(p), warnings_out=warnings_out)
    assert warnings_out == []
    assert result == "strict-security"


def test_profile_absent_key_no_warning(tmp_path: Path) -> None:
    """A rules-only config (no `profile:` key) is legitimate — never warn."""
    p = tmp_path / ".roam-rules.yml"
    p.write_text("rules:\n  - id: max-fan-out\n", encoding="utf-8")
    warnings_out: list[str] = []
    result = _load_config_profile(str(p), warnings_out=warnings_out)
    assert result is None
    assert warnings_out == []


def test_profile_non_string_warns(tmp_path: Path) -> None:
    p = tmp_path / ".roam-rules.yml"
    p.write_text("profile:\n  - listy\n", encoding="utf-8")
    warnings_out: list[str] = []
    result = _load_config_profile(str(p), warnings_out=warnings_out)
    assert result is None
    assert len(warnings_out) == 1
    assert "expected a non-empty string" in warnings_out[0]


def test_profile_byte_identical_when_no_accumulator(tmp_path: Path) -> None:
    """Pre-W1019d callers (no accumulator) get the legacy None result."""
    p = tmp_path / ".roam-rules.yml"
    p.write_text("profile:\n  - listy\n", encoding="utf-8")
    assert _load_config_profile(str(p)) is None
    # Happy path unchanged.
    p2 = tmp_path / "ok.yml"
    p2.write_text("profile: minimal\n", encoding="utf-8")
    assert _load_config_profile(str(p2)) == "minimal"


# ---------------------------------------------------------------------------
# Click integration — envelope surfaces accumulated warnings
# ---------------------------------------------------------------------------


def _setup_git_repo(cwd: Path) -> None:
    """Initialise a minimal git repo so ``roam init`` accepts the tree."""
    import subprocess

    subprocess.run(["git", "init", "-q", str(cwd)], check=False)
    subprocess.run(["git", "-C", str(cwd), "config", "user.email", "t@t"], check=False)
    subprocess.run(["git", "-C", str(cwd), "config", "user.name", "t"], check=False)


def _git_add_commit(cwd: Path) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(cwd), "add", "-A"], check=False)
    subprocess.run(["git", "-C", str(cwd), "commit", "-m", "init", "-q"], check=False)


def _extract_envelope(output: str) -> dict:
    """Pull the trailing JSON envelope out of mixed stdout.

    ``roam --json check-rules`` triggers an in-process auto-index whose
    progress lines are printed on stdout before the envelope. The
    envelope is always the last top-level ``{ ... }`` block in the
    output.
    """
    # The envelope opens at the last line that begins with `{` at column
    # 0 — every other `{` inside the envelope is indented by
    # ``to_json``'s pretty-printer.
    lines = output.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].startswith("{"):
            return _json.loads("\n".join(lines[idx:]))
    raise AssertionError(f"no JSON envelope found in output:\n{output}")


def test_envelope_surfaces_warnings_on_malformed_config(tmp_path: Path) -> None:
    """End-to-end: malformed .roam-rules.yml surfaces in envelope warnings_out."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        cwd_p = Path(cwd)
        _setup_git_repo(cwd_p)
        # Minimal Python source so init has something to index.
        (cwd_p / "sample.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
        # Malformed config — non-list rules.
        (cwd_p / ".roam-rules.yml").write_text(
            "rules:\n  id: not-a-list\n", encoding="utf-8"
        )
        _git_add_commit(cwd_p)
        runner.invoke(roam_cli, ["init"])
        result = runner.invoke(roam_cli, ["--json", "check-rules"])
        assert result.exit_code in (0, 1), result.output
        payload = _extract_envelope(result.output)
        warnings_out = payload.get("warnings_out", [])
        assert isinstance(warnings_out, list)
        assert any("expected a list" in w for w in warnings_out), warnings_out
        summary = payload.get("summary", {})
        assert summary.get("partial_success") is True


def test_envelope_clean_on_well_formed_config(tmp_path: Path) -> None:
    """Happy path: well-formed config produces no warnings on the envelope."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        cwd_p = Path(cwd)
        _setup_git_repo(cwd_p)
        (cwd_p / "sample.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
        (cwd_p / ".roam-rules.yml").write_text(
            "rules:\n  - id: max-fan-out\n    threshold: 5\n", encoding="utf-8"
        )
        _git_add_commit(cwd_p)
        runner.invoke(roam_cli, ["init"])
        result = runner.invoke(roam_cli, ["--json", "check-rules"])
        assert result.exit_code in (0, 1), result.output
        payload = _extract_envelope(result.output)
        warnings_out = payload.get("warnings_out", [])
        assert warnings_out == [], warnings_out
        summary = payload.get("summary", {})
        assert summary.get("partial_success") is not True
