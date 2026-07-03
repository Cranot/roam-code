"""Characterization tests for ``roam.commands._command_utils.bare_command_name``.

``bare_command_name`` is the ONE canonical definition of the "reduce a raw
command string to its bare verb" parsing rule (W878 consolidated the three
prior inlined copies in ``cmd_next.py``, ``constitution/loader.py`` and
``modes/policy.py``). Its consumers all rely on two load-bearing contracts:

* the **empty/falsy contract** -- falsy or unparseable input yields ``""`` so
  every call-site's ``if not bare:`` guard (modes policy, constitution loader,
  ``cmd_next``) keeps working;
* the **exact-verb contract** -- the extracted token is compared by membership
  (``bare in known`` / ``bare in mode.allowed_commands``), so any stray prefix,
  flag, placeholder, or surrounding whitespace must be stripped away.

These tests pin both contracts plus the documented input forms, so a refactor
of the single definition can't silently drift any of the four call-sites.

Coverage scope: the function is type-hinted ``str``; only ``str`` and falsy
(``""`` / ``None``) inputs are in-scope. Truthy non-string values are not part
of the contract and are deliberately not exercised.
"""

from __future__ import annotations

import pytest

from roam.commands._command_utils import bare_command_name


@pytest.mark.parametrize(
    "raw",
    ["", None],
    ids=["empty_string", "none"],
)
def test_falsy_input_returns_empty_string(raw):
    # Every consumer guards with `if not bare:` -- falsy MUST collapse to "".
    assert bare_command_name(raw) == ""


def test_whitespace_only_input_returns_empty_string():
    # A non-empty but whitespace-only string is truthy, so it bypasses the
    # falsy guard; the `else s` fallback (s == "" after .strip()) must still
    # yield "" rather than a bare flag fragment.
    assert bare_command_name("   \n\t  ") == ""


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Already-bare verb passes through untouched.
        ("preflight", "preflight"),
        # The documented headline form: strip the `roam ` prefix + placeholder.
        ("roam preflight <sym>", "preflight"),
        # Flags after the `roam ` prefix are dropped (documented form).
        ("roam --json preflight", "preflight"),
        # Flags without a `roam ` prefix are also dropped.
        ("--json preflight", "preflight"),
        # Multiple leading flags collapse, first real verb wins.
        ("--json --sarif health", "health"),
        # Only the first non-flag token matters; trailing args + flags ignored.
        ("roam preflight --severity fail src/app.py", "preflight"),
        # Non-dash-prefixed placeholder (e.g. `<sym>`) is a later token, ignored.
        ("roam impact handleSave", "impact"),
    ],
    ids=[
        "bare_verb",
        "roam_prefix_with_placeholder",
        "flags_after_roam_prefix",
        "flags_without_roam_prefix",
        "multiple_leading_flags",
        "trailing_args_and_flags_ignored",
        "verb_followed_by_symbol",
    ],
)
def test_realistic_invocation_forms_extract_bare_verb(raw, expected):
    assert bare_command_name(raw) == expected


def test_normalizes_surrounding_and_internal_whitespace():
    # Guards the subtle `.lstrip()` applied AFTER the `s = s[5:]` slice:
    # collapsing multiple spaces between `roam` and the verb. Removing that
    # lstrip would yield a leading-space token and break the exact-verb
    # membership comparisons at every call-site.
    assert bare_command_name("  roam   preflight  ") == "preflight"


def test_bare_roam_with_no_subcommand_returned_verbatim():
    # "roam" with no trailing space does not match the `startswith("roam ")`
    # prefix, so it is returned as-is -- NOT collapsed to "". Pins the
    # boundary between "roam <nothing>" (verbatim) and whitespace-only ("").
    assert bare_command_name("roam") == "roam"


def test_all_flags_no_real_verb_returns_stripped_flag_string():
    # Characterization of the `tokens[0] if tokens else s` fallback: once every
    # token is a flag there is no real verb, so the stripped remainder (`s`)
    # is returned. Degenerate but stable -- pinned so a refactor that returns
    # "" here (also plausible) is a deliberate, reviewed change.
    assert bare_command_name("roam --json") == "--json"
