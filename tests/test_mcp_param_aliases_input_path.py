"""W332 — input_path alias-normalization tests (Pattern 3b extension).

Pattern 3b at the MCP boundary: the four legacy "path-to-an-input-file"
parameters (``rules_path`` / ``rules_file`` / ``statement_path`` /
``envelope_path``) collapse to a single canonical ``input_path``.

This module tests:

1. The normalizer translates each of the four legacy names to
   ``input_path`` when the tool's signature declares ``input_path``
   (and only then — distinct-semantics tools are untouched).
2. A deprecation warning is appended under
   ``summary.alias_warnings`` matching the existing 3-canonical
   behaviour.
3. Canonical ``input_path`` callers see no warning.
4. End-to-end dispatch on the actual ``roam_rules_validate`` /
   ``roam_cga_verify`` / ``roam_pr_comment_render`` /
   ``roam_audit_trail_verify`` / ``roam_audit_trail_export`` /
   ``roam_audit_trail_conformance_check`` / ``roam_dogfood`` /
   ``roam_pr_analyze`` wrappers translates aliases and reaches the
   underlying CLI args correctly.

Runs without ``fastmcp`` installed — the helpers under test are pure
Python (same harness style as ``test_mcp_param_aliases.py``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_dispatch_policy(monkeypatch):
    """Alias forwarding is independent of repo-local mode policy state."""
    monkeypatch.setenv("ROAM_MCP_DISABLE_COLD_START_GUARD", "1")
    monkeypatch.setenv("ROAM_MODE_ENFORCEMENT", "0")


# ---------------------------------------------------------------------------
# Direct unit tests on the helper for each W332 alias
# ---------------------------------------------------------------------------


def test_input_path_alias_resolves_rules_path():
    """``rules_path`` -> ``input_path`` when canonical is accepted."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "roam_rules_validate",
        {"rules_path": ".roam/rules.yml"},
        accepted={"input_path"},
    )
    assert out == {"input_path": ".roam/rules.yml"}
    assert len(warns) == 1
    assert "rules_path" in warns[0]
    assert "input_path" in warns[0]
    assert "deprecated" in warns[0]


def test_input_path_alias_resolves_rules_file():
    """``rules_file`` -> ``input_path`` (the dogfood compound's legacy name)."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "roam_dogfood",
        {"rules_file": ".roam/rules.yml"},
        accepted={"input_path"},
    )
    assert out == {"input_path": ".roam/rules.yml"}
    assert len(warns) == 1
    assert "rules_file" in warns[0]


def test_input_path_alias_resolves_statement_path():
    """``statement_path`` -> ``input_path`` (the cga_verify legacy name)."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "roam_cga_verify",
        {"statement_path": "/tmp/cga.json"},
        accepted={"input_path"},
    )
    assert out == {"input_path": "/tmp/cga.json"}
    assert len(warns) == 1
    assert "statement_path" in warns[0]


def test_input_path_alias_resolves_envelope_path():
    """``envelope_path`` -> ``input_path`` (the pr_comment_render legacy name)."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "roam_pr_comment_render",
        {"envelope_path": "/tmp/pr-analyze.json"},
        accepted={"input_path"},
    )
    assert out == {"input_path": "/tmp/pr-analyze.json"}
    assert len(warns) == 1
    assert "envelope_path" in warns[0]


def test_input_path_alias_skipped_when_canonical_not_accepted():
    """A tool whose signature doesn't declare ``input_path`` must not see
    its ``rules_path`` argument silently rewritten — Pattern 3b alias
    behaviour respects the per-tool ``accepted`` filter.

    This is the W332 ambiguity guard: ``pr_analyze`` deliberately
    keeps ``input_path`` for its sidecar rules pack, but a tool that
    doesn't declare ``input_path`` must NEVER see a phantom rewrite.
    """
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "hypothetical_tool_without_input_path",
        {"rules_path": ".roam/rules.yml"},
        accepted={"some_other_param"},  # input_path NOT in this set
    )
    # Legacy name passes through untouched.
    assert out == {"rules_path": ".roam/rules.yml"}
    assert warns == []


def test_canonical_input_path_no_warning():
    """Canonical ``input_path=`` produces no rewrite, no warning."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "roam_rules_validate",
        {"input_path": ".roam/rules.yml"},
        accepted={"input_path"},
    )
    assert out == {"input_path": ".roam/rules.yml"}
    assert warns == []


def test_input_path_canonical_wins_when_alias_also_supplied():
    """Both canonical + legacy alias supplied -> canonical wins, alias dropped
    with ``ignoring`` warning. Mirrors the existing 3-canonical behaviour."""
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "roam_rules_validate",
        {"rules_path": "wrong.yml", "input_path": "right.yml"},
        accepted={"input_path"},
    )
    assert out == {"input_path": "right.yml"}
    assert len(warns) == 1
    assert "ignoring" in warns[0]
    assert "rules_path" in warns[0]


def test_alias_promoted_when_canonical_at_signature_default():
    """2026-05-24 regression: when FastMCP fills the canonical from the
    wrapped function's declared default while the caller only sets the
    alias, the alias value must be promoted to the canonical (rule 2a),
    NOT dropped as a "duplicate".

    Before the fix, ``roam_search_symbol(pattern='_FOO')`` triggered
    rule 2b ("ignoring 'pattern' (use 'query' only)") because FastMCP
    passed ``query=''`` (the wrapped fn's default) alongside the
    user-supplied ``pattern='_FOO'``. The wrapped function then saw
    ``query=''`` and the underlying CLI raised EMPTY_INPUT.

    With the ``defaults`` arg, we detect that ``query`` matches its
    declared default and treat it as "not user-set", so the alias wins.
    """
    from roam.mcp_server import _normalize_aliases

    # Simulates the post-FastMCP-dispatch state for roam_search_symbol:
    # caller passed pattern='_FOO'; FastMCP also filled query='' (default).
    out, warns = _normalize_aliases(
        "roam_search_symbol",
        {"pattern": "_FOO", "query": ""},  # query at signature default
        accepted={"query"},
        defaults={"query": ""},
    )
    assert out == {"query": "_FOO"}, (
        f"alias value should be promoted to canonical when canon is at its declared default. Got: {out}"
    )
    assert len(warns) == 1
    assert "deprecated" in warns[0]
    assert "pattern" in warns[0]
    assert "query" in warns[0]
    # NOT an "ignoring" warning — that would mean the value was dropped.
    assert "ignoring" not in warns[0]


def test_canonical_wins_when_both_user_set_and_canon_differs_from_default():
    """Inverse of the bug-fix test: when BOTH canon and alias are user-set
    (canon differs from declared default), canonical still wins per rule 2b.

    Ensures the defaults-based fix doesn't accidentally regress the
    existing duplicate-detection behavior. Mirrors
    test_input_path_canonical_wins_when_alias_also_supplied but with the
    defaults arg explicitly passed.
    """
    from roam.mcp_server import _normalize_aliases

    out, warns = _normalize_aliases(
        "roam_rules_validate",
        {"rules_path": "wrong.yml", "input_path": "right.yml"},
        accepted={"input_path"},
        defaults={"input_path": ""},  # default is empty; user set "right.yml" — NOT default
    )
    assert out == {"input_path": "right.yml"}
    assert len(warns) == 1
    assert "ignoring" in warns[0]
    assert "rules_path" in warns[0]


# ---------------------------------------------------------------------------
# Parametrized regression for the 9-tool bug class found 2026-05-24
# ---------------------------------------------------------------------------
#
# Audit found 9 real wrappers where the canonical param has a non-empty
# default (e.g. ``query: str = ""``, ``symbol: str = ""``,
# ``input_path: str = ""``). Pre-fix, an alias-only call would have its
# value silently dropped because FastMCP fills the canonical with its
# declared default, then ``_normalize_aliases`` saw both kwargs and fired
# the rule 2b "BOTH supplied" branch.
#
# Affected wrappers (from 2026-05-24 audit, src/roam/mcp_server.py):
#   - roam_explore       (line 5599):  symbol = ""
#   - roam_effects       (line 10757): symbol = "", path = ""
#   - roam_pr_analyze    (line  9005): input_path = ""
#   - roam_get_annotations (line 10508): symbol = ""
#   - roam_generate_plan (line 10968): symbol = "", path = ""
#   - roam_for_security_review (line 7939): symbol = ""
#   - roam_cga_verify    (line  8756): input_path = ""
#   - roam_pr_comment_render (line 9083): input_path = ""
#   - roam_forecast      (line 10934): symbol = ""
#
# Rule 2a (in _normalize_aliases) handles them all generically via the
# defaults dict — no per-tool patch needed. This parametrized test fires
# an end-to-end alias-only call against a synthetic wrapper per
# canonical/alias pair to prove the rule 2a fix protects every entry in
# ``_PARAM_ALIASES`` (not just the ``pattern → query`` case the bug was
# originally reported against).


import pytest as _pytest


@_pytest.mark.parametrize(
    "canonical,alias",
    [
        # symbol family — covers roam_explore, roam_effects, roam_forecast,
        # roam_get_annotations, roam_generate_plan, roam_for_security_review
        ("symbol", "name"),
        ("symbol", "target"),
        ("symbol", "subject"),
        # path family — covers roam_effects, roam_generate_plan
        ("path", "file"),
        ("path", "file_path"),
        ("path", "filename"),
        ("path", "filepath"),
        # query family — covers roam_search_symbol (the original bug)
        ("query", "pattern"),
        # input_path family — covers roam_pr_analyze, roam_cga_verify,
        # roam_pr_comment_render, plus the W332 canonical
        ("input_path", "rules_path"),
        ("input_path", "rules_file"),
        ("input_path", "statement_path"),
        ("input_path", "envelope_path"),
    ],
    ids=lambda v: v,  # readable test IDs: symbol-name, path-file, ...
)
def test_alias_promoted_for_every_canonical_with_empty_default(canonical, alias):
    """For every canonical/alias pair in _PARAM_ALIASES, an alias-only call
    against a wrapper that declares ``<canonical>: str = ""`` must reach
    the wrapped function with the alias value.

    Pre-fix (rule 2b only): FastMCP filled the canonical with "" (the
    declared default), _normalize_aliases saw both, dropped the alias.
    Post-fix (rule 2a): defaults-aware check detects canon-at-default
    and promotes the alias instead.
    """
    from roam.mcp_server import _wrap_with_alias_normalization

    # Synthesize a wrapper with the canonical declared at empty-string
    # default — mirrors the real wrappers identified in the 2026-05-24 audit.
    def _make_fn(canon: str):
        if canon == "symbol":

            def fn(symbol: str = "", root: str = "."):
                return {"command": "fake", "data": {"received": symbol}}
        elif canon == "path":

            def fn(path: str = "", root: str = "."):
                return {"command": "fake", "data": {"received": path}}
        elif canon == "query":

            def fn(query: str = "", root: str = "."):
                return {"command": "fake", "data": {"received": query}}
        elif canon == "input_path":

            def fn(input_path: str = "", root: str = "."):
                return {"command": "fake", "data": {"received": input_path}}
        else:
            raise AssertionError(f"unhandled canonical {canon!r}")
        return fn

    fn = _make_fn(canonical)
    wrapped = _wrap_with_alias_normalization(f"fake_{canonical}", fn)

    # Alias-only call: the value the user actually wants.
    result = wrapped(**{alias: "user_value"})

    assert result["data"]["received"] == "user_value", (
        f"alias '{alias}' was dropped instead of promoted to canonical "
        f"'{canonical}'. Got: {result}. Pre-fix bug: rule 2b 'BOTH supplied' "
        f"fired because FastMCP filled '{canonical}' with default ''."
    )
    warns = result["summary"]["alias_warnings"]
    assert len(warns) == 1, f"expected exactly 1 deprecation warning, got: {warns}"
    assert "deprecated" in warns[0], (
        f"expected 'deprecated' (rule 2a path), got: {warns[0]}. "
        f"If you see 'ignoring', rule 2b fired — the fix is broken."
    )
    assert alias in warns[0], f"warning should name alias '{alias}': {warns[0]}"
    assert canonical in warns[0], f"warning should name canonical '{canonical}': {warns[0]}"


def test_alias_dropped_when_both_user_set_and_canon_differs_from_default():
    """Companion to the parametrized test: when BOTH are user-set and
    canon is NOT at its default, canon still wins (rule 2b unchanged).

    This is the inverse guard — if it ever flips to "alias wins" we've
    broken the duplicate-detection contract.
    """
    from roam.mcp_server import _wrap_with_alias_normalization

    def fn(symbol: str = "", root: str = "."):
        return {"command": "fake", "data": {"received": symbol}}

    wrapped = _wrap_with_alias_normalization("fake_symbol_dup", fn)

    # Both supplied, canon is user-set (not at default "").
    result = wrapped(symbol="canon_wins", target="alias_value")

    assert result["data"]["received"] == "canon_wins"
    warns = result["summary"]["alias_warnings"]
    assert len(warns) == 1
    assert "ignoring" in warns[0]


# ---------------------------------------------------------------------------
# _PARAM_ALIASES table — the four new entries are present
# ---------------------------------------------------------------------------


def test_param_aliases_table_has_input_path_canonical():
    """Sanity: ``input_path`` is a canonical with four legacy aliases."""
    from roam.mcp_server import _PARAM_ALIASES

    assert "input_path" in _PARAM_ALIASES
    aliases = set(_PARAM_ALIASES["input_path"].keys())
    expected = {"rules_path", "rules_file", "statement_path", "envelope_path"}
    assert expected.issubset(aliases), f"Expected {expected} in _PARAM_ALIASES['input_path'], got {aliases}"


def test_param_aliases_emit_deprecation_warning():
    """``_attach_alias_warnings`` surfaces W332 deprecation under
    ``summary.alias_warnings``. End-to-end on the wrapper machinery."""
    from roam.mcp_server import _wrap_with_alias_normalization

    def fake_tool(input_path: str = "", root: str = ".") -> dict:
        return {"command": "fake_tool", "data": {"received_input_path": input_path}}

    wrapped = _wrap_with_alias_normalization("fake_tool", fake_tool)
    result = wrapped(rules_path=".roam/rules.yml")
    assert result["data"]["received_input_path"] == ".roam/rules.yml"
    warns = result["summary"]["alias_warnings"]
    assert len(warns) == 1
    assert "rules_path" in warns[0] and "input_path" in warns[0]


def test_wrapper_synthesised_signature_exposes_w332_aliases():
    """The synthesised signature must advertise the W332 aliases as
    optional kwargs so FastMCP / Pydantic schema generation lists ALL
    accepted spellings on the public tool surface."""
    import inspect

    from roam.mcp_server import _wrap_with_alias_normalization

    def fake_tool(input_path: str = "", root: str = ".") -> dict:
        return {}

    wrapped = _wrap_with_alias_normalization("fake_tool", fake_tool)
    sig = inspect.signature(wrapped)
    param_names = set(sig.parameters.keys())
    assert "input_path" in param_names  # canonical
    for alias in ("rules_path", "rules_file", "statement_path", "envelope_path"):
        assert alias in param_names, f"alias '{alias}' missing from synth signature"
        assert sig.parameters[alias].default is None


# ---------------------------------------------------------------------------
# End-to-end dispatch tests on the actual wrapper functions
#
# When ``fastmcp`` is installed, ``@_tool`` already wraps each tool with
# ``_wrap_with_alias_normalization`` at module-import time. When fastmcp
# is absent (the test environment in CI / dev), the bare function is
# returned and tests must apply the alias wrapper directly. The two
# branches converge on the SAME post-wrap callable, so test logic stays
# identical.
# ---------------------------------------------------------------------------


def _ensure_aliased(tool_name: str, fn):
    """Return a wrapper that has the W332 alias machinery applied, even
    when ``fastmcp`` isn't installed (in which case ``@_tool`` returns
    the bare function). Idempotent: a fn already wrapped is detected by
    the presence of ``input_path`` AND any alias on its synth signature.
    """
    import inspect

    from roam.mcp_server import _wrap_with_alias_normalization

    sig = inspect.signature(fn)
    param_names = set(sig.parameters.keys())
    # Already wrapped if any W332 alias is exposed alongside input_path.
    if "input_path" in param_names and (
        "rules_path" in param_names
        or "statement_path" in param_names
        or "envelope_path" in param_names
        or "rules_file" in param_names
    ):
        return fn
    return _wrap_with_alias_normalization(tool_name, fn)


def test_rules_validate_accepts_legacy_rules_path():
    """``rules_validate(rules_path=...)`` translates and reaches the CLI
    with the path as a positional arg."""
    from roam.mcp_server import rules_validate

    wrapped = _ensure_aliased("roam_rules_validate", rules_validate)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {"command": "roam_rules_validate", "data": []}
        result = wrapped(rules_path="custom-rules.yml")
        actual_args = mock.call_args[0][0]
        assert actual_args == ["rules-validate", "custom-rules.yml"]
    # Deprecation surfaced.
    warns = result.get("summary", {}).get("alias_warnings", [])
    assert any("rules_path" in w for w in warns)


def test_rules_validate_canonical_input_path_no_warning():
    """``rules_validate(input_path=...)`` is the new canonical — no warning."""
    from roam.mcp_server import rules_validate

    wrapped = _ensure_aliased("roam_rules_validate", rules_validate)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {
            "command": "roam_rules_validate",
            "summary": {"verdict": "ok"},
            "data": [],
        }
        result = wrapped(input_path="custom-rules.yml")
        actual_args = mock.call_args[0][0]
        assert actual_args == ["rules-validate", "custom-rules.yml"]
    # No alias used, summary must not have alias_warnings.
    assert "alias_warnings" not in result.get("summary", {})


def test_cga_verify_accepts_legacy_statement_path():
    """``cga_verify(statement_path=...)`` translates to input_path."""
    from roam.mcp_server import cga_verify

    wrapped = _ensure_aliased("roam_cga_verify", cga_verify)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {"command": "roam_cga_verify", "data": []}
        result = wrapped(statement_path="/tmp/cga.json")
        actual_args = mock.call_args[0][0]
        assert actual_args == ["cga", "verify", "/tmp/cga.json"]
    warns = result.get("summary", {}).get("alias_warnings", [])
    assert any("statement_path" in w for w in warns)


def test_cga_verify_canonical_input_path_no_warning():
    from roam.mcp_server import cga_verify

    wrapped = _ensure_aliased("roam_cga_verify", cga_verify)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {
            "command": "roam_cga_verify",
            "summary": {"verdict": "ok"},
            "data": [],
        }
        result = wrapped(input_path="/tmp/cga.json")
        actual_args = mock.call_args[0][0]
        assert actual_args == ["cga", "verify", "/tmp/cga.json"]
    assert "alias_warnings" not in result.get("summary", {})


def test_pr_comment_render_accepts_legacy_envelope_path():
    """``pr_comment_render(envelope_path=...)`` translates to input_path."""
    from roam.mcp_server import pr_comment_render

    wrapped = _ensure_aliased("roam_pr_comment_render", pr_comment_render)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {"command": "roam_pr_comment_render", "data": []}
        result = wrapped(envelope_path="/tmp/pr-analyze.json")
        actual_args = mock.call_args[0][0]
        # The CLI gets the path under --input.
        assert "--input" in actual_args
        assert "/tmp/pr-analyze.json" in actual_args
    warns = result.get("summary", {}).get("alias_warnings", [])
    assert any("envelope_path" in w for w in warns)


def test_pr_comment_render_canonical_input_path_no_warning():
    from roam.mcp_server import pr_comment_render

    wrapped = _ensure_aliased("roam_pr_comment_render", pr_comment_render)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {
            "command": "roam_pr_comment_render",
            "summary": {"verdict": "ok"},
            "data": [],
        }
        result = wrapped(input_path="/tmp/pr-analyze.json")
        actual_args = mock.call_args[0][0]
        assert "/tmp/pr-analyze.json" in actual_args
    assert "alias_warnings" not in result.get("summary", {})


def test_dogfood_accepts_legacy_rules_file():
    """``dogfood(rules_file=...)`` translates to input_path."""
    from roam.mcp_server import dogfood

    wrapped = _ensure_aliased("roam_dogfood", dogfood)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {"command": "roam_dogfood", "data": []}
        result = wrapped(rules_file="custom-rules.yml")
        actual_args = mock.call_args[0][0]
        # CLI receives --rules
        assert "--rules" in actual_args
        assert "custom-rules.yml" in actual_args
    warns = result.get("summary", {}).get("alias_warnings", [])
    assert any("rules_file" in w for w in warns)


def test_dogfood_canonical_input_path_no_warning():
    from roam.mcp_server import dogfood

    wrapped = _ensure_aliased("roam_dogfood", dogfood)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {
            "command": "roam_dogfood",
            "summary": {"verdict": "ok"},
            "data": [],
        }
        result = wrapped(input_path="custom-rules.yml")
        actual_args = mock.call_args[0][0]
        assert "--rules" in actual_args
        assert "custom-rules.yml" in actual_args
    assert "alias_warnings" not in result.get("summary", {})


def test_pr_analyze_accepts_legacy_rules_path():
    """``pr_analyze(rules_path=...)`` translates to input_path (sidecar
    rules pack). ``diff_path`` is the primary input and stays distinct."""
    from roam.mcp_server import pr_analyze

    wrapped = _ensure_aliased("roam_pr_analyze", pr_analyze)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {"command": "roam_pr_analyze", "data": []}
        result = wrapped(rules_path="custom-rules.yml")
        actual_args = mock.call_args[0][0]
        assert "--rules" in actual_args
        assert "custom-rules.yml" in actual_args
    warns = result.get("summary", {}).get("alias_warnings", [])
    assert any("rules_path" in w for w in warns)


def test_pr_analyze_canonical_input_path_no_warning():
    """``pr_analyze`` declares both ``diff_path`` (primary) and
    ``input_path`` (sidecar rules). Calling with the canonical
    ``input_path`` produces no warning."""
    from roam.mcp_server import pr_analyze

    wrapped = _ensure_aliased("roam_pr_analyze", pr_analyze)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {
            "command": "roam_pr_analyze",
            "summary": {"verdict": "ok"},
            "data": [],
        }
        result = wrapped(input_path="custom-rules.yml")
        actual_args = mock.call_args[0][0]
        assert "--rules" in actual_args
        assert "custom-rules.yml" in actual_args
    assert "alias_warnings" not in result.get("summary", {})


def test_audit_trail_verify_canonical_input_path_unchanged():
    """``audit_trail_verify`` already used ``input_path`` pre-W332. The
    canonical call must still produce no warning and reach the CLI as
    ``--input <path>``."""
    from roam.mcp_server import audit_trail_verify

    wrapped = _ensure_aliased("roam_audit_trail_verify", audit_trail_verify)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {
            "command": "roam_audit_trail_verify",
            "summary": {"verdict": "ok"},
            "data": [],
        }
        result = wrapped(input_path=".roam/audit-trail.jsonl")
        actual_args = mock.call_args[0][0]
        assert "--input" in actual_args
        assert ".roam/audit-trail.jsonl" in actual_args
    assert "alias_warnings" not in result.get("summary", {})


def test_audit_trail_verify_accepts_legacy_statement_path_alias():
    """W332 cross-canonical reach: ``audit_trail_verify`` declares
    ``input_path`` and now accepts ALL four W332 legacy aliases —
    including ones it never historically used (e.g. ``statement_path``).
    This is the silent-fail seal: any agent that gets the param-name
    wrong on a W332 tool gets a deprecation warning instead of silent
    misbind."""
    from roam.mcp_server import audit_trail_verify

    wrapped = _ensure_aliased("roam_audit_trail_verify", audit_trail_verify)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {"command": "roam_audit_trail_verify", "data": []}
        result = wrapped(statement_path=".roam/audit-trail.jsonl")
        actual_args = mock.call_args[0][0]
        assert "--input" in actual_args
        assert ".roam/audit-trail.jsonl" in actual_args
    warns = result.get("summary", {}).get("alias_warnings", [])
    assert any("statement_path" in w and "input_path" in w for w in warns)


def test_audit_trail_export_accepts_legacy_envelope_path_alias():
    """Same cross-canonical seal for ``audit_trail_export``."""
    from roam.mcp_server import audit_trail_export

    wrapped = _ensure_aliased("roam_audit_trail_export", audit_trail_export)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {"command": "roam_audit_trail_export", "data": []}
        result = wrapped(envelope_path=".roam/audit-trail.jsonl")
        actual_args = mock.call_args[0][0]
        assert "--input" in actual_args
        assert ".roam/audit-trail.jsonl" in actual_args
    warns = result.get("summary", {}).get("alias_warnings", [])
    assert any("envelope_path" in w for w in warns)


def test_audit_trail_conformance_check_accepts_legacy_rules_path_alias():
    """``audit_trail_conformance_check`` is a third W332 input_path
    consumer. The cross-canonical reach test ensures any of the four
    legacy aliases works."""
    from roam.mcp_server import audit_trail_conformance_check

    wrapped = _ensure_aliased("roam_audit_trail_conformance_check", audit_trail_conformance_check)
    with patch("roam.mcp_server._run_roam") as mock:
        mock.return_value = {
            "command": "roam_audit_trail_conformance_check",
            "data": [],
        }
        result = wrapped(rules_path=".roam/audit-trail.jsonl")
        actual_args = mock.call_args[0][0]
        assert "--input" in actual_args
        assert ".roam/audit-trail.jsonl" in actual_args
    warns = result.get("summary", {}).get("alias_warnings", [])
    assert any("rules_path" in w for w in warns)
