"""R3 — `roam taint` must not present co-occurrence as proven dataflow.

WHAT WAS WRONG (measured on this repo, 2026-08-06, via
``ROAM_DEFAULT_JSON_BUDGET=0 roam taint --json`` against a fresh index):

    total findings                                894
    path_length distribution                      {3: 891, 2: 3}
    source.id == sink.id                          576  (64.4%)
    source descriptor == sink descriptor          145
    confidence=high                               890
    reason "direct source→sink reach, ..."        890
    risk_score                                    100
    duplicate (rule, src_loc, sink_loc) surplus   218  (worst tuple x24)
    summary.rules_lint                            {"qualified_only_violations": 0,
                                                   "total_rules": 22}

ATTRIBUTION (the falsifier the brief demanded, run before any edit).
``run_taint`` was executed twice on the same index, once with
``project_root=None`` — which structurally disables the text-scan
fallback — and once with it, and every finding was tagged by its
producing constructor:

    project_root=None                    ->   0 findings
    project_root=<repo>                  -> 894 findings
    by constructor:
        _text_scan_rule_anchors (co-occ) -> 434, ALL path_len 3,
                                            ALL with source.id == sink.id
        _intraprocedural_co_calls        -> 461, ALL path_len 3,
                                            146 with source descriptor
                                            byte-identical to sink,
                                            217 surplus duplicates
        _bfs_path (the real dataflow)    ->   3, ALL path_len 2

So the constant ``path_length=3`` provably does NOT come from
``_bfs_path``: the BFS produced three findings and none of them had
length 3. It comes from two literal three-element list constructors.
That kills the "these are genuine dataflow findings" hypothesis.

WHAT THIS FILE PINS
-------------------
Three properties, all RED before the R3 fix:

(a) No finding claims ONE symbol as BOTH ends of a flow.
(b) No finding without a computed dataflow path is rendered as a
    "direct source→sink reach", and none of them carries a hop count.
(c) ``summary.rules_lint`` discloses a bare-name-entry count that is
    computed over EVERY rule, so a consumer reading 0 is reading
    something true.

(a) and (b) run against a hermetic fixture that reproduces the exact
shape (a Flask-style handler where a source token and a sink token
co-occur in one function body), not against this repo's index — the
counts above are the recorded field measurement, the fixture is the
regression gate.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import make_src_project as _make_project

# The pre-fix field measurement, kept here so a future reader can tell
# whether a regression is a return to the old behaviour or something new.
PRE_FIX_BASELINE = {
    "findings": 894,
    "path_length_3": 891,
    "source_id_equals_sink_id": 576,
    "source_descriptor_equals_sink_descriptor": 145,
    "confidence_high": 890,
    "reason_says_direct_reach": 890,
    "risk_score": 100,
    "duplicate_surplus_emissions": 218,
    "bfs_path_findings": 3,
}

_DIRECT_REACH_PHRASE = "direct source"
_NO_DATAFLOW_PHRASE = "no dataflow path was computed"


@pytest.fixture(scope="module")
def co_occurrence_taint_envelope(tmp_path_factory):
    """Index a project whose ONLY taint signal is co-occurrence.

    ``request.args`` and ``cursor.execute`` are import-bound names, so
    neither materialises as a ``symbols`` row — the engine reaches them
    exclusively through the text-scan fallback, which is the constructor
    under test. No forward call edge connects them: the only relation is
    "both tokens appear inside ``handler``".
    """
    tmp_path = tmp_path_factory.mktemp("r3_taint")
    proj = _make_project(
        tmp_path,
        {
            "app.py": """
                from flask import request
                import sqlite3

                def handler():
                    name = request.args.get("name")
                    cursor = sqlite3.connect(":memory:").cursor()
                    cursor.execute("SELECT id FROM t ORDER BY id")
                    return name
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["--json", "taint"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
    finally:
        os.chdir(old_cwd)
    return envelope


def test_fixture_actually_produces_co_occurrence_findings(co_occurrence_taint_envelope):
    """Guard the guard: an empty finding list would make (a) and (b) vacuous."""
    findings = co_occurrence_taint_envelope["findings"]
    assert findings, "fixture produced no taint findings — assertions below would be vacuous"
    evidences = {f["value"].get("evidence") for f in findings}
    assert "co_occurrence" in evidences, (
        f"fixture was supposed to exercise the co-occurrence constructor; got evidence classes {evidences!r}"
    )


def test_no_finding_claims_one_symbol_as_both_ends(co_occurrence_taint_envelope):
    """(a) RED pre-fix at 576/894 on this repo.

    A text-scan anchor's ``id`` was the ENCLOSING function's symbol id,
    stamped as the id of the source AND as the id of the sink. One
    symbol is not a flow.
    """
    offenders = []
    for f in co_occurrence_taint_envelope["findings"]:
        value = f["value"]
        src, sink = value["source"], value["sink"]
        src_id, sink_id = src.get("id"), sink.get("id")
        if src_id is not None and src_id == sink_id:
            offenders.append((value["rule_id"], src_id, src, sink))
        if src == sink:
            offenders.append((value["rule_id"], "identical-descriptor", src, sink))
    assert not offenders, (
        f"{len(offenders)} finding(s) report the same symbol as both source and sink "
        f"(pre-fix baseline on roam-code: {PRE_FIX_BASELINE['source_id_equals_sink_id']} by id, "
        f"{PRE_FIX_BASELINE['source_descriptor_equals_sink_descriptor']} byte-identical): {offenders[:3]!r}"
    )


def test_co_occurrence_is_never_rendered_as_direct_reach(co_occurrence_taint_envelope):
    """(b) RED pre-fix at 891/894 on this repo.

    Every co-occurrence finding must say what it actually is, must not
    claim high confidence, and must not carry a hop count — the "3" was
    ``len([source, enclosing, sink])``, a literal constant.
    """
    bad_reason, bad_confidence, bad_path_length = [], [], []
    for f in co_occurrence_taint_envelope["findings"]:
        value = f["value"]
        if value.get("evidence") == "dataflow":
            continue
        reason = f.get("reason") or ""
        if _DIRECT_REACH_PHRASE in reason:
            bad_reason.append(reason)
        if _NO_DATAFLOW_PHRASE not in reason:
            bad_reason.append(reason)
        if f.get("confidence") != "low":
            bad_confidence.append((f.get("confidence"), reason))
        if value.get("path_length") is not None:
            bad_path_length.append(value.get("path_length"))
    assert not bad_reason, (
        f"co-occurrence findings must state that no dataflow was computed; "
        f"pre-fix baseline: {PRE_FIX_BASELINE['reason_says_direct_reach']} findings said "
        f"'direct source→sink reach'. Offending reasons: {bad_reason[:3]!r}"
    )
    assert not bad_confidence, (
        f"co-occurrence findings must be confidence=low; pre-fix baseline: "
        f"{PRE_FIX_BASELINE['confidence_high']} were 'high'. Got: {bad_confidence[:3]!r}"
    )
    assert not bad_path_length, (
        f"co-occurrence findings must emit path_length=null, never a hop count; "
        f"pre-fix baseline: {PRE_FIX_BASELINE['path_length_3']} carried path_length=3. "
        f"Got: {bad_path_length[:5]!r}"
    )


def test_risk_score_counts_only_proven_dataflow(co_occurrence_taint_envelope):
    """The headline number must not be inflated by findings with no computed flow."""
    summary = co_occurrence_taint_envelope["summary"]
    mix = summary.get("evidence_mix")
    assert mix is not None, f"summary must disclose the evidence mix behind risk_score; got {sorted(summary)!r}"
    assert mix["dataflow"] + mix["co_occurrence"] == summary["findings"]
    if mix["dataflow"] == 0:
        assert summary["risk_score"] == 0, (
            f"risk_score must be 0 when no dataflow finding exists (pre-fix baseline on roam-code: "
            f"risk_score={PRE_FIX_BASELINE['risk_score']} off 0 dataflow findings); got {summary['risk_score']}"
        )
        assert summary["errors"] == 0
        assert summary["warnings"] == 0
        assert summary["infos"] == summary["findings"]


def test_unverified_co_occurrence_uses_informational_severity():
    """A zero-dataflow observation cannot remain an error/warning finding."""
    from roam.security.taint_engine import _unverified_severity

    assert _unverified_severity("error") == "note"
    assert _unverified_severity("warning") == "note"


def test_static_subprocess_argv_does_not_create_argument_dataflow():
    """Environment data passed as the child env is not command injection.

    This mirrors ``scripts/prepush_check.py``: fixed argv, no ``shell=True``,
    and a separately constructed environment mapping.
    """
    from roam.security.taint_engine import TaintRule, _python_argument_dataflow_pairs

    source = """
import os
import subprocess

def run_check():
    child_env = os.environ.copy()
    child_env["PYTHONPATH"] = "src"
    return subprocess.run(
        ["python", "-m", "pytest", "tests/test_smoke.py"],
        env=child_env,
        check=False,
    )
"""
    rule = TaintRule(
        rule_id="python-command-injection",
        description="dynamic input reaches subprocess command",
        sources=("os.environ",),
        sinks=("subprocess.run",),
    )
    assert _python_argument_dataflow_pairs(source, rule) == set()


def test_dynamic_subprocess_command_remains_a_taint_positive():
    """Conservation: an interpolated command with ``shell=True`` still fires."""
    from roam.security.taint_engine import TaintRule, _python_argument_dataflow_pairs

    source = """
import os
import subprocess

def run_check():
    target = os.environ["TARGET"]
    return subprocess.run(f"check {target}", shell=True)
"""
    rule = TaintRule(
        rule_id="python-command-injection",
        description="dynamic input reaches subprocess command",
        sources=("os.environ",),
        sinks=("subprocess.run",),
    )
    assert _python_argument_dataflow_pairs(source, rule)


def test_findings_carry_no_engine_private_keys(co_occurrence_taint_envelope):
    """``_enclosing_id`` leaked into 463 of 894 published findings pre-fix."""
    leaked = set()
    for f in co_occurrence_taint_envelope["findings"]:
        value = f["value"]
        for descriptor in (value["source"], value["sink"], *(value.get("path") or [])):
            leaked |= {k for k in descriptor if k.startswith("_")}
    assert not leaked, f"engine-private keys published in the taint envelope: {sorted(leaked)!r}"


def test_identical_claims_are_deduplicated(co_occurrence_taint_envelope):
    """One (rule, source location, sink location) claim is one finding.

    Pre-fix baseline on roam-code: 218 surplus emissions, the worst
    single claim emitted 24 times — each copy counted again in the
    finding total, the confidence distribution and the risk score.
    """
    seen = set()
    dups = []
    for f in co_occurrence_taint_envelope["findings"]:
        value = f["value"]
        src, sink = value["source"], value["sink"]
        key = (
            value["rule_id"],
            src.get("file"),
            src.get("line"),
            src.get("qualified_name") or src.get("name"),
            sink.get("file"),
            sink.get("line"),
            sink.get("qualified_name") or sink.get("name"),
        )
        if key in seen:
            dups.append(key)
        seen.add(key)
    assert not dups, (
        f"{len(dups)} duplicate claim(s) emitted (pre-fix baseline on roam-code: "
        f"{PRE_FIX_BASELINE['duplicate_surplus_emissions']} surplus emissions): {dups[:3]!r}"
    )


def test_rules_lint_counts_bare_entries_in_every_rule():
    """(c) RED pre-fix — the key did not exist.

    ``qualified_only_violations`` is computed only for rules that set
    ``qualified_only: true`` (3 of the shipped 22), so the stamped 0 means
    "no rule disabled its own bare names" while reading as "no rule has
    bare names" — the exact inversion, over a corpus full of bare tokens.
    """
    from roam.commands.cmd_taint import _default_rules_dir
    from roam.security.taint_engine import load_rules
    from roam.security.taint_rules_lint import count_bare_name_entries

    rules = load_rules(_default_rules_dir())
    assert rules, "shipped rule pack failed to load"
    bare = count_bare_name_entries(rules)
    qualified_only_rules = [r for r in rules if r.qualified_only]
    assert bare > 0, (
        f"the shipped {len(rules)}-rule pack is full of bare (dot-less) source/sink entries; "
        f"a counter reporting 0 is the inversion this test exists to prevent"
    )
    assert len(qualified_only_rules) < len(rules), (
        "this test's premise is that qualified_only is set on only a minority of rules; "
        "if every rule sets it, qualified_only_violations is no longer misleading and this "
        "assertion should be revisited rather than deleted"
    )


def test_rules_lint_envelope_stamps_bare_name_entries(co_occurrence_taint_envelope):
    """(c) at the envelope surface — the number a consumer actually reads."""
    rules_lint = co_occurrence_taint_envelope["summary"]["rules_lint"]
    assert "bare_name_entries" in rules_lint, (
        f"summary.rules_lint must disclose an unconditional bare-entry count alongside "
        f"qualified_only_violations; got keys {sorted(rules_lint)!r}"
    )
    assert rules_lint["bare_name_entries"] > 0, (
        f"the shipped rule pack has bare entries; stamped {rules_lint['bare_name_entries']!r}"
    )
