"""An analyzed repository cannot replace the MCP server's installed CLI."""

from __future__ import annotations

import inspect

import pytest

from roam import mcp_server
from tests.conftest import git_init, index_in_process


@pytest.mark.parametrize("shadow_source", ["cwd", "pythonpath"])
@pytest.mark.parametrize("operation", ["search", "critique"])
def test_mcp_child_uses_roam_not_project_module(tmp_path, monkeypatch, shadow_source, operation):
    root = tmp_path / "repo space $literal 'quote' &"
    root.mkdir()
    (root / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (root / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    for key in ("ROAM_PROJECT_ROOT", "ROAM_DB_DIR", "ROAM_RUN_ID"):
        monkeypatch.delenv(key, raising=False)
    git_init(root)
    output, code = index_in_process(root)
    assert code == 0, output
    shadow = root if shadow_source == "cwd" else tmp_path / "pythonpath"
    shadow.mkdir(exist_ok=True)
    # Harmless producer impersonation: no files written and no network access.
    (shadow / "roam.py").write_text('print(\'{"command":"PROJECT_MODULE_EXECUTED"}\')\n', encoding="utf-8")
    if shadow_source == "pythonpath":
        monkeypatch.setenv("PYTHONPATH", str(shadow))
    if operation == "search":
        result = mcp_server._run_roam_subprocess(["search", "answer"], str(root))
        assert result["command"] == "search", result
        assert [item["name"] for item in result["results"]] == ["answer"]
    else:
        diff = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def answer():\n-    return 42\n+    return 43\n"
        result = inspect.unwrap(mcp_server.critique_patch)(diff, root=str(root))
        assert result["command"] == "critique", result
        assert result["summary"]["review_source"] == "piped_diff"
