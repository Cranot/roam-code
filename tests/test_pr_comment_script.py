"""Tests for .github/scripts/pr-comment.js helper behavior.

These tests execute small Node snippets and are skipped when Node is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node.js not available in test environment",
)


def _node_eval(code: str, env: dict[str, str] | None = None) -> str:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        ["node", "-e", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=merged_env,
        check=True,
    )
    return result.stdout.strip()


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".github" / "scripts" / "pr-comment.js"


def test_build_comment_includes_marker_and_sections(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "health.json").write_text(
        json.dumps({
            "summary": {
                "verdict": "overall healthy",
                "health_score": 82,
                "issue_count": 3,
            },
        }),
        encoding="utf-8",
    )
    (results_dir / "pr-risk.json").write_text(
        json.dumps({
            "summary": {
                "verdict": "low risk",
                "risk_score": 21,
                "files_changed": 4,
            },
        }),
        encoding="utf-8",
    )

    code = """
const mod = require(process.env.PR_COMMENT_PATH);
const body = mod.buildComment(process.env);
console.log(body);
"""
    out = _node_eval(
        code,
        env={
            "PR_COMMENT_PATH": str(_script_path()),
            "RESULTS_DIR": str(results_dir),
            "HEALTH_SCORE": "82",
            "GATE_EXPR": "health_score>=70",
            "GATE_PASSED": "true",
            "SARIF_CATEGORY": "roam-code/analyze/py3.11",
            "SARIF_RESULTS": "42",
            "SARIF_TRUNCATED": "false",
            "COMMANDS_RUN": "health pr-risk",
            "CHANGED_ONLY": "true",
            "BASE_REF": "abc123",
            "AFFECTED_COUNT": "10",
        },
    )

    assert "<!-- roam-code-analysis -->" in out
    assert "## roam-code Analysis" in out
    assert "### Health Metrics" in out
    assert "### PR Risk" in out
    assert "### Quality Gate: PASSED" in out
    assert "### SARIF Upload" in out


def test_select_sticky_comments_prefers_latest_and_marks_duplicates():
    code = """
const mod = require(process.env.PR_COMMENT_PATH);
const marker = mod.MARKER;
const comments = [
  {id: 1, body: "random", updated_at: "2026-02-20T00:00:00Z"},
  {id: 2, body: marker + " old", updated_at: "2026-02-20T00:00:00Z"},
  {id: 3, body: marker + " new", updated_at: "2026-02-21T00:00:00Z"},
  {id: 4, body: marker + " oldest", updated_at: "2026-02-19T00:00:00Z"},
];
const pick = mod.selectStickyComments(comments);
console.log(JSON.stringify({primary: pick.primary.id, duplicates: pick.duplicates.map(c => c.id)}));
"""
    out = _node_eval(
        code,
        env={"PR_COMMENT_PATH": str(_script_path())},
    )
    data = json.loads(out)
    assert data["primary"] == 3
    assert data["duplicates"] == [2, 4]


def test_upsert_sticky_comment_updates_and_deletes_duplicates():
    code = """
const mod = require(process.env.PR_COMMENT_PATH);
const marker = mod.MARKER;
const calls = {update: [], create: [], del: []};
const github = {
  paginate: async () => ([
    {id: 10, body: marker + " old", updated_at: "2026-02-20T00:00:00Z"},
    {id: 11, body: marker + " new", updated_at: "2026-02-21T00:00:00Z"},
  ]),
  rest: {issues: {
    listComments: async () => ({data: []}),
    updateComment: async (args) => { calls.update.push(args); return {}; },
    createComment: async (args) => { calls.create.push(args); return {data: {id: 99}}; },
    deleteComment: async (args) => { calls.del.push(args); return {}; },
  }},
};
const context = {repo: {owner: "o", repo: "r"}, issue: {number: 7}};
const core = {info: () => {}, warning: () => {}};
mod.upsertStickyComment({github, context, core, body: "B"}).then(() => {
  console.log(JSON.stringify(calls));
});
"""
    out = _node_eval(
        code,
        env={"PR_COMMENT_PATH": str(_script_path())},
    )
    calls = json.loads(out)
    assert len(calls["update"]) == 1
    assert calls["update"][0]["comment_id"] == 11
    assert len(calls["create"]) == 0
    assert len(calls["del"]) == 1
    assert calls["del"][0]["comment_id"] == 10


def test_clamp_comment_enforces_upper_bound():
    code = """
const mod = require(process.env.PR_COMMENT_PATH);
const text = "x".repeat(70000);
const out = mod.clampComment(text, 1000);
console.log(out.length.toString());
"""
    out = _node_eval(
        code,
        env={"PR_COMMENT_PATH": str(_script_path())},
    )
    assert int(out) <= 1000
