"""Tests for ``cmd_dogfood_aggregate._parse_frontmatter_yaml`` (W1053).

W1045 flagged the function for a bare-except scope. W1053 narrowed the
catch from ``except Exception`` to ``(ImportError, AttributeError)`` plus a
nested ``yaml.YAMLError`` handler — process-control exceptions
(``KeyboardInterrupt``, ``SystemExit``, ``MemoryError``) now propagate, but
the two-engine dual-parse pattern is preserved.

The function is a dev-facing parser for Markdown frontmatter in the
dogfood corpus (`internal/dogfood/evals/`), so the silent-fallback
contract is intentional: callers expect "best-effort dict, never crash"
because eval files are hand-edited Markdown and stray colons in
observation rows are normal.
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_dogfood_aggregate import _parse_frontmatter_yaml

# ---------------------------------------------------------------------------
# Scenario 1: valid YAML frontmatter -> clean parse, no fallthrough
# ---------------------------------------------------------------------------


def test_valid_frontmatter_parses_via_yaml_engine():
    """Well-formed YAML frontmatter: every key/value lands intact."""
    text = "command: complexity\ndate: 2026-05-12\nroam_version: 12.50\nstatus: open\nverdict: use-with-caveats\n"
    parsed = _parse_frontmatter_yaml(text)
    assert parsed["command"] == "complexity"
    assert parsed["date"] == "2026-05-12"
    assert parsed["status"] == "open"
    assert parsed["verdict"] == "use-with-caveats"
    # Numeric scalar stringified — the helper coerces every value to str.
    assert parsed["roam_version"] == "12.5"


def test_quoted_strings_round_trip_via_yaml_engine():
    """PyYAML strips the surrounding quotes; engine selection matters."""
    text = "task: \"with: a colon\"\nverdict: 'single-quoted'\n"
    parsed = _parse_frontmatter_yaml(text)
    assert parsed["task"] == "with: a colon"
    assert parsed["verdict"] == "single-quoted"


# ---------------------------------------------------------------------------
# Scenario 2: malformed YAML -> regex fallback engages, no crash
# ---------------------------------------------------------------------------


def test_malformed_yaml_falls_through_to_regex_engine():
    """YAML parse failure must NOT crash — regex sweep extracts what it can."""
    # A `: : :` triple breaks PyYAML's parser; the regex engine should still
    # pick up the surrounding key/value lines.
    text = "command: complexity\nbroken: : : :\nstatus: open\n"
    parsed = _parse_frontmatter_yaml(text)
    # Regex engine recovers the clean keys.
    assert parsed.get("command") == "complexity"
    assert parsed.get("status") == "open"


def test_completely_malformed_returns_dict_not_crash():
    """Even a fully broken document yields a dict — the silent-fallback contract."""
    text = "{[}: : invalid yaml ::: :\n"
    parsed = _parse_frontmatter_yaml(text)
    # Contract: always a dict, never an exception.
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Scenario 3: missing PyYAML -> regex engine handles flat shape
# ---------------------------------------------------------------------------


def test_missing_pyyaml_uses_regex_engine(no_pyyaml):
    """When PyYAML import fails, the regex engine still extracts flat KV pairs."""
    text = "command: dead\ndate: 2026-05-12\nstatus: fixed-in-v1\n"
    parsed = _parse_frontmatter_yaml(text)
    assert parsed["command"] == "dead"
    assert parsed["date"] == "2026-05-12"
    assert parsed["status"] == "fixed-in-v1"


# ---------------------------------------------------------------------------
# Scenario 4 (drive-by): empty / missing frontmatter
# ---------------------------------------------------------------------------


def test_empty_text_returns_empty_dict():
    """Empty text yields an empty dict — legitimate "no frontmatter" case."""
    assert _parse_frontmatter_yaml("") == {}


def test_only_comments_returns_empty_dict():
    """Comment-only frontmatter yields no rows."""
    text = "# just a comment\n# another\n"
    parsed = _parse_frontmatter_yaml(text)
    # PyYAML parses comments-only as None; regex engine skips `#`-prefixed
    # lines explicitly. Either path lands on an empty result.
    # (PyYAML returns None which fails isinstance(dict), so regex runs and
    #  finds nothing.)
    assert parsed == {}


# ---------------------------------------------------------------------------
# Scenario 5 (W1053 invariant): process-control exceptions must propagate
# ---------------------------------------------------------------------------


def test_keyboard_interrupt_during_yaml_load_propagates(monkeypatch):
    """The narrowed catch MUST let KeyboardInterrupt escape — that's the W1053 fix."""
    import yaml

    def boom(_text):
        raise KeyboardInterrupt()

    monkeypatch.setattr(yaml, "safe_load", boom)

    with pytest.raises(KeyboardInterrupt):
        _parse_frontmatter_yaml("command: x\n")


def test_system_exit_during_yaml_load_propagates(monkeypatch):
    """SystemExit must also escape — old bare-except would have swallowed it."""
    import yaml

    def boom(_text):
        raise SystemExit(2)

    monkeypatch.setattr(yaml, "safe_load", boom)

    with pytest.raises(SystemExit):
        _parse_frontmatter_yaml("command: x\n")
