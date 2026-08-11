"""Drift-guard: every ``roam_*`` name an MCP tool mentions must be a real tool.

Why this drift-guard exists
---------------------------

MCP tool descriptions are the *only* thing an agent sees before it decides
which tool to call. They are therefore instructions, not prose. Several of
roam's descriptions use a "Different from ``roam_x`` (...) -- this is ..."
disambiguation clause to steer agents to the right neighbour tool.

On 2026-08-11 an audit found three such clauses naming tools that are
registered nowhere:

* ``roam_runs_verify`` — cited by ``roam_agent_score``. The real surface is
  the CLI ``roam runs verify`` (:mod:`roam.commands.cmd_runs`); it is
  deliberately not an MCP tool.
* ``roam_pr_replay`` — cited by ``roam_postmortem``. The real surface is the
  CLI ``roam pr-replay`` (:mod:`roam.commands.cmd_pr_replay`); likewise not
  an MCP tool.
* ``roam_neighbours`` — cited by ``roam_recommend``. This one never existed
  at all: no MCP tool, no CLI command, no module.

All three shipped in ``docs/mcp-tools.md`` and in the live tool surface. An
agent that followed the instruction got a tool-not-found error at best, and
at worst abandoned the sub-task. No existing gate caught it:
``dev/build_readme_counts.py`` only checks that the generated doc matches the
docstrings — it happily propagates a phantom name from source into the doc,
and ``tests/test_doc_no_phantom_cli.py`` checks a hardcoded 9-pattern list of
*CLI* shapes in three root markdown files, which does not cover MCP tool
names in tool descriptions at all.

What this asserts
-----------------

Referential integrity, not counts (counting is owned elsewhere):

    For every registered MCP tool description, every ``roam_<identifier>``
    token appearing in that description must itself be the name of a
    registered MCP tool.

and the same over the generated ``docs/mcp-tools.md``, which is what a human
or an agent reads out-of-band.

The registry is read from :func:`roam.surface_counts.mcp_tool_descriptions`,
the same source of truth the doc generator uses — so the guard cannot drift
away from the surface it is guarding.

Deliberately NOT asserted here: the number of tools, the number of presets,
or any other count. Those belong to the count-checking gate.

How to fix a failure
--------------------

A violation means a tool description points an agent at something that does
not exist. Two valid resolutions:

1. The referenced capability exists on the **CLI only** — say so explicitly,
   e.g. "the CLI ``roam runs verify``". Prose that names a CLI invocation
   (``roam runs verify``) does not match the ``roam_x`` token shape and so
   does not trip this guard.
2. The referenced capability does not exist — delete the clause.

Do NOT add the phantom to an allow-list. There is deliberately no allow-list:
the whole point is that a name in an instruction must resolve.
"""

from __future__ import annotations

import re

import pytest

from roam.surface_counts import mcp_tool_descriptions
from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()

#: Generated reference page. Regenerated from the ``@_tool`` decorations by
#: ``dev/build_readme_counts.py``; checked here as a second surface because it
#: is what ships to readers who are not holding a live MCP session.
_GENERATED_TOOL_DOC = "docs/mcp-tools.md"

#: A ``roam_<identifier>`` token. Matches the tool-name shape used throughout
#: the descriptions (``roam_pr_risk``, ``roam_batch_get``, ...). Deliberately
#: does NOT match the CLI form ``roam pr-replay`` (space + hyphen), so a
#: description may legitimately point at a CLI-only surface.
_TOOL_TOKEN = re.compile(r"\broam_[a-z0-9_]+")


def _registered_tool_names() -> frozenset[str]:
    """Names of every MCP tool registered in the ``full`` preset."""
    return frozenset(name for name, _ in mcp_tool_descriptions())


def _phantom_refs(text: str, registered: frozenset[str]) -> list[str]:
    """Return the ``roam_*`` tokens in *text* that are not registered tools."""
    return [tok for tok in _TOOL_TOKEN.findall(text) if tok not in registered]


def test_registry_is_non_empty() -> None:
    """Sanity: the guard must be reading a populated registry.

    Without this, an import regression that made ``mcp_tool_descriptions()``
    return ``[]`` would turn every assertion below into a vacuous pass — the
    exact false-green shape this guard exists to prevent.
    """
    registered = _registered_tool_names()
    assert len(registered) > 100, (
        f"mcp_tool_descriptions() returned only {len(registered)} tools; the "
        "guard below would be vacuous. Fix the registry read, do not relax "
        "this assertion."
    )
    assert "roam_uses" in registered, (
        "roam_uses missing from the registry read — the guard is not looking at the real MCP surface."
    )


def test_tool_descriptions_reference_only_registered_tools() -> None:
    """No MCP tool description may name a ``roam_*`` tool that isn't registered."""
    registered = _registered_tool_names()
    violations: list[str] = []
    for name, description in mcp_tool_descriptions():
        for phantom in _phantom_refs(description, registered):
            violations.append(f"{name}: description references '{phantom}', which is not a registered MCP tool")
    assert not violations, (
        "MCP tool description(s) instruct an agent to call a tool that is "
        "registered nowhere. The agent gets tool-not-found. Either name the "
        "CLI surface explicitly (e.g. 'the CLI ``roam runs verify``') or "
        "delete the clause. Offenders:\n  " + "\n  ".join(violations)
    )


def test_generated_tool_doc_references_only_registered_tools() -> None:
    """``docs/mcp-tools.md`` may not name a ``roam_*`` tool that isn't registered.

    The page is generated, so in the normal case this is implied by the test
    above. It is asserted separately because the generated file is committed:
    if it is ever regenerated stale, hand-edited, or reverted by a concurrent
    change, readers still see the phantom.
    """
    registered = _registered_tool_names()
    doc_path = REPO_ROOT / _GENERATED_TOOL_DOC
    assert doc_path.exists(), f"{_GENERATED_TOOL_DOC} not found at {doc_path}"
    violations: list[str] = []
    for line_no, line in enumerate(doc_path.read_text(encoding="utf-8").splitlines(), start=1):
        for phantom in _phantom_refs(line, registered):
            violations.append(
                f"{_GENERATED_TOOL_DOC}:{line_no}: references '{phantom}', which is not a registered MCP tool"
            )
    assert not violations, (
        f"{_GENERATED_TOOL_DOC} names MCP tool(s) that do not exist. If the "
        "source docstrings are already correct, regenerate with "
        "`python dev/build_readme_counts.py --apply`. Offenders:\n  " + "\n  ".join(violations)
    )


@pytest.mark.parametrize(
    "phantom_text",
    [
        "Different from ``roam_neighbours`` (graph-only 1-hop neighbours).",
        "Pair with ``roam_runs_verify`` for ledger tamper-detection.",
        "Different from ``roam_pr_replay`` (one PR replay).",
    ],
)
def test_detector_fires_on_known_phantoms(phantom_text: str) -> None:
    """Negative control: the detector must FLAG the three real 2026-08-11 phantoms.

    A guard nobody has watched fail is not a guard. These are the exact
    strings that shipped in ``roam_recommend`` / ``roam_agent_score`` /
    ``roam_postmortem``; the detector must reject each one.
    """
    registered = _registered_tool_names()
    assert _phantom_refs(phantom_text, registered), (
        f"detector did NOT flag known phantom text {phantom_text!r} — the "
        "guard is vacuous and would pass on the very defect it was written "
        "for."
    )


def test_detector_accepts_a_real_tool_and_a_cli_reference() -> None:
    """Negative control, other direction: no false positives on valid prose.

    A guard that flags everything is as useless as one that flags nothing.
    A real tool name must pass, and the CLI escape hatch documented in the
    module docstring (``roam runs verify``, space-separated) must not be
    mistaken for a tool token.
    """
    registered = _registered_tool_names()
    assert not _phantom_refs("Different from ``roam_impact`` (transitive blast radius).", registered), (
        "detector flagged roam_impact, which IS registered — false positive"
    )
    assert not _phantom_refs(
        "Pair with the CLI ``roam runs verify`` and ``roam pr-replay``.",
        registered,
    ), "detector flagged a CLI invocation as a tool token — false positive"
