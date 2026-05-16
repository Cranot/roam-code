"""Cross-surface documentation consistency check.

The same load-bearing numbers (package version, CLI command count, MCP
tool count) appear across many surfaces — pyproject.toml, server.json,
the MCP server card, the README, the docs-site landscape entry — and
they have a habit of drifting out of sync because a release bump only
touches some of them.

This test scrapes every public surface for those numbers and asserts
they all agree with the source-of-truth (``pyproject.toml`` and the
live ``cli._COMMANDS`` / ``mcp_server._REGISTERED_TOOLS`` counters).
When one of them drifts, all of them must be updated in the same PR.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# Source of truth
# ---------------------------------------------------------------------------


def _truth_version() -> str:
    """Read ``version`` from pyproject.toml — the canonical version."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "pyproject.toml missing version"
    return m.group(1)


def _truth_cli_command_count() -> int:
    """Live public-command count from ``cli._COMMANDS`` (counts aliases).

    Uses ``command_names`` (not ``canonical_commands``) because that's what
    a user sees when running ``roam --help`` and what the README headline
    advertises. Aliases like ``algo``/``math`` are real commands a user
    can invoke.
    """
    from roam.surface_counts import cli_surface_counts

    return int(cli_surface_counts()["command_names"])


def _truth_mcp_tool_count() -> int:
    """Live registered-tool count from ``mcp_server._REGISTERED_TOOLS``."""
    from roam.surface_counts import mcp_surface_counts

    return int(mcp_surface_counts()["registered_tools"])


# ---------------------------------------------------------------------------
# Per-surface scrapers
# ---------------------------------------------------------------------------


def _scrape_first_int_after(text: str, pattern: str) -> int | None:
    """Find the first integer in ``text`` matching the regex pattern."""
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (IndexError, ValueError):
        return None


def _readme_command_count() -> int | None:
    """README's headline ``N commands``."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return _scrape_first_int_after(text, r"\b(\d+)\s+commands\b")


def _readme_mcp_count() -> int | None:
    """README's headline ``N MCP tools``."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return _scrape_first_int_after(text, r"\b(\d+)\s+MCP\s+tools\b")


def _llms_install_command_count() -> int | None:
    p = ROOT / "llms-install.md"
    if not p.exists():
        return None
    return _scrape_first_int_after(p.read_text(encoding="utf-8"), r"\b(\d+)\s+commands\b")


def _server_json_version() -> str | None:
    p = ROOT / "server.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("version")


# GitHub Pages was disabled on 2026-05-08; the canonical public
# mcp-server-card.json moved to the Cloudflare-served landing-page tree.
# The bundled wheel copy lives under ``src/roam/`` for ``roam mcp --card``.
_PUBLIC_MCP_CARD = ROOT / "templates" / "distribution" / "landing-page" / ".well-known" / "mcp-server-card.json"


def _mcp_card_version() -> str | None:
    p = _PUBLIC_MCP_CARD
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("version")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVersionConsistency:
    """``version`` must agree across pyproject, server.json, mcp-server-card,
    and the landscape.json self-row."""

    def test_pyproject_is_truth(self):
        v = _truth_version()
        # Accept both 2-segment (12.12) and 3-segment (12.12.0) forms.
        # The project switched to 2-segment versions in v12.11; the
        # release commit explicitly noted "skipping the third version
        # component going forward". Tests guard the *consistency*
        # across pyproject/server.json/mcp-card, not the segment count.
        assert re.match(r"^\d+\.\d+(\.\d+)?$", v), f"Bad version format: {v!r}"

    def test_server_json_matches_pyproject(self):
        truth = _truth_version()
        actual = _server_json_version()
        assert actual is not None, "server.json missing"
        assert actual == truth, f"server.json {actual!r} != pyproject {truth!r}"

    def test_mcp_card_matches_pyproject(self):
        truth = _truth_version()
        actual = _mcp_card_version()
        assert actual is not None, "mcp-server-card.json missing"
        assert actual == truth, f"mcp-server-card.json {actual!r} != pyproject {truth!r}"

    def test_bundled_card_matches_public_card(self):
        """The wheel ships ``src/roam/mcp-server-card.json`` so
        ``roam mcp --card`` works post-install without a source
        checkout. The Cloudflare-served public copy is canonical for
        the hosted ``/.well-known`` URL. Both must match byte-for-byte
        so they don't drift across releases.
        """
        bundled = ROOT / "src" / "roam" / "mcp-server-card.json"
        canonical = _PUBLIC_MCP_CARD
        if not bundled.exists() or not canonical.exists():
            pytest.skip("card files not both present")
        a = bundled.read_text(encoding="utf-8")
        b = canonical.read_text(encoding="utf-8")
        assert a == b, (
            "src/roam/mcp-server-card.json drifted from "
            f"{canonical.relative_to(ROOT).as_posix()} — "
            "re-copy after editing either file."
        )

    def test_card_tool_count_matches_live_count(self):
        """The card's ``capabilities.tools.total`` must match the live
        MCP tool count from ``surface_counts``."""
        try:
            from roam.surface_counts import collect_surface_counts
        except ImportError:
            pytest.skip("surface_counts unavailable")
        live = collect_surface_counts()
        card = json.loads(_PUBLIC_MCP_CARD.read_text(encoding="utf-8"))
        live_total = live["mcp"]["registered_tools"]
        live_core = live["mcp"]["core_tools"]
        card_total = card["capabilities"]["tools"]["total"]
        card_core = card["capabilities"]["tools"]["presets"]["core"]
        card_full = card["capabilities"]["tools"]["presets"]["full"]
        assert card_total == live_total, (
            f"card capabilities.tools.total = {card_total} but live MCP tool count = {live_total}"
        )
        assert card_core == live_core, (
            f"card capabilities.tools.presets.core = {card_core} but live core preset count = {live_core}"
        )
        assert card_full == live_total, (
            f"card capabilities.tools.presets.full = {card_full} but live total = {live_total}"
        )

    # ``test_landscape_json_self_row_version_matches`` removed
    # 2026-05-08: ``docs/site/data/landscape.json`` was deleted when GH
    # Pages was disabled. The roam-code self-row data still lives in
    # ``src/roam/competitor_site_data.py`` and the gitignored internal
    # tracker; neither needs a public-version-stamp consistency check.


class TestCommandCountConsistency:
    """CLI command count must agree across README, llms-install.md,
    and the live ``cli._COMMANDS`` count. (``landscape.json`` consistency
    check removed when GH Pages was disabled — file no longer exists.)"""

    def test_truth_command_count_is_positive(self):
        n = _truth_cli_command_count()
        assert n >= 100, f"suspiciously low: {n}"

    def test_readme_matches_source(self):
        truth = _truth_cli_command_count()
        actual = _readme_command_count()
        assert actual is not None, "README missing 'N commands' phrase"
        assert actual == truth, f"README says '{actual} commands' but cli._COMMANDS has {truth}"

    def test_llms_install_matches_source(self):
        truth = _truth_cli_command_count()
        actual = _llms_install_command_count()
        if actual is None:
            pytest.skip("llms-install.md not present or no count")
        assert actual == truth, f"llms-install.md says '{actual} commands' but truth is {truth}"


class TestMcpToolCountConsistency:
    """MCP tool count must agree across README + the live count.
    (``landscape.json`` consistency check removed; see above.)"""

    def test_truth_mcp_count_is_positive(self):
        n = _truth_mcp_tool_count()
        assert n >= 50, f"suspiciously low: {n}"

    def test_readme_matches_source(self):
        truth = _truth_mcp_tool_count()
        actual = _readme_mcp_count()
        assert actual is not None, "README missing 'N MCP tools' phrase"
        assert actual == truth, f"README says '{actual} MCP tools' but live count is {truth}"


# ---------------------------------------------------------------------------
# Internal-docs link audit
# ---------------------------------------------------------------------------

# Historically README and CHANGELOG linked out to ``docs/site/*.html``
# and a few sibling files. After GitHub Pages was disabled on
# 2026-05-08 and ``docs/site/`` was deleted, the docs live entirely at
# https://roam-code.com/docs/. New markdown link references to
# ``docs/site/*`` are now leaks pointing at deleted paths — catch them.

_DOC_LINK_RE = re.compile(r"\(docs/site/([^)#?]+\.(?:html|md))\)")


def _scrape_doc_links(text: str) -> set[str]:
    """All ``docs/site/*.{html,md}`` markdown links referenced by ``text``."""
    return {f"docs/site/{m}" for m in _DOC_LINK_RE.findall(text)}


class TestInternalDocLinks:
    """No markdown link in README or CHANGELOG should reference the
    deleted ``docs/site/*`` tree. New references are leaks."""

    def test_readme_does_not_link_to_deleted_docs_site(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        links = _scrape_doc_links(text)
        assert not links, (
            "README links to deleted docs/site/* paths "
            "(GH Pages was disabled 2026-05-08; canonical docs live at "
            f"https://roam-code.com/docs/): {sorted(links)}"
        )

    def test_changelog_does_not_link_to_deleted_docs_site(self):
        cl = ROOT / "CHANGELOG.md"
        if not cl.exists():
            pytest.skip("CHANGELOG.md missing")
        text = cl.read_text(encoding="utf-8")
        links = _scrape_doc_links(text)
        # NEW link references must be zero; historical entries that just
        # mention paths in prose aren't matched by the link regex.
        assert not links, (
            "CHANGELOG has markdown links to deleted docs/site/* paths "
            "(GH Pages was disabled 2026-05-08; canonical docs live at "
            f"https://roam-code.com/docs/): {sorted(links)}"
        )


# ---------------------------------------------------------------------------
# W200/W??? positioning regression guards
# ---------------------------------------------------------------------------


def test_readme_headline_uses_codebase_intelligence_phrasing():
    """README headline must use the current local-codebase-intelligence frame,
    not the legacy evidence-only or structural-intelligence wording.

    The README opens with a centered <div> block, an H1, a tagline, an intro
    paragraph, then the count line. The headline window is the first ~20 lines
    where all of that lives — bounded so the test catches headline regressions
    without spuriously matching distant prose elsewhere in the file.
    """
    readme_lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    headline_block = "\n".join(readme_lines[:20]).lower()
    assert "local codebase intelligence" in headline_block, (
        "README headline must use 'local codebase intelligence' phrasing"
    )
    assert "structural intelligence" not in headline_block, (
        "Legacy 'structural intelligence' wording detected in README headline"
    )
    assert "evidence engine" not in headline_block, (
        "Evidence-only headline detected; frame Roam as local codebase intelligence"
    )


# W200: words that, in a compliance/audit context, would be overclaims.
# `guarantee` is NOT in this list because the README legitimately uses it
# for technical product behavior (zero-conflict orchestration partitioning,
# reproducibility of deterministic analysis) — neither of which is a
# regulatory claim. The forbidden compliance-overclaim vocabulary is
# `certif*` and `compliant`. Both are permitted only inside negation /
# disclaimer wording (e.g. "we do NOT certify", "this does not make you
# compliant").
_COMPLIANCE_OVERCLAIM_WORDS = ("certif", "compliant")


def test_no_compliance_overclaim_in_readme_or_landing_page():
    """W200 wording discipline: no certif*/compliant outside negation context,
    and no compliance-context 'guarantee' overclaim in the headline window.

    Scope:
    - `certif*` and `compliant` are forbidden file-wide unless they appear
      in a line that explicitly negates them (`not `, `no `, `never`).
    - `guarantee` is allowed for legitimate technical claims (e.g.
      "zero-conflict guarantees"); it must NOT appear in the headline
      window of either file, which is where compliance overclaims would
      be most damaging.
    """
    for path_str in ["README.md", "templates/distribution/landing-page/index.html"]:
        text = (ROOT / path_str).read_text(encoding="utf-8")
        for pattern_word in _COMPLIANCE_OVERCLAIM_WORDS:
            for match in re.finditer(rf"\b{pattern_word}\w*\b", text, re.IGNORECASE):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end_idx = text.find("\n", match.end())
                line_end = line_end_idx if line_end_idx != -1 else len(text)
                line = text[line_start:line_end]
                lowered = line.lower()
                if "not " not in lowered and "no " not in lowered and "never" not in lowered:
                    raise AssertionError(f"Compliance overclaim in {path_str}: {line.strip()!r}")
        # Headline-window check for `guarantee` (the place where a
        # compliance overclaim would be most prominent).
        head_lines = text.splitlines()[:20]
        head = "\n".join(head_lines).lower()
        assert "guarantee" not in head, f"'guarantee' must not appear in the headline window of {path_str}"


# ---------------------------------------------------------------------------
# W203: control-mapping.yaml wording-guard lint
# ---------------------------------------------------------------------------
#
# W184 introduced a `wording_guard` field on every control entry, one of
# "maps to" / "supports evidence for" / "audit-ready record". The intent
# was always for a follow-up CI lint (this one) to verify the rendered
# `export_text` actually USES its declared wording_guard, AND that no
# forbidden compliance-overclaim words leak in.
#
# Mirrors the W200 negation discipline above: `certif*` / `compliant` /
# `guarantee` are allowed only when an explicit `not`/`no`/`never`/...
# marker sits in the surrounding window.


# Allowed wording-guard values per templates/audit-report/control-mapping-README.md
# §"wording_guard rule" - closed enumeration, LAW 11.
_WORDING_GUARD_ALLOWED = ("maps to", "supports evidence for", "audit-ready record")

# W536: forbidden-word + negation-marker vocabulary now lives in the
# shared helper. The YAML lint uses the *full* canonical set
# (certif / compliant / guarantee) — the control-mapping file has no
# legitimate technical use of "guarantee" the way the README does
# ("zero-conflict guarantees" is a product claim, not a compliance one).
# The README-only lint above keeps its narrower local subset by design.
from tests._helpers.wording_lint import (
    FORBIDDEN_WORDS as _YAML_FORBIDDEN_OVERCLAIMS,
)
from tests._helpers.wording_lint import (
    NEGATION_MARKERS as _YAML_NEGATION_MARKERS,
)


def _run_wording_guard_lint(entries):
    """Apply the W203 wording-guard lint to a parsed list of control entries.

    Returns ``(drift, overclaim)`` - two lists of human-readable error
    strings. An empty pair means the lint passed. Factored out of the
    main test so the self-test can drive it with synthetic input.
    """
    drift = []
    overclaim = []

    for entry in entries:
        cid = entry.get("control_id", "<unknown>")
        wording_guard = entry.get("wording_guard")
        export_text = entry.get("export_text", "") or ""

        # Rule 1 - declared wording_guard must appear verbatim in export_text.
        if wording_guard and wording_guard.lower() not in export_text.lower():
            drift.append(f"{cid}: wording_guard {wording_guard!r} NOT in export_text {export_text!r}")

        # Rule 2 - forbidden overclaim words only in negation context.
        et_lower = export_text.lower()
        for word in _YAML_FORBIDDEN_OVERCLAIMS:
            start = 0
            while True:
                idx = et_lower.find(word, start)
                if idx == -1:
                    break
                window_start = max(0, idx - 30)
                window = et_lower[window_start : idx + len(word) + 10]
                if not any(neg in window for neg in _YAML_NEGATION_MARKERS):
                    overclaim.append(
                        f"{cid}: forbidden word {word!r} in export_text without negation context: ...{window}..."
                    )
                start = idx + len(word)

    return drift, overclaim


def test_control_mapping_yaml_wording_discipline():
    """W203 - Control-mapping wording-guard lint.

    For every entry in the wheel-bundled
    ``src/roam/templates/audit_report/control-mapping.yaml`` (W554):
      1. The declared ``wording_guard`` MUST appear in the entry's
         ``export_text`` (verbatim, case-insensitive).
      2. No forbidden compliance-overclaim word (``certif*``,
         ``compliant``, ``guarantee``) may appear in ``export_text``
         except inside a negation window.

    Per ``(internal memo)`` §"Suggested
    wording" - Roam markets evidence, not certification. Drift in
    either direction (silent overclaim OR a wording_guard that doesn't
    match the rendered text) is caught here.
    """
    # W554 — YAML lives inside the package so it ships in the wheel.
    yaml_path = ROOT / "src" / "roam" / "templates" / "audit_report" / "control-mapping.yaml"
    text = yaml_path.read_text(encoding="utf-8")

    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not installed in this env")

    parsed = yaml.safe_load(text)

    # The file may be a bare top-level list (v0-style) OR a mapping with a
    # top-level ``controls:`` key (v1 - the current schema). Handle both
    # so this lint stays correct across schema bumps.
    if isinstance(parsed, dict) and "controls" in parsed:
        entries = parsed["controls"]
    else:
        entries = parsed

    assert isinstance(entries, list) and entries, (
        f"control-mapping.yaml must yield a non-empty list of entries, got {type(entries).__name__}"
    )

    # Sanity: every entry must declare a wording_guard from the closed set.
    bad_guards = [
        f"{e.get('control_id', '<unknown>')}: {e.get('wording_guard')!r}"
        for e in entries
        if e.get("wording_guard") not in _WORDING_GUARD_ALLOWED
    ]
    assert not bad_guards, f"wording_guard must be one of {_WORDING_GUARD_ALLOWED}; offending entries: " + ", ".join(
        bad_guards
    )

    drift, overclaim = _run_wording_guard_lint(entries)

    assert not drift, f"wording_guard drift in {len(drift)} entries:\n  " + "\n  ".join(drift)
    assert not overclaim, f"compliance overclaim in {len(overclaim)} entries:\n  " + "\n  ".join(overclaim)


def test_wording_guard_lint_catches_violations():
    """W203 self-test - synthesise a control entry that violates both
    rules and confirm the shared lint helper detects each one.

    Guards against the lint silently degrading (e.g. a refactor that
    flips the negation logic, or strips out one of the forbidden words).
    """
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not installed in this env")

    bad_yaml = """
- control_id: BAD_DRIFT
  wording_guard: "maps to"
  export_text: "This certifies you SOC 2 compliant!"
- control_id: BAD_GUARANTEE
  wording_guard: "supports evidence for"
  export_text: "Roam guarantees audit success."
- control_id: GOOD_NEGATED
  wording_guard: "maps to"
  export_text: "Roam's run ledger maps to Article 12; it does not certify compliance."
"""
    entries = yaml.safe_load(bad_yaml)
    drift, overclaim = _run_wording_guard_lint(entries)

    drift_ids = {row.split(":", 1)[0] for row in drift}
    overclaim_ids = {row.split(":", 1)[0] for row in overclaim}

    # BAD_DRIFT: "maps to" missing from export_text -> drift; also has
    # raw "certifies" and "compliant" with no negation -> two overclaims.
    assert "BAD_DRIFT" in drift_ids, f"lint failed to flag BAD_DRIFT wording_guard drift; drift={drift!r}"
    assert "BAD_DRIFT" in overclaim_ids, f"lint failed to flag BAD_DRIFT overclaim words; overclaim={overclaim!r}"

    # BAD_GUARANTEE: "supports evidence for" missing -> drift; raw
    # "guarantees" with no negation -> overclaim.
    assert "BAD_GUARANTEE" in drift_ids, f"lint failed to flag BAD_GUARANTEE drift; drift={drift!r}"
    assert "BAD_GUARANTEE" in overclaim_ids, f"lint failed to flag BAD_GUARANTEE overclaim; overclaim={overclaim!r}"

    # GOOD_NEGATED: "maps to" present; "certify" and "compliance" both
    # sit inside a "does not" negation window -> neither rule fires.
    assert "GOOD_NEGATED" not in drift_ids, f"lint falsely flagged GOOD_NEGATED drift; drift={drift!r}"
    assert "GOOD_NEGATED" not in overclaim_ids, f"lint falsely flagged GOOD_NEGATED overclaim; overclaim={overclaim!r}"


# ---------------------------------------------------------------------------
# W502 / W503 / W504: control-mapping closed-enum lints
# ---------------------------------------------------------------------------
#
# W428 (added five crosswalk entries for NIST AI 600-1 + NIST SP 800-218A)
# surfaced that three structural fields on every control entry are still
# free strings - any future typo silently splits the population:
#
#   W502 - source_framework (per-entry framework slug)
#   W503 - pass_condition   (per-entry verdict rule)
#   W504 - surface[]        (list of product surfaces emitting the evidence)
#
# Each is a closed enumeration today - the YAML header documents
# `surface` explicitly (templates/audit-report/control-mapping.yaml
# lines 26-30) and `pass_condition` is the textbook three-state verdict.
# Mirror the W203 `_WORDING_GUARD_ALLOWED` lint shape for grep-ability.


def _load_control_mapping_entries():
    """Load and unwrap the control-mapping YAML.

    Returns the list of control entries (handles both the v0 bare-list
    layout and the current v1 ``controls:`` mapping). Skips the calling
    test when PyYAML is not installed.
    """
    # W554 — YAML lives inside the package so it ships in the wheel.
    yaml_path = ROOT / "src" / "roam" / "templates" / "audit_report" / "control-mapping.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not installed in this env")
    parsed = yaml.safe_load(text)
    if isinstance(parsed, dict) and "controls" in parsed:
        return parsed["controls"]
    return parsed


# W502 / W503 / W504 closed enums - W518 consolidated these three
# frozensets + the ``_framework_title()`` dict in
# ``src/roam/evidence/oscal.py`` into one canonical module so a future
# rename (like W506's ``iso_42001`` -> ``iso_iec_42001``) cannot drift
# between the YAML lint and the OSCAL emitter.
#
# Local aliases keep the original W502/W503/W504 lint identifiers
# stable (test names + grep-ability) while pointing to the single
# source of truth.
from roam.evidence.control_mapping_vocab import (
    FRAMEWORK_SLUGS as _SOURCE_FRAMEWORK_ALLOWED,
)
from roam.evidence.control_mapping_vocab import (
    FRAMEWORK_TITLES,
)
from roam.evidence.control_mapping_vocab import (
    PASS_CONDITIONS as _PASS_CONDITION_ALLOWED,
)
from roam.evidence.control_mapping_vocab import (
    SURFACES as _SURFACE_ALLOWED,
)


def test_control_mapping_source_framework_enum_closed():
    """W502 - every entry's ``source_framework`` is in the closed enum.

    A free-string field invites silent typo splits (e.g. ``iso_42001``
    vs ``iso_iec_42001`` would silently create two cohorts and break
    every "filter by framework" report downstream — W506 renamed to
    `iso_iec_42001` for spec alignment). Pin the enum here.
    """
    entries = _load_control_mapping_entries()
    assert isinstance(entries, list) and entries, "control-mapping.yaml must yield a non-empty list of entries"
    bad = []
    for entry in entries:
        cid = entry.get("control_id", "<unknown>")
        sf = entry.get("source_framework")
        if sf not in _SOURCE_FRAMEWORK_ALLOWED:
            bad.append(f"{cid}: source_framework={sf!r}")
    assert not bad, (
        f"source_framework values must be in {sorted(_SOURCE_FRAMEWORK_ALLOWED)}; "
        f"offending entries:\n  " + "\n  ".join(bad)
    )


def test_control_mapping_pass_condition_enum_closed():
    """W503 - every entry's ``pass_condition`` is in the closed enum.

    The three legal verdict rules (``all_required_present``,
    ``any_required_present``, ``conditional``) are how the audit
    renderer decides what to report; a typo here silently downgrades
    a hard "all" gate to an "always-fail-on-missing" string compare.
    """
    entries = _load_control_mapping_entries()
    assert isinstance(entries, list) and entries, "control-mapping.yaml must yield a non-empty list of entries"
    bad = []
    for entry in entries:
        cid = entry.get("control_id", "<unknown>")
        pc = entry.get("pass_condition")
        if pc not in _PASS_CONDITION_ALLOWED:
            bad.append(f"{cid}: pass_condition={pc!r}")
    assert not bad, (
        f"pass_condition values must be in {sorted(_PASS_CONDITION_ALLOWED)}; offending entries:\n  " + "\n  ".join(bad)
    )


def test_control_mapping_surface_enum_closed():
    """W504 - every item in every entry's ``surface[]`` is in the closed enum.

    The seven legal surfaces are documented inline in the YAML header
    (control-mapping.yaml lines 26-30). The crosswalk addition wave
    (W428) introduced new entries; any future typo (``pr_replay`` vs
    ``pr-replay``, ``security-reach`` vs ``security-reachability``) would
    silently split the surface population. Pin the enum here.
    """
    entries = _load_control_mapping_entries()
    assert isinstance(entries, list) and entries, "control-mapping.yaml must yield a non-empty list of entries"
    bad = []
    for entry in entries:
        cid = entry.get("control_id", "<unknown>")
        surfaces = entry.get("surface") or []
        assert isinstance(surfaces, list), f"{cid}: surface must be a list, got {type(surfaces).__name__}"
        for s in surfaces:
            if s not in _SURFACE_ALLOWED:
                bad.append(f"{cid}: surface item={s!r}")
    assert not bad, f"surface[] items must be in {sorted(_SURFACE_ALLOWED)}; offending entries:\n  " + "\n  ".join(bad)


# ---------------------------------------------------------------------------
# W518: framework-vocab consolidation drift guard
# ---------------------------------------------------------------------------


def test_framework_slugs_titles_in_sync():
    """W518 - the FRAMEWORK_SLUGS frozenset and the FRAMEWORK_TITLES
    dict in ``roam.evidence.control_mapping_vocab`` must enumerate
    the same set of keys.

    These two structures are the single source of truth for both the
    YAML closed-enum lint (W502 above) and the OSCAL emitter's
    ``_framework_title()`` display map. If a future contributor adds
    a slug to one without the other (the exact failure mode W506
    surfaced when ``iso_42001`` was renamed across two duplicate
    allowlists), this test fails loudly.
    """
    assert _SOURCE_FRAMEWORK_ALLOWED == frozenset(FRAMEWORK_TITLES.keys()), (
        "FRAMEWORK_SLUGS and FRAMEWORK_TITLES.keys() must enumerate "
        "the same slugs. "
        f"Only in FRAMEWORK_SLUGS: "
        f"{sorted(_SOURCE_FRAMEWORK_ALLOWED - frozenset(FRAMEWORK_TITLES.keys()))}; "
        f"Only in FRAMEWORK_TITLES: "
        f"{sorted(frozenset(FRAMEWORK_TITLES.keys()) - _SOURCE_FRAMEWORK_ALLOWED)}"
    )
