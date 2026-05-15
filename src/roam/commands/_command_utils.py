"""Shared command-name parsing helpers.

This module holds tiny, dependency-free string helpers that several
substrates need to canonicalise a command invocation back to its bare
verb (``"roam preflight <sym>" -> "preflight"``). It lives at the
``roam.commands`` package level — not inside any ``cmd_*.py`` — so
substrates outside the CLI dispatch layer (constitution loader, modes
policy) can import it without pulling Click or any command-module side
effects.

History: prior to W878 the same helper was inlined three times — once
in ``cmd_next.py``, once in ``constitution/loader.py`` (with a stale
"avoid import cycle" hedge that W902 verified was false), and twice in
``modes/policy.py`` (as ``_normalise_command`` and a thin
``_bare_command_name`` wrapper on top). Consolidated here so the parsing
rule has exactly one definition.
"""

from __future__ import annotations


def bare_command_name(verdict_cmd: str) -> str:
    """Extract a canonical command name (just the verb) from a raw string.

    Accepts forms like ``"roam preflight <sym>"``, ``"preflight"``, or
    ``"roam --json preflight"`` and returns the leading subcommand verb.
    Empty or falsy input returns ``""`` (defensive — historically the
    modes-policy variant guarded this; preserving that behaviour keeps
    every prior call-site safe).
    """
    if not verdict_cmd:
        return ""
    s = verdict_cmd.strip()
    # Strip a leading 'roam '
    if s.startswith("roam "):
        s = s[5:].lstrip()
    # Drop any leading flags like '--json'
    tokens = [t for t in s.split() if t and not t.startswith("-")]
    return tokens[0] if tokens else s
