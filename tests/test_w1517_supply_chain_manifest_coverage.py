"""W1517 — an unreadable manifest left the DENOMINATOR and IMPROVED the score.

Nine identical ``except OSError: return deps`` blocks, one per manifest parser,
returned an empty list that ``discover_and_parse`` could not tell apart from a
manifest declaring nothing.  ``compute_risk_score`` then computed pin coverage
over whatever survived, and ``files_scanned`` was derived from
``{d.source_file for d in deps}`` — so an unreadable manifest was not merely
uncounted, it was absent from the file list entirely and no reader could tell it
had ever existed.  Measured: a repo with 2 exact + 2 range pins in
``requirements.txt`` and one ``"*"`` in ``package.json`` scores 40/100 at 40% pin
coverage with 1 unpinned; denying read on ``package.json`` makes it 50/100 at 50%
with 0 unpinned, and the recommendation flips to "Dependency pinning looks good".

The must-not-fire half carries the weight.  A legitimately EMPTY manifest — an
empty ``requirements.txt``, a ``package.json`` with no ``dependencies`` key, a
``pyproject.toml`` carrying only ``[build-system]`` — parses to zero dependencies
and is extremely common.  Keying incompleteness on emptiness rather than on the
error would make ordinary repos report a partial scan.
"""

from __future__ import annotations

import json

from tests.conftest import git_init, index_in_process, invoke_cli, parse_json_output


def _make_repo(tmp_path, files):
    proj = tmp_path / "sc_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    for rel, content in files.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
    git_init(proj)
    index_in_process(proj)
    return proj


_REQS = "requests==2.31.0\nflask==3.0.0\ndjango>=4.2\ncelery>=5.3\n"
_PKG = json.dumps({"dependencies": {"left-pad": "*"}}, indent=2) + "\n"


def _deny(monkeypatch, suffix):
    """Make ``Path.read_text`` raise for one manifest, as an ACL deny would."""
    from pathlib import Path as _Path

    real = _Path.read_text

    def _refusing(self, *args, **kwargs):
        if str(self).replace("\\", "/").endswith(suffix):
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "read_text", _refusing)


# ---------------------------------------------------------------------------
# MUST FIRE
# ---------------------------------------------------------------------------


def test_baseline_counts_both_manifests(cli_runner, tmp_path, monkeypatch):
    proj = _make_repo(tmp_path, {"requirements.txt": _REQS, "package.json": _PKG})
    monkeypatch.chdir(proj)
    summary = parse_json_output(invoke_cli(cli_runner, ["supply-chain"], cwd=proj, json_mode=True))["summary"]

    assert summary["total_dependencies"] == 5
    assert summary["unpinned_count"] == 1
    assert summary["manifests_read"] == summary["manifests_discovered"] == 2
    assert summary["scan_incomplete"] is False
    assert set(summary["files_scanned"]) == {"requirements.txt", "package.json"}


def test_unreadable_manifest_is_disclosed_not_dropped(cli_runner, tmp_path, monkeypatch):
    proj = _make_repo(tmp_path, {"requirements.txt": _REQS, "package.json": _PKG})
    monkeypatch.chdir(proj)
    _deny(monkeypatch, "package.json")

    result = invoke_cli(cli_runner, ["supply-chain"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, "supply-chain has no gate flag; the exit code must not move"
    summary = parse_json_output(result)["summary"]

    assert summary["scan_incomplete"] is True
    assert summary["partial_success"] is True
    assert summary["manifests_discovered"] == 2
    assert summary["manifests_read"] == 1
    assert summary["manifests_unreadable"] == 1

    # The manifest must remain visible: previously it vanished from the list.
    assert any("package.json" in f for f in summary["files_scanned"]), summary["files_scanned"]
    assert any("NOT READ" in f for f in summary["files_scanned"]), summary["files_scanned"]

    gaps = summary["manifests_not_measured"]
    assert gaps and gaps[0]["file"] == "package.json"
    assert gaps[0]["kind"] == "unreadable"
    assert "PermissionError" in gaps[0]["error"]

    # The headline number is still published, but it must no longer read as a
    # whole-project score.
    assert "NOT a whole-project score" in summary["verdict"]
    assert "1 of 2 manifests" in summary["verdict"]


def test_text_channel_carries_the_same_disclosure(cli_runner, tmp_path, monkeypatch):
    proj = _make_repo(tmp_path, {"requirements.txt": _REQS, "package.json": _PKG})
    monkeypatch.chdir(proj)
    _deny(monkeypatch, "package.json")

    result = invoke_cli(cli_runner, ["supply-chain"], cwd=proj, json_mode=False)
    assert result.exit_code == 0, result.output
    out = getattr(result, "stdout", None) or result.output

    assert "were NOT measured" in out
    assert "package.json (NOT READ)" in out
    assert "NOT a whole-project score" in out


def test_malformed_manifest_is_unparseable_not_absent(tmp_path):
    """A manifest that opens but does not parse is also not 'declares none'."""
    from roam.commands.cmd_supply_chain import discover_and_parse_status

    proj = tmp_path / "bad_json"
    proj.mkdir()
    (proj / "requirements.txt").write_text(_REQS)
    (proj / "package.json").write_text("{ this is not json ]")

    deps, coverage = discover_and_parse_status(proj)
    assert len(deps) == 4
    assert coverage.scan_incomplete is True
    assert [g["kind"] for g in coverage.gaps] == ["unparseable"]
    assert coverage.gaps[0]["file"] == "package.json"


# ---------------------------------------------------------------------------
# MUST NOT FIRE
# ---------------------------------------------------------------------------


def test_manifest_declaring_zero_dependencies_is_a_clean_measurement(tmp_path):
    """Empty / dependency-free manifests must stay OUT of the gap set.

    This is the over-eager-fix guard named in the finding: keying on
    ``len(parsed) == 0`` would make an ordinary Python repo with a bare
    ``pyproject.toml`` report an incomplete scan.
    """
    from roam.commands.cmd_supply_chain import discover_and_parse_status

    proj = tmp_path / "empty_manifests"
    proj.mkdir()
    (proj / "requirements.txt").write_text("")
    (proj / "package.json").write_text('{"name": "x", "version": "1.0.0"}')
    (proj / "pyproject.toml").write_text('[build-system]\nrequires = ["setuptools"]\n')

    deps, coverage = discover_and_parse_status(proj)
    assert deps == []
    assert coverage.gaps == [], f"a manifest that declares nothing is not a gap: {coverage.gaps}"
    assert coverage.scan_incomplete is False
    assert len(coverage.read) == len(coverage.discovered) == 3


def test_repo_with_no_manifests_stays_a_clean_100(cli_runner, tmp_path, monkeypatch):
    """Zero discovered manifests is a legitimate clean run, not an incomplete one."""
    proj = _make_repo(tmp_path, {"app.py": "def main():\n    return 1\n"})
    monkeypatch.chdir(proj)

    summary = parse_json_output(invoke_cli(cli_runner, ["supply-chain"], cwd=proj, json_mode=True))["summary"]
    assert summary["manifests_discovered"] == 0
    assert summary["scan_incomplete"] is False
    assert summary.get("partial_success", False) is False
    assert "NOT a whole-project score" not in summary["verdict"]


def test_complete_scan_leaves_the_numbers_byte_identical(cli_runner, tmp_path, monkeypatch):
    """Everything above fires only when manifests_read < manifests_discovered."""
    proj = _make_repo(tmp_path, {"requirements.txt": _REQS, "package.json": _PKG})
    monkeypatch.chdir(proj)

    summary = parse_json_output(invoke_cli(cli_runner, ["supply-chain"], cwd=proj, json_mode=True))["summary"]
    assert summary["verdict"] == "Supply chain risky (40/100) -- 1 unpinned dependencies"
    assert summary["risk_score"] == 40
    assert summary["pin_coverage_pct"] == 40.0
    assert summary["files_scanned"] == ["package.json", "requirements.txt"]

    text = invoke_cli(cli_runner, ["supply-chain"], cwd=proj, json_mode=False)
    out = getattr(text, "stdout", None) or text.output
    assert "NOT READ" not in out
    assert "were NOT measured" not in out


def test_discover_and_parse_wrapper_is_unchanged(tmp_path):
    """The list-returning wrapper stays intact so cmd_sbom is not broken."""
    from roam.commands.cmd_supply_chain import discover_and_parse, discover_and_parse_status

    proj = tmp_path / "wrapper_proj"
    proj.mkdir()
    (proj / "requirements.txt").write_text(_REQS)

    assert discover_and_parse(proj) == discover_and_parse_status(proj)[0]


def test_sarif_discloses_an_incomplete_manifest_census(cli_runner, tmp_path, monkeypatch):
    """A Code-Scanning consumer must not read a partial census as complete."""
    proj = _make_repo(tmp_path, {"requirements.txt": _REQS, "package.json": _PKG})
    monkeypatch.chdir(proj)
    _deny(monkeypatch, "package.json")

    result = invoke_cli(cli_runner, ["supply-chain", "--sarif"], cwd=proj, json_mode=False)
    assert result.exit_code == 0, result.output
    out = getattr(result, "stdout", None) or result.output
    doc = json.loads(out[: out.rindex("}") + 1])
    notifications = doc["runs"][0].get("invocations", [{}])[0].get("toolExecutionNotifications", [])
    blob = json.dumps(notifications)
    assert "supply_chain_scan_incomplete" in blob, blob


def test_sarif_on_a_complete_scan_carries_no_disclosure(cli_runner, tmp_path, monkeypatch):
    """Negative control: an uncapped, fully-read run's SARIF is untouched."""
    proj = _make_repo(tmp_path, {"requirements.txt": _REQS, "package.json": _PKG})
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["supply-chain", "--sarif"], cwd=proj, json_mode=False)
    assert result.exit_code == 0, result.output
    out = getattr(result, "stdout", None) or result.output
    doc = json.loads(out[: out.rindex("}") + 1])
    assert "invocations" not in doc["runs"][0], "a complete scan must add nothing"
