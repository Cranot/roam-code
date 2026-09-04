"""Mechanical prose-documentation drift checks for ``roam doc-drift``."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process, invoke_cli


def _make_project(
    tmp_path: Path,
    *,
    docs: dict[str, str] | None = None,
    functions: int = 1,
    version: str = "1.2.3",
    extra_files: dict[str, str] | None = None,
    gitignore: str = "",
) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "src").mkdir()
    function_source = "\n\n".join(f"def function_{index}():\n    return {index}" for index in range(functions))
    (project / "src" / "app.py").write_text(function_source + "\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "fixture-project"\nversion = "{version}"\nrequires-python = ">=3.10"\n',
        encoding="utf-8",
    )
    (project / ".gitignore").write_text(f".roam/\n{gitignore}", encoding="utf-8")

    for relative, content in (docs or {}).items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for relative, content in (extra_files or {}).items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    output, exit_code = index_in_process(project)
    assert exit_code == 0, output
    return project


def _invoke_json(cli_runner, project: Path, *args: str):
    result = invoke_cli(cli_runner, ["doc-drift", *args], cwd=project, json_mode=True)
    payload = json.loads(result.output)
    return result, payload


def _finding(payload: dict, claim_text: str) -> dict:
    return next(item for item in payload["findings"] if item["claim_text"] == claim_text)


def test_path_claims_report_existing_and_missing_targets(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        docs={"README.md": "Use `src/app.py` and src/missing.py.\n"},
    )

    result, payload = _invoke_json(cli_runner, project)

    assert result.exit_code == 0
    assert _finding(payload, "src/app.py")["status"] == "verified"
    missing = _finding(payload, "src/missing.py")
    assert missing["status"] == "drifted"
    assert missing["expected"] == "path exists"
    assert missing["actual"] == "path missing"


def test_count_claims_compare_exact_plus_and_approximate_qualifiers(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        functions=11,
        docs={
            "README.md": (
                "The index has 11 functions.\n"
                "The index has 11 indexed functions.\n"
                "An old note says 12 functions.\n"
                "The project has 10+ functions.\n"
                "The project has ~10 functions.\n"
            )
        },
    )

    result, payload = _invoke_json(cli_runner, project)

    assert result.exit_code == 0
    assert _finding(payload, "11 functions")["status"] == "verified"
    assert _finding(payload, "12 functions")["status"] == "drifted"
    assert _finding(payload, "10+ functions")["status"] == "verified"
    assert _finding(payload, "~10 functions")["status"] == "verified"
    assert _finding(payload, "11 indexed functions")["status"] == "verified"
    for text in ("11 functions", "11 indexed functions", "12 functions", "10+ functions", "~10 functions"):
        assert _finding(payload, text)["metric_definition"] == "indexed symbols whose kind is 'function'"


def test_path_claims_resolve_document_relative_siblings_and_keep_ignore_policy(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        docs={"docs/README.md": "Use `examples/demo.py`, `private/result.json`, and `src/app.py`.\n"},
        extra_files={"docs/examples/demo.py": "pass\n", "docs/private/result.json": "{}\n"},
        gitignore="docs/private/\n",
    )

    result, payload = _invoke_json(cli_runner, project, "--ci")

    assert result.exit_code == 0
    sibling = _finding(payload, "examples/demo.py")
    assert sibling["status"] == "verified"
    assert sibling["resolved_path"] == "docs/examples/demo.py"
    assert _finding(payload, "src/app.py")["resolved_path"] == "src/app.py"
    assert _finding(payload, "private/result.json")["status"] == "unverifiable"


def test_unresolvable_count_noun_is_unverifiable_and_ci_passes(tmp_path, cli_runner):
    project = _make_project(tmp_path, docs={"README.md": "The project has 7 widgets.\n"})

    result, payload = _invoke_json(cli_runner, project, "--ci")

    assert result.exit_code == 0
    finding = _finding(payload, "7 widgets")
    assert finding["status"] == "unverifiable"
    assert "resolver" in finding["reason"]
    assert payload["summary"]["drifted"] == 0
    assert payload["summary"]["partial_success"] is True


def test_fenced_code_blocks_are_excluded_from_all_claims(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        docs={"README.md": ("```text\nsrc/not-real.py\n999 functions\nproject version v9.9.9\n```\n")},
    )

    result, payload = _invoke_json(cli_runner, project, "--ci")

    assert result.exit_code == 0
    assert payload["summary"]["claims_total"] == 0
    assert payload["findings"] == []
    assert "zero claims extracted" in payload["summary"]["verdict"]


@pytest.mark.parametrize(
    "text",
    [
        "The product supports 28 languages.\n",
        "There are 28 languages supported.\n",
        "28 supported languages.\n",
        "The API contains 99 public symbols.\n",
        "For example, a project has 99 functions.\n",
        "Example: 99 functions.\n",
        "An illustration, e.g. 99 functions.\n",
        "Sample output: 99 functions.\n",
        "The response is `99 functions`.\n",
    ],
)
def test_scoped_counts_are_disclosed_without_comparing_index_totals(tmp_path, cli_runner, text):
    project = _make_project(tmp_path, docs={"README.md": text})

    result, payload = _invoke_json(cli_runner, project, "--ci")

    assert result.exit_code == 0
    assert payload["summary"]["drifted"] == 0
    assert payload["summary"]["partial_success"] is True
    counts = [item for item in payload["findings"] if item["kind"] == "count"]
    assert len(counts) == 1
    assert counts[0]["status"] == "unverifiable"
    assert counts[0]["actual"] is None
    assert counts[0]["reason"]


def test_tilde_fences_and_mismatched_closers_do_not_create_claims(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        docs={"README.md": "~~~text\n999 functions\n```\nsrc/missing.py\n~~~\nThe index has 1 functions.\n"},
    )

    result, payload = _invoke_json(cli_runner, project, "--ci")

    assert result.exit_code == 0
    assert payload["summary"]["claims_total"] == 1
    assert _finding(payload, "1 functions")["status"] == "verified"


def test_example_on_same_line_does_not_hide_separate_repository_claims(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        docs={"README.md": "The index has 99 functions. For example, 3 functions. The index has 98 functions.\n"},
    )

    result, payload = _invoke_json(cli_runner, project, "--ci")

    assert result.exit_code == 5
    assert _finding(payload, "99 functions")["status"] == "drifted"
    assert _finding(payload, "98 functions")["status"] == "drifted"
    assert _finding(payload, "3 functions")["status"] == "unverifiable"


def test_doc_discovery_prunes_ignored_trees_and_preserves_reincluded_docs(tmp_path, monkeypatch):
    from roam.commands import cmd_doc_drift as mod

    project = _make_project(
        tmp_path,
        docs={"README.md": "Read the guide.\n", "docs/keep.md": "Public guide.\n"},
        extra_files={".venv/nested/private.md": "999 functions\n", "docs/skip.md": "999 functions\n"},
        gitignore=".venv/\ndocs/*\n!docs/keep.md\n",
    )
    walked = []
    real_walk = mod.os.walk

    def record_walk(*args, **kwargs):
        for entry in real_walk(*args, **kwargs):
            walked.append(Path(entry[0]).relative_to(project).as_posix())
            yield entry

    with monkeypatch.context() as patch:
        patch.setattr(mod.os, "walk", record_walk)
        paths, errors, unknown = mod._discover_docs(project, mod._GitIgnore(project))

    assert {path.relative_to(project).as_posix() for path in paths} == {"README.md", "docs/keep.md"}
    assert not any(path.startswith(".venv") for path in walked)
    assert errors == unknown == []


def test_gitignored_path_claim_is_unverifiable_not_drifted(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        docs={"README.md": "Generated data lives at generated/output.json.\n"},
        extra_files={"generated/output.json": "{}\n"},
        gitignore="generated/\n",
    )

    result, payload = _invoke_json(cli_runner, project, "--ci")

    assert result.exit_code == 0
    finding = _finding(payload, "generated/output.json")
    assert finding["status"] == "unverifiable"
    assert "gitignored" in finding["reason"]
    assert payload["summary"]["drifted"] == 0


def test_version_claim_compares_with_project_metadata(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        version="1.2.3",
        docs={"README.md": "Project version v9.9.9 is documented here.\n"},
    )

    result, payload = _invoke_json(cli_runner, project)

    assert result.exit_code == 0
    finding = _finding(payload, "version v9.9.9")
    assert finding["kind"] == "version"
    assert finding["expected"] == "9.9.9"
    assert finding["actual"] == "1.2.3"
    assert finding["status"] == "drifted"


def test_changelog_and_release_notes_are_not_scanned(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        docs={
            "README.md": "No concrete claims here.\n",
            "CHANGELOG.md": "Old version v0.0.1 and src/deleted.py.\n",
            "docs/release-notes.md": "Historical version v0.0.2.\n",
        },
    )

    result, payload = _invoke_json(cli_runner, project, "--ci")

    assert result.exit_code == 0
    assert payload["summary"]["docs_scanned"] == 1
    assert payload["summary"]["claims_total"] == 0


def test_ci_refuses_zero_docs_with_nonempty_json_envelope(tmp_path, cli_runner):
    project = _make_project(tmp_path)

    result, payload = _invoke_json(cli_runner, project, "--ci")

    assert result.exit_code == 5
    assert result.output.strip()
    assert payload["command"] == "doc-drift"
    assert payload["summary"]["docs_scanned"] == 0
    assert payload["summary"]["state"] == "no_docs_scanned"
    assert payload["summary"]["gate_passed"] is False
    assert "refused" in payload["summary"]["verdict"].lower()


def test_precision_guards_skip_urls_globs_and_placeholder_segments(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        docs={
            "README.md": (
                "See https://example.test/docs/missing.md.\n"
                "Use `docs/*.md`, `src/<module>.py`, and `src/example.py`.\n"
                "A bare semantic-release number 9.9.9 is not a claim.\n"
            )
        },
    )

    result, payload = _invoke_json(cli_runner, project, "--ci")

    assert result.exit_code == 0
    assert payload["summary"]["claims_total"] == 0


def test_sarif_projects_only_drifted_claims_to_doc_locations(tmp_path, cli_runner):
    project = _make_project(
        tmp_path,
        docs={"README.md": "Use `src/app.py`.\nUse src/missing.py.\n"},
    )

    result = invoke_cli(cli_runner, ["doc-drift"], cwd=project, json_mode=False)
    assert result.exit_code == 0

    from roam.cli import cli

    old_cwd = Path.cwd()
    try:
        import os

        os.chdir(project)
        sarif_result = cli_runner.invoke(cli, ["--sarif", "doc-drift"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert sarif_result.exit_code == 0
    sarif = json.loads(sarif_result.output)
    results = sarif["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["ruleId"] == "doc-drift/path"
    location = results[0]["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "README.md"
    assert location["region"]["startLine"] == 2


@pytest.mark.parametrize("gate_args", [("--ci",), ("--threshold", "0")])
def test_gate_fails_only_when_objective_drift_exceeds_limit(tmp_path, cli_runner, gate_args):
    project = _make_project(tmp_path, docs={"README.md": "Use src/missing.py.\n"})

    result, payload = _invoke_json(cli_runner, project, *gate_args)

    assert result.exit_code == 5
    assert payload["summary"]["drifted"] == 1
    assert payload["summary"]["gate_passed"] is False
