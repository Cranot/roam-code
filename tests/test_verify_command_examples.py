from __future__ import annotations

from pathlib import Path

from roam.commands.cmd_verify import (
    SEVERITY_FAIL,
    SEVERITY_INFO,
    _check_command_examples,
    _extract_roam_command_examples,
)


def test_extract_roam_command_examples_from_inline_and_shell_lines():
    text = """
Run `roam verify --auto` after editing.
Reference the command name `roam compile` without treating it as an example.
This phrase is not a command: `highest-priority roam hardening lesson`.
  roam hardening lesson.
roam does NOT own cross-server policy.

```bash
$ roam compile "find callers" --artifact facts
roam preflight <symbol>
```
"""

    examples = _extract_roam_command_examples(text)

    assert [item["command"] for item in examples] == [
        "roam verify --auto",
        'roam compile "find callers" --artifact facts',
        "roam preflight <symbol>",
    ]


def test_extract_roam_command_examples_ignores_powershell_variables():
    text = """
```powershell
$roam = "$env:TEMP\\roam-smoke-venv\\Scripts\\roam.exe"
& $roam --version
```
"""

    examples = _extract_roam_command_examples(text)

    assert examples == []


def test_check_command_examples_flags_invalid_commands_and_placeholders(tmp_path: Path):
    doc = tmp_path / "README.md"
    doc.write_text(
        "\n".join(
            [
                "Run `roam verify --auto`.",
                "Run `roam verify --definitely-not-a-flag`.",
                "Run `roam preflight <symbol>`.",
                "Run `roam cycles [--actionable-only]`.",
                "Run `roam definitely-not-a-command --flag`.",
            ]
        ),
        encoding="utf-8",
    )

    result = _check_command_examples(["README.md"], tmp_path)
    violations = result["violations"]

    assert result["examples_checked"] == 5
    assert len(violations) == 4
    severities = [v["severity"] for v in violations]
    assert severities.count(SEVERITY_FAIL) == 2
    assert severities.count(SEVERITY_INFO) == 2
    assert any(v["command_check"]["target_status"] == "placeholder" for v in violations)
    assert any(v["command_check"]["registry_status"] == "unknown" for v in violations)


def test_check_command_examples_skips_non_doc_surfaces(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text('HELP = "`roam verify --not-real`"\n', encoding="utf-8")

    result = _check_command_examples(["src/app.py"], tmp_path)

    assert result["examples_checked"] == 0
    assert result["violations"] == []


def test_check_command_examples_skips_historical_changelog(tmp_path: Path):
    doc = tmp_path / "CHANGELOG.md"
    doc.write_text("Historical note: `roam index --old-flag` once existed.\n", encoding="utf-8")

    result = _check_command_examples(["CHANGELOG.md"], tmp_path)

    assert result["examples_checked"] == 0
    assert result["violations"] == []


def test_check_command_examples_skips_plugin_example_docs(tmp_path: Path):
    doc = tmp_path / "dev" / "example-plugin" / "README.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("Install the plugin, then run `roam example-greet --name agent`.\n", encoding="utf-8")

    result = _check_command_examples(["dev/example-plugin/README.md"], tmp_path)

    assert result["examples_checked"] == 0
    assert result["violations"] == []


def test_check_command_examples_skips_toml_config(tmp_path: Path):
    doc = tmp_path / "pyproject.toml"
    doc.write_text('description = "Run `roam retrieve --rerank learned`."\n', encoding="utf-8")

    result = _check_command_examples(["pyproject.toml"], tmp_path)

    assert result["examples_checked"] == 0
    assert result["violations"] == []
