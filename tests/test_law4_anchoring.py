"""LAW 4 (CLAUDE.md): agent_contract.facts must anchor on concrete nouns.

From CLAUDE.md::

  [LAW] "Code" nouns activate analytical mode on any input. (D15) — In roam:
  agent_contract.facts strings should anchor on concrete nouns
  ("useThemeClasses has 528 callers") not abstract ones
  ("this symbol has many callers"). Concrete nouns activate analytical
  processing; abstract nouns activate summary mode.

This file holds:

1. A meta-test that source-scans the W12-fixed commands for known-bad fact
   patterns ("{N} X symbols" with no analytical anchor, "Direction: X"
   key:value pairs, bare-numeric facts). If any future change re-introduces
   one of these patterns, the test fails LOUDLY so the regression is caught
   at PR time.

2. Per-command regression tests that exercise the fixed commands end-to-end
   and assert their ``agent_contract.facts`` lists contain the expected
   concrete-noun anchor strings.

Scope: only the 6 commands fixed during the W12 polish wave —
``idempotency``, ``side-effects``, ``graph-diff``, ``architecture-drift``,
``alerts``, ``adversarial``. A future full sweep can extend the coverage.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_COMMANDS = REPO_ROOT / "src" / "roam" / "commands"

# Ensure we import roam from the working tree (not a globally-installed copy
# that may lag behind). conftest.py does not add ``src/`` to sys.path because
# the test runner is normally invoked via ``pip install -e .``; in
# environments where that hasn't been done (e.g. running pytest directly
# against a checkout), prepend it here so the importlib-based tests resolve
# the modules we just edited.
_SRC_DIR = REPO_ROOT / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# ---------------------------------------------------------------------------
# Meta-test: scan source for known-bad patterns
# ---------------------------------------------------------------------------


# Patterns that violate LAW 4 (in fact-emission contexts). Match these against
# the LITERAL strings inside the fixed command files only — full-codebase
# scanning would require a much larger allowlist.
#
# Each entry is (regex, human-readable reason). The regex is matched against
# raw file source after stripping comments / docstrings.
KNOWN_BAD_FACT_PATTERNS = [
    (
        re.compile(r'facts\.append\(\s*f?["\']\{[^"\']*\}\s+(?:non_idempotent|idempotent|unknown-idempotency)\s+symbols["\']'),
        "bare numeric prefix + classification noun (no analytical verb)",
    ),
    (
        re.compile(r'facts\.append\(\s*f?["\']\{n\}\s+\{k\}\s+symbols["\']'),
        "bare numeric prefix + interpolated kind (no analytical verb)",
    ),
    (
        re.compile(r'f["\']Direction:\s*\{direction\}["\']'),
        "abstract key:value pair ('Direction: X')",
    ),
    (
        re.compile(r'f["\']\{len\(diff\.symbols_added\)\}\s+symbols added["\']'),
        "bare numeric prefix + concrete noun (no analytical subject anchor)",
    ),
]


FIXED_COMMAND_FILES = [
    "cmd_idempotency.py",
    "cmd_side_effects.py",
    "cmd_graph_diff.py",
    "cmd_architecture_drift.py",
    "cmd_alerts.py",
    "cmd_adversarial.py",
]


def _strip_python_comments(src: str) -> str:
    """Remove # line comments. Docstrings are left in place because they
    don't contain ``facts.append(...)`` call sites by convention."""
    out_lines = []
    for line in src.splitlines():
        # Strip trailing # comment but keep strings intact -- naive split is
        # fine for our regex needs (we only care about live code).
        if "#" in line:
            # Don't strip if # is inside a string literal -- the bad
            # patterns above are all f-strings that wouldn't contain raw #
            # in the matched portion, so a coarse heuristic is enough.
            idx = line.find("#")
            # If the # is inside a string, the line up to # is unbalanced
            # in quotes. Cheap check:
            head = line[:idx]
            if head.count('"') % 2 == 0 and head.count("'") % 2 == 0:
                line = head
        out_lines.append(line)
    return "\n".join(out_lines)


@pytest.mark.parametrize("filename", FIXED_COMMAND_FILES)
def test_no_known_bad_law4_patterns(filename: str) -> None:
    """No fixed command may re-introduce a known-bad LAW 4 fact pattern."""
    path = SRC_COMMANDS / filename
    assert path.exists(), f"expected fixed command file at {path}"
    src = _strip_python_comments(path.read_text(encoding="utf-8"))
    for pattern, reason in KNOWN_BAD_FACT_PATTERNS:
        m = pattern.search(src)
        assert m is None, (
            f"{filename}: re-introduced LAW 4 violation ({reason}): "
            f"matched fragment = {m.group(0)!r}"
        )


# ---------------------------------------------------------------------------
# Per-command anchor-string regression tests
# ---------------------------------------------------------------------------


def _fact_anchor_present(facts: list, anchor: str) -> bool:
    """Return True if any fact contains *anchor* (case-insensitive)."""
    lower = anchor.lower()
    return any(isinstance(f, str) and lower in f.lower() for f in facts)


def _import_envelope_builder(module_name: str, attr: str):
    """Helper to import a function from src/roam without invoking the CLI."""
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, attr)


def test_graph_diff_facts_anchor_on_graph_delta() -> None:
    """``_facts`` in cmd_graph_diff emits 'graph delta vs <label>' anchored
    facts with an explicit verb (added / removed / introduced / detected)."""
    _facts = _import_envelope_builder("roam.commands.cmd_graph_diff", "_facts")

    class _Diff:
        symbols_added = ["a", "b", "c"]
        symbols_removed = ["d"]
        edges_added = ["e1", "e2"]
        edges_removed = []
        in_degree_shifts = ["s1"]
        out_degree_shifts = ["s2", "s3"]
        new_cycles = ["c1"]
        likely_moves = []

    facts = _facts(_Diff(), baseline_label="snap-2026-05-12")
    # Anchor: every fact must lead with the concrete subject + scope.
    assert facts, "graph-diff produced no facts"
    for f in facts:
        assert "graph delta vs snap-2026-05-12" in f, (
            f"graph-diff fact missing 'graph delta vs <label>' anchor: {f!r}"
        )
    # Verbs: ensure explicit analytical verbs are present.
    joined = " ".join(facts).lower()
    assert "added" in joined
    assert "removed" in joined
    assert "introduced" in joined  # for new_cycles branch


def test_graph_diff_facts_default_baseline_label() -> None:
    """When *baseline_label* is ``None``, facts still anchor on a concrete
    word ('baseline'), not on bare numerics."""
    _facts = _import_envelope_builder("roam.commands.cmd_graph_diff", "_facts")

    class _Diff:
        symbols_added = []
        symbols_removed = []
        edges_added = []
        edges_removed = []
        in_degree_shifts = []
        out_degree_shifts = []
        new_cycles = []
        likely_moves = []

    facts = _facts(_Diff(), baseline_label=None)
    assert facts, "graph-diff produced no facts even with empty diff"
    assert all("graph delta vs baseline" in f for f in facts), facts


# ---------------------------------------------------------------------------
# Source-text regression tests (cheap, no DB / CLI invocation needed)
# ---------------------------------------------------------------------------


def _read_cmd_source(filename: str) -> str:
    return (SRC_COMMANDS / filename).read_text(encoding="utf-8")


def test_idempotency_facts_anchor_on_idempotency_scan() -> None:
    src = _read_cmd_source("cmd_idempotency.py")
    assert "idempotency scan flagged" in src, src[:200]
    assert "idempotency scan classified" in src
    assert "idempotency scan confirmed" in src
    # And the bare-numeric form must be gone:
    assert "{by_kind['non_idempotent']} non_idempotent symbols" not in src


def test_side_effects_facts_anchor_on_side_effects_scan() -> None:
    src = _read_cmd_source("cmd_side_effects.py")
    assert "side-effects scan classified" in src
    assert "side-effects scan confirmed" in src
    # The verbless "{n} {k} symbols" form must be gone:
    assert 'f"{n} {k} symbols"' not in src


def test_architecture_drift_facts_drop_direction_keyvalue() -> None:
    src = _read_cmd_source("cmd_architecture_drift.py")
    # Old "Direction: X" key:value pair was the worst LAW 4 violation here.
    assert 'f"Direction: {direction}"' not in src
    # Replaced with concrete-anchored sentence:
    assert "architecture drift over" in src
    assert "trajectory is" in src


def test_alerts_emits_explicit_agent_contract_facts() -> None:
    src = _read_cmd_source("cmd_alerts.py")
    # The fix wires explicit agent_contract={...} into json_envelope().
    assert "agent_contract={" in src
    assert "alerts scan flagged" in src or "alerts scan emitted" in src


def test_adversarial_emits_explicit_agent_contract_facts() -> None:
    src = _read_cmd_source("cmd_adversarial.py")
    assert "agent_contract={" in src
    assert "adversarial review flagged" in src or "adversarial review surfaced" in src


# ---------------------------------------------------------------------------
# Helpers.py: positive vocabulary + allowlist policy docstring
# ---------------------------------------------------------------------------


def test_helpers_docstring_names_intentional_exclusions() -> None:
    """W11 polish: the auto_log allowlist policy is documented inline so a
    future maintainer can see 'health is intentionally not auto-logged'
    instead of having to deduce it from absence (LAW 7 — positive
    vocabulary)."""
    src = (REPO_ROOT / "src" / "roam" / "runs" / "helpers.py").read_text(
        encoding="utf-8"
    )
    # Must mention the policy section by name.
    assert "Auto-log allowlist policy" in src
    # Must enumerate the four allowlist tiers.
    assert "Gate commands" in src
    assert "Constitution gates" in src
    assert "Strategic commands" in src
    # Must enumerate the intentional exclusions (positive vocabulary).
    for cmd in ("health", "search", "index", "complexity"):
        assert cmd in src, f"exclusion list missing '{cmd}'"
