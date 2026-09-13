"""Execute the published algorithm example and its semantic counterexamples."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.conftest import invoke_cli
from tests.test_homepage_contract import SITE, HomepageParser, normalized


def _example():
    page = HomepageParser((SITE / "docs/command-reference.html").read_text(encoding="utf-8"))
    section = next(node for node in page.root.find("section") if node.attrs.get("id") == "algorithm-choices")
    sources = [node for node in section.find("pre") if node.attrs.get("id") == "algorithm-example-source"]
    assert len(sources) == 1, "Publish a reproducible source fixture beside the algorithm guidance"
    return section, next(sources[0].find("code")).text()


def test_published_algorithm_fixture_produces_the_selected_finding(project_factory, cli_runner, monkeypatch):
    section, source = _example()
    project = project_factory({"lookup.js": source})
    monkeypatch.chdir(project)
    commands = next(node for node in section.find("pre") if node.attrs.get("id") == "algorithm-example-commands")
    lines = next(commands.find("code")).text().splitlines()
    assert lines == [
        "git init --quiet",
        "git add -- lookup.js",
        "roam --json index",
        "roam --json algo --task loop-lookup --path lookup.js",
    ]
    result = invoke_cli(cli_runner, lines[-1].split()[2:], cwd=project, json_mode=True)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    summary = payload["summary"]
    assert summary["partial_success"] is False
    assert summary["detectors_failed"] == 0
    assert summary["truncated"] is False
    assert summary["scope_file_count"] == 1 and summary["scoped_paths"] == ["lookup.js"]
    assert [item["symbol_name"] for item in payload["findings"]] == ["countAllowed"], (
        "Retain the repeated-membership finding without advising Set for positions or an existing Set"
    )
    excerpt = next(node for node in section.find("pre") if node.attrs.get("id") == "algorithm-example-finding")
    selected = json.loads(next(excerpt.find("code")).text())
    assert selected == {key: payload["findings"][0][key] for key in selected}
    assert {"symbol_name", "task_id", "suggested_way", "confidence", "location"} <= selected.keys()
    text = normalized(section.text())
    assert "synthetic fixture" in text and "selected fields" in text
    assert "No speedup was measured" in text


def test_published_algorithm_alternative_preserves_strings_but_not_every_semantic_contract():
    _, source = _example()
    node = shutil.which("node")
    if not node:
        pytest.skip("The JavaScript semantic controls require Node.js")
    checks = r"""
const assert = require('node:assert/strict');
for (const [values, allowed, expected] of [
  [[], [], 0],
  [['guest'], [], 0],
  [['admin', 'guest', 'admin'], ['admin', 'admin'], 2],
  [['Admin', 'admin'], ['admin'], 1],
]) {
  const before = JSON.stringify([values, allowed]);
  assert.equal(countAllowed(values, allowed), expected);
  assert.equal(countAllowedWithSet(values, allowed), expected);
  assert.equal(JSON.stringify([values, allowed]), before);
}
assert.deepEqual(findPositions(['admin', 'guest', 'admin'], ['guest', 'admin', 'admin']), [1, 0, 1]);
assert.deepEqual(findPositions(['missing'], ['admin']), [-1]);
assert.equal(countAllowed([NaN], [NaN]), 0);
assert.equal(countAllowedWithSet([NaN], [NaN]), 1);
console.log('PASS: 4 string cases; inputs preserved; first positions and missing sentinel preserved; NaN refutes blanket replacement');
"""
    # Intentionally execute the published JavaScript in a real JS engine.
    # The fixed source/controls use no network, clock, random values or disk I/O;
    # a mock would not catch indexOf/Set semantic differences. Bound execution.
    run = subprocess.run([node, "-e", source + "\n" + checks], capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stdout + run.stderr
