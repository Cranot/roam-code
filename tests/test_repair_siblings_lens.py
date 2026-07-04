from __future__ import annotations

import json
import os
import subprocess

import pytest

from roam.cli import cli
from roam.commands.cmd_repair_siblings import (
    RepairIntent,
    SymbolBody,
    derive_repair_intent,
    lexical_candidate_generation,
    rerank_by_repair_applicability,
)
from tests.conftest import index_in_process, invoke_cli


_PATCH = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,4 +1,6 @@
 def handle_user(user):
-    return normalize(user.email)
+    if user.email is None:
+        return None
+    return normalize(user.email)
"""


def _git_init(path):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True, env=env)
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True, env=env)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=path, capture_output=True, check=True, env=env)


@pytest.fixture
def repair_project(tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (project / "app.py").write_text(
        "def normalize(value):\n"
        "    return value.strip().lower()\n"
        "\n"
        "\n"
        "def handle_user(user):\n"
        "    if user.email is None:\n"
        "        return None\n"
        "    return normalize(user.email)\n"
        "\n"
        "\n"
        "def handle_account(account):\n"
        "    return normalize(account.email)\n"
        "\n"
        "\n"
        "def handle_admin(user):\n"
        "    if user.email is None:\n"
        "        return None\n"
        "    return normalize(user.email)\n",
        encoding="utf-8",
    )
    _git_init(project)
    output, code = index_in_process(project)
    assert code == 0, output
    return project


def test_repair_intent_extraction_prefers_added_guard():
    intent = derive_repair_intent(_PATCH)

    assert intent.kind == "guard_added"
    assert intent.deleted_pattern == "return normalize(user.email)"
    assert intent.added_pattern == "if user.email is None:"
    assert intent.changed_callees == {"removed": [], "added": [], "changed": []}


def test_lexical_candidate_generation_orders_similar_bodies():
    anchor = SymbolBody(1, "app.py", "handle_user", None, "function", 1, 4, "def handle_user(user):\n    return normalize(user.email)")
    sibling = SymbolBody(2, "app.py", "handle_account", None, "function", 6, 7, "def handle_account(user):\n    return normalize(user.email)")
    other = SymbolBody(3, "app.py", "parse_count", None, "function", 9, 10, "def parse_count(raw):\n    return int(raw)")

    candidates = lexical_candidate_generation(anchor, [sibling, other], limit=10, min_score=0.01)

    assert candidates[0].name == "handle_account"
    assert candidates[0].lexical_score > candidates[1].lexical_score


def test_applicability_rerank_suppresses_hard_negative():
    intent = RepairIntent(
        kind="guard_added",
        deleted_pattern="return normalize(user.email)",
        added_pattern="if user.email is None:",
        changed_callees={"removed": [], "added": [], "changed": []},
    )
    risky = SymbolBody(
        2,
        "app.py",
        "handle_account",
        None,
        "function",
        10,
        11,
        "def handle_account(account):\n    return normalize(account.email)",
        lexical_score=0.91,
    )
    hard_negative = SymbolBody(
        3,
        "app.py",
        "handle_admin",
        None,
        "function",
        13,
        16,
        "def handle_admin(user):\n    if user.email is None:\n        return None\n    return normalize(user.email)",
        lexical_score=0.95,
    )

    ranked, suppressed = rerank_by_repair_applicability(intent, [hard_negative, risky])

    assert suppressed == 1
    assert [item.symbol.name for item in ranked] == ["handle_account"]
    assert ranked[0].repair_applicability == 1.0


def test_repair_siblings_default_off(cli_runner, monkeypatch):
    monkeypatch.delenv("ROAM_EXPERIMENTAL_REPAIR_SIBLINGS", raising=False)

    result = cli_runner.invoke(cli, ["repair-siblings", "--help"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_repair_siblings_json_output_shape(cli_runner, repair_project, tmp_path, monkeypatch):
    patch_path = tmp_path / "fix.patch"
    patch_path.write_text(_PATCH, encoding="utf-8")
    monkeypatch.setenv("ROAM_EXPERIMENTAL_REPAIR_SIBLINGS", "1")

    result = invoke_cli(
        cli_runner,
        [
            "repair-siblings",
            "--anchor",
            "app.py::handle_user",
            "--diff",
            str(patch_path),
            "--top-n",
            "5",
        ],
        cwd=repair_project,
        json_mode=True,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["default_off_flag"] == "ROAM_EXPERIMENTAL_REPAIR_SIBLINGS=1"
    assert payload["summary"]["candidate_count"] == 1
    assert payload["summary"]["suppressed_count"] >= 1
    assert payload["repair_intent"]["kind"] == "guard_added"
    assert payload["candidates"][0]["symbol"] == "handle_account"
    assert payload["candidates"][0]["repair_applicability"] == 1.0
