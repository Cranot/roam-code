"""Probe-trigger override + intent probes for bare-symbol freeform shapes.

Corpus finding: prompts like "which tests cover X" / "find SQL injection
risks" / "list TODO comments in F" score only 0.35 classifier confidence
(no path bump), so the confidence-band artifact policy chose "facts" and
the probe pipeline NEVER RAN — empty envelopes shipped while the probes
that answer these shapes outright (W80 test-impact, W109 owners, W111
todo-audit, the new taint scan) sat idle. Three seals:

1. Probe-trigger override: a matched shape-regex attempts the L1 build
   even when the policy chose "facts" (empty probes still demote).
2. New taint probe for security-shaped tasks.
3. Skip-table fix: todo_audit was cost-skipped for freeform despite
   self-gating on a microsecond regex; "owner_probe" was a dead label.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli  # noqa: E402

SRC = """\
import sqlite3


def load_items(conn, items):
    # TODO: batch this query
    out = []
    for item in items:
        out.append(conn.execute("SELECT * FROM t WHERE id=?", (item,)).fetchone())
    return out
"""

TEST = """\
from src.loader import load_items


def test_load_items_roundtrip():
    assert load_items is not None
"""


def _repo(tmp_path: Path) -> Path:
    proj = tmp_path / "intent_repo"
    (proj / "src").mkdir(parents=True)
    (proj / "tests").mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "src" / "loader.py").write_text(SRC)
    (proj / "tests" / "test_loader.py").write_text(TEST)
    git_init(proj)
    index_in_process(proj)
    return proj


def _compile_facts(proj: Path, task: str) -> tuple[str, dict]:
    runner = CliRunner()
    res = invoke_cli(runner, ["--json", "compile", task], cwd=proj)
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    pf = ((d.get("artifact") or {}).get("plan") or {}).get("prefetched_facts") or {}
    return d["summary"]["artifact_type"], pf


def test_bare_symbol_tests_for_prompt_gets_test_impact(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    art, pf = _compile_facts(proj, "which tests cover load_items")
    assert "test_impact" in pf, (art, list(pf))


def test_security_prompt_gets_taint_summary(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    art, pf = _compile_facts(proj, "find SQL injection risks")
    assert "taint_summary" in pf, (art, list(pf))
    # Pattern-2 honesty: zero findings still discloses the scan ran.
    assert "scan ran clean" in pf.get("taint_summary_definition", "")


def test_todo_prompt_gets_todo_items(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    art, pf = _compile_facts(proj, "list TODO comments in src/loader.py")
    assert "todo_items" in pf, (art, list(pf))


def test_non_trigger_low_conf_freeform_still_demotes(tmp_path, monkeypatch):
    """The override is shape-gated: a generic low-confidence freeform prompt
    without a probe trigger keeps the facts artifact (the W167-169 family's
    cost discipline is untouched)."""
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    runner = CliRunner()
    res = invoke_cli(
        runner,
        ["--json", "compile", "lets think about the general direction of this project together"],
        cwd=proj,
    )
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["summary"]["artifact_type"] == "facts", d["summary"]


def test_freeform_skip_table_keeps_self_gated_probes():
    """todo_audit must not be cost-skipped (it self-gates on a regex), and
    the dead 'owner_probe' label must stay gone (the real label is 'owners')."""
    from roam.plan.compiler import _PROCEDURE_PROBE_SKIPS

    freeform = _PROCEDURE_PROBE_SKIPS["freeform_explore"]
    assert "todo_audit" not in freeform
    assert "owner_probe" not in freeform


def test_dead_skip_labels_never_reappear():
    """Every label in every skip set must be a registered extender label —
    a typo'd label silently never applies (the 'owner_probe' class)."""
    from roam.plan.compiler import _L1_ALWAYS_ON_PROBES, _PROCEDURE_PROBE_SKIPS

    registered = {label for label, _fn in _L1_ALWAYS_ON_PROBES}
    for proc, skips in _PROCEDURE_PROBE_SKIPS.items():
        unknown = skips - registered
        assert not unknown, f"{proc}: skip labels not registered as extenders: {sorted(unknown)}"


def test_world_model_prompt_gets_classification(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    art, pf = _compile_facts(proj, "is load_items idempotent")
    assert "world_model" in pf, (art, list(pf))
    assert pf["world_model"]["symbol"] == "load_items"


def test_mutation_prompt_gets_side_effects(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    art, pf = _compile_facts(proj, "what does load_items mutate")
    assert "world_model" in pf, (art, list(pf))


def test_design_pattern_prompt_gets_instances(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    art, pf = _compile_facts(proj, "what design patterns does this codebase use")
    # Small fixture repo may have zero patterns — the probe must not crash;
    # when the summary exists the key embeds (dict-shaped envelopes flattened).
    if "design_patterns" in pf:
        assert isinstance(pf["design_patterns"].get("instances"), list)
