"""Pin the help-text template for high-leverage commands (R8.G9).

Every command listed in ``CURATED_COMMANDS`` below MUST have a
docstring that:

1. Opens with a one-line summary.
2. Includes either an ``Examples:`` block (with at least 2 example
   invocations) OR Click's preserve-format marker ``\\b`` followed by
   ``Examples:`` and example lines.
3. Mentions at least one related command via a "See also" / "see also"
   reference (any case, any inline form — the goal is that newcomers
   discover related tooling without re-reading the whole help index).

The list grows incrementally as commands are polished. New commands
SHOULD aim for the template; ones in the long tail (~210 commands at
the time of writing) are NOT held to it yet — adding them here when
ready is how we ratchet up consistency over time.
"""

from __future__ import annotations

import importlib

import pytest


CURATED_COMMANDS: tuple[str, ...] = (
    # Onboarding + doctoring (G2 must-haves)
    "init",
    "doctor",
    # The five Start-here verbs (the help panel surfaces these)
    "understand",
    "context",
    "preflight",
    "critique",
    "ask",
    # Daily workflow staples
    "search",
    "impact",
    "diff",
    "retrieve",
    "mcp-setup",
    # === Ratchet wave 2 (next 20 highest-leverage commands) ===
    # Codebase navigation primitives (CLAUDE.md "Codebase navigation
    # with roam" section + docs site staples)
    "tour",
    "file",
    "uses",  # ``refs`` is the alias; both surface the same docstring.
    "trace",
    "deps",
    # Health, risk, and verdicts
    "health",
    "diagnose",
    "complexity",
    "pr-risk",
    "affected-tests",
    "fan",
    # Code quality detectors
    "n1",
    "clones",
    # Architecture transforms
    "simulate",
    "mutate",
    # Indexing + maintenance
    "index",
    "watch",
    # Security + audit
    "taint",
    "vuln-reach",
    "attest",
    # === Ratchet wave 3 (next 20 high-leverage commands) ===
    # Refactoring planning + simulation
    "plan",
    "plan-refactor",
    "suggest-refactoring",
    # Architecture + multi-agent
    "partition",
    "layers",
    "cut",
    "orchestrate",
    "agent-plan",
    "agent-context",
    # PR + review
    "pr-diff",
    # Verdict-style explainers
    "why",
    "intent",
    "capabilities",
    "capsule",
    # Trends + audit + ownership
    "trends",
    "audit",
    "bus-factor",
    # Dataflow + search variants
    "effects",
    "grep",
    # CI integration
    "ci-setup",
)


def _load_command(name: str):
    from roam.cli import _COMMANDS
    if name not in _COMMANDS:
        pytest.fail(f"command {name!r} is not registered in roam.cli._COMMANDS")
    mod_path, fn_name = _COMMANDS[name]
    mod = importlib.import_module(mod_path)
    fn = getattr(mod, fn_name, None)
    if fn is None:
        pytest.fail(f"could not import {mod_path}.{fn_name} for {name!r}")
    return fn


def _docstring(fn) -> str:
    """Click commands sometimes carry help on the decorator and
    sometimes on ``__doc__``. Return whichever is non-empty."""
    return (fn.help if getattr(fn, "help", None) else (fn.__doc__ or "")) or ""


@pytest.mark.parametrize("name", CURATED_COMMANDS)
def test_curated_command_has_examples_block(name):
    fn = _load_command(name)
    doc = _docstring(fn)
    assert doc.strip(), f"{name!r} has an empty docstring"
    has_examples = "Examples:" in doc or "examples:" in doc
    has_format_marker = "\b" in doc and ("roam " in doc or "$ " in doc)
    assert has_examples or has_format_marker, (
        f"{name!r} is missing an Examples block. Add either:\n"
        f"  Examples:\n"
        f"    roam {name} ...\n"
        f"or with Click's preserve-format marker:\n"
        f"  \\b\n  Examples:\n  roam {name} ..."
    )


@pytest.mark.parametrize("name", CURATED_COMMANDS)
def test_curated_command_has_see_also(name):
    fn = _load_command(name)
    doc = _docstring(fn).lower()
    assert "see also" in doc or "see ``" in doc, (
        f"{name!r} docstring is missing a 'See also' / cross-reference. "
        f"Add a line at the bottom such as:\n"
        f"  See also ``related-command``, ``other-command``."
    )


@pytest.mark.parametrize("name", CURATED_COMMANDS)
def test_curated_command_has_summary_line(name):
    """First non-empty line should be a sentence ending in ``.`` —
    Click renders this as the command's one-line description in
    ``roam --help``."""
    fn = _load_command(name)
    doc = _docstring(fn)
    first = next((ln.strip() for ln in doc.splitlines() if ln.strip()), "")
    assert first.endswith(".") or first.endswith(":"), (
        f"{name!r} docstring's first line should end with '.' (or ':') — "
        f"got: {first!r}"
    )
