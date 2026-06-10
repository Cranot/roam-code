"""Tests for `roam envelope-diff` — structured diff of two compile envelopes.

The command is invoked directly via `CliRunner` against the click object,
because at the time these tests are authored the command has not yet been
registered in `roam.cli._COMMANDS`. The lazy-loader registration is a
separate concern; the command's behavior is fully tested here.

Three tests:
  * happy-path: two trivially-different prompts compile to envelopes
    with at least one classifier or probe-shape delta.
  * --from-cache: seed a tempdir's `.roam/compile-envelope-cache.sqlite`
    with two synthetic rows and diff them by key.
  * LAW 4: every fact terminal must be in the concrete-noun anchor set
    imported from `roam.output.formatter.concrete_plural_terminals`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands.cmd_envelope_diff import envelope_diff
from tests._helpers.repo_root import repo_root


@pytest.fixture
def runner():
    return CliRunner()


def _json_obj():
    """Click context object for `--json` mode (matches roam.cli setup)."""
    return {"json": True}


# ---------------------------------------------------------------------------
# Test 1 — happy path: two different prompts compile and diff cleanly.
# ---------------------------------------------------------------------------


def test_envelope_diff_happy_path(runner, tmp_path):
    """Compiling two clearly different prompts yields a structured diff
    with a verdict, classifier delta, and probe-family deltas."""
    # Two prompts that route to DIFFERENT procedures so classifier_delta
    # is meaningfully populated regardless of probe data availability.
    prompt_a = "Find files coupled to src/roam/cli.py"
    prompt_b = "investigate why login is slow"

    result = runner.invoke(
        envelope_diff,
        [prompt_a, prompt_b, "--root", str(tmp_path)],
        obj=_json_obj(),
    )
    assert result.exit_code == 0, result.output

    envelope = json.loads(result.output)
    assert envelope["schema"] == "roam-envelope-v1"
    assert envelope["command"] == "envelope-diff"
    assert "summary" in envelope
    assert "verdict" in envelope["summary"]
    assert envelope["summary"]["partial_success"] is False

    # Verdict is one line, mentions added/removed/changed
    verdict = envelope["summary"]["verdict"]
    assert "\n" not in verdict
    assert "added" in verdict
    assert "removed" in verdict
    assert "changed" in verdict

    # Top-level diff keys present and correctly typed
    assert isinstance(envelope["added_probes"], list)
    assert isinstance(envelope["removed_probes"], list)
    assert isinstance(envelope["changed_probes"], list)
    assert isinstance(envelope["size_delta_bytes"], int)
    cdelta = envelope["classifier_delta"]
    assert set(cdelta.keys()) == {
        "procedure_a",
        "procedure_b",
        "confidence_a",
        "confidence_b",
    }
    # The two prompts route to different procedures (the whole point of
    # the test). If this ever stops being true the chosen prompts must
    # change to preserve test value.
    assert cdelta["procedure_a"] != cdelta["procedure_b"], f"prompts no longer differ in procedure: {cdelta}"


# ---------------------------------------------------------------------------
# Test 2 — `--from-cache`: seed two rows in a tempdir SQLite and diff them.
# ---------------------------------------------------------------------------


def _seed_envelope_cache(
    root: Path, key: str, envelope: dict, art_label: str = "facts", repo_head: str = "deadbeef"
) -> None:
    """Build a `.roam/compile-envelope-cache.sqlite` and insert one row."""
    dot_roam = root / ".roam"
    dot_roam.mkdir(parents=True, exist_ok=True)
    path = dot_roam / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS env_cache "
        "(key TEXT PRIMARY KEY, repo_head TEXT, art_label TEXT, "
        "envelope_json TEXT, ts REAL, dep_mtimes_json TEXT)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO env_cache "
        "(key, repo_head, art_label, envelope_json, ts, dep_mtimes_json) "
        "VALUES (?,?,?,?,?,?)",
        (key, repo_head, art_label, json.dumps(envelope), 1700000000.0, None),
    )
    conn.commit()
    conn.close()


def test_envelope_diff_from_cache(runner, tmp_path):
    """Seed two synthetic envelopes in the on-disk cache, diff by key."""
    # Envelope A — two probe families
    env_a = {
        "schema": "roam-plan-v0",
        "summary": {
            "procedure": "structural_coupling",
            "classifier_confidence": 0.92,
        },
        "plan": {
            "procedure": "structural_coupling",
            "classifier_confidence": 0.92,
            "prefetched_facts": {
                "coupling_pairs": [{"a": "x.py", "b": "y.py"}],
                "structural_blast": {"affected": ["x.py", "y.py"]},
            },
        },
    }
    # Envelope B — drops coupling_pairs, changes structural_blast, adds new probe
    env_b = {
        "schema": "roam-plan-v0",
        "summary": {
            "procedure": "trace_flow",
            "classifier_confidence": 0.77,
        },
        "plan": {
            "procedure": "trace_flow",
            "classifier_confidence": 0.77,
            "prefetched_facts": {
                "structural_blast": {
                    "affected": ["x.py", "y.py", "z.py"],
                    "newly_added_field": True,
                },
                "trace_steps": [{"step": 1}, {"step": 2}],
            },
        },
    }

    key_a = "a" * 40
    key_b = "b" * 40
    _seed_envelope_cache(tmp_path, key_a, env_a)
    _seed_envelope_cache(tmp_path, key_b, env_b)

    result = runner.invoke(
        envelope_diff,
        ["--from-cache", "--root", str(tmp_path), key_a, key_b],
        obj=_json_obj(),
    )
    assert result.exit_code == 0, result.output

    envelope = json.loads(result.output)
    assert envelope["summary"]["from_cache"] is True

    # Probes added in B: trace_steps. Removed (in A but not B): coupling_pairs.
    assert envelope["added_probes"] == ["trace_steps"]
    assert envelope["removed_probes"] == ["coupling_pairs"]

    # structural_blast changed (different value); fields_added should contain
    # "newly_added_field"
    changed_names = {c["name"] for c in envelope["changed_probes"]}
    assert "structural_blast" in changed_names
    sb = next(c for c in envelope["changed_probes"] if c["name"] == "structural_blast")
    assert "newly_added_field" in sb["fields_added"]
    assert sb["fields_removed"] == []
    # Adding a list element + a field grows the serialized bytes.
    assert sb["size_delta_bytes"] > 0

    # Classifier delta reflects the synthetic envelopes.
    cdelta = envelope["classifier_delta"]
    assert cdelta["procedure_a"] == "structural_coupling"
    assert cdelta["procedure_b"] == "trace_flow"
    assert cdelta["confidence_a"] == 0.92
    assert cdelta["confidence_b"] == 0.77


def test_envelope_diff_from_cache_miss(runner, tmp_path):
    """Cache miss surfaces a `cache_miss` verdict and exit code 2."""
    # Empty cache directory — neither key exists.
    (tmp_path / ".roam").mkdir()
    result = runner.invoke(
        envelope_diff,
        ["--from-cache", "--root", str(tmp_path), "nope1", "nope2"],
        obj=_json_obj(),
    )
    assert result.exit_code == 2
    envelope = json.loads(result.output)
    assert envelope["summary"]["verdict"] == "cache_miss"
    assert envelope["summary"]["partial_success"] is True


# ---------------------------------------------------------------------------
# Test 3 — LAW 4 anchor compliance: every fact's terminal token must be in
# the concrete-noun anchor vocabulary exported by the formatter.
# ---------------------------------------------------------------------------


def _terminal_token(fact: str) -> str:
    """Return the last word of `fact`, lowercased, punctuation stripped."""
    # Match the test_law4_lint.py convention: split on whitespace, strip
    # trailing punctuation from the last token, lowercase.
    last = fact.strip().split()[-1] if fact.strip().split() else ""
    return re.sub(r"[^a-zA-Z0-9_-]+$", "", last).lower()


def _concrete_plural_terminals() -> frozenset[str]:
    """Pull the canonical anchor set from formatter.py by reading the
    source — `concrete_plural_terminals` is defined inline inside
    `_humanize_summary_fact` and is not module-level importable. We
    extract it via AST so this test stays decoupled from the function's
    internals and surfaces drift cleanly."""
    import ast

    formatter_path = repo_root() / "src" / "roam" / "output" / "formatter.py"
    tree = ast.parse(formatter_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "concrete_plural_terminals"
            and isinstance(node.value, ast.Tuple)
        ):
            terminals: set[str] = set()
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    terminals.add(elt.value)
            if terminals:
                return frozenset(terminals)
    raise AssertionError("could not locate concrete_plural_terminals tuple in formatter.py")


def test_envelope_diff_facts_are_law4_anchored(runner, tmp_path):
    """Every emitted `agent_contract.facts` terminal token must be in the
    concrete-noun anchor vocabulary. This mirrors `tests/test_law4_lint.py`
    but pins it to this specific command so regressions land on the right
    test file."""
    anchors = _concrete_plural_terminals()
    # Sanity: the formatter's set is non-empty and contains a known term.
    assert "files" in anchors
    assert "symbols" in anchors

    # Seed two cached envelopes (avoids needing a real git repo / index)
    env_a = {"summary": {"procedure": "p1", "classifier_confidence": 0.5}, "plan": {"prefetched_facts": {"probe_x": 1}}}
    env_b = {
        "summary": {"procedure": "p2", "classifier_confidence": 0.6},
        "plan": {"prefetched_facts": {"probe_y": 2, "probe_x": 3}},
    }
    _seed_envelope_cache(tmp_path, "k1" + "0" * 38, env_a)
    _seed_envelope_cache(tmp_path, "k2" + "0" * 38, env_b)

    result = runner.invoke(
        envelope_diff,
        ["--from-cache", "--root", str(tmp_path), "k1" + "0" * 38, "k2" + "0" * 38],
        obj=_json_obj(),
    )
    assert result.exit_code == 0, result.output

    envelope = json.loads(result.output)
    facts = envelope["agent_contract"]["facts"]
    assert facts, "facts list must not be empty"

    for fact in facts:
        terminal = _terminal_token(fact)
        assert terminal in anchors, (
            f"LAW 4 violation: fact {fact!r} ends on terminal {terminal!r} "
            f"which is not in the concrete-noun anchor set. Add it to "
            f"src/roam/output/formatter.py:concrete_plural_terminals "
            f"AND tests/test_law4_lint.py:_CONCRETE_NOUN_ANCHORS, "
            f"or rephrase the fact to end on an existing anchor."
        )
