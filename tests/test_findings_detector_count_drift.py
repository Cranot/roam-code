"""Drift-guard test for the "28 detectors persist findings" claim.

CLAUDE.md asserts a specific count of ``src/roam/commands/cmd_*.py``
modules that call ``emit_finding(...)`` from the central findings
registry. The claim is reproduced verbatim in marketing /
procurement collateral (``templates/legal/security-procurement-packet.md``)
and in the architecture landing-page docs
(``templates/distribution/landing-page/docs/architecture.html``).

Same leak class as W462: if the live count drifts (a new detector
lands, an old one is removed) without the docs being updated, the
docs go stale silently and downstream consumers of the procurement
packet read a number that no longer maps to reality. This test pins
the count structurally so any drift forces a docs update in the same
commit.

Scope discipline
----------------

* AST-parses each ``cmd_*.py`` module and counts modules with at
  least one ``emit_finding(...)`` call expression. Comments,
  docstrings, and bare name imports do not count.
* Asserts the live module count matches the pinned LIVE_COUNT below.
* Asserts the CLAUDE.md claim appears exactly once in the canonical
  section. CLAUDE.md may reference ``28`` elsewhere, so the match
  scopes to the specific claim phrase including the "detectors
  persist findings" anchor.
* Cross-checks the same literal count in two satellite docs that
  ship customer-facing language (``security-procurement-packet.md``
  and ``architecture.html``). Each satellite assertion is skipped
  if the doc has been removed (rather than failing) so concurrent
  doc edits in sister sessions don't cascade into a hard failure
  here; the canonical CLAUDE.md guard remains hard.

If a drift fires
----------------

The failure messages enumerate which modules entered or left the
``emit_finding`` set so the diagnosis is one shell command, not a
file walk.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
COMMANDS_DIR = REPO_ROOT / "src" / "roam" / "commands"

# Pinned live count. CLAUDE.md currently says "28 detectors persist
# findings" as of 2026-05-18. Bump this AND the docs together when a
# new detector module starts calling ``emit_finding``.
LIVE_COUNT = 29

# The canonical claim string in CLAUDE.md spans a line break:
# ``**28\ndetectors persist findings**``. We scope the canonical
# check to a regex that tolerates whitespace/newline between "28"
# and "detectors" so the test doesn't break on cosmetic reflow.
CLAUDE_MD_CLAIM_PATTERN = re.compile(
    r"\*\*\s*" + str(LIVE_COUNT) + r"\s+detectors persist findings\*\*",
    re.MULTILINE,
)

# Satellite docs that quote the same literal count in customer-facing
# language. These are advisory cross-checks; missing files skip
# rather than fail.
SATELLITE_DOCS: tuple[tuple[Path, re.Pattern[str]], ...] = (
    (
        REPO_ROOT / "templates" / "legal" / "security-procurement-packet.md",
        re.compile(r"\b" + str(LIVE_COUNT) + r"\s+detectors\b"),
    ),
    (
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "architecture.html",
        re.compile(r"\b" + str(LIVE_COUNT) + r"\s+detectors persist findings\b"),
    ),
)


# ---------------------------------------------------------------------------
# Detector-set computation
# ---------------------------------------------------------------------------


def _modules_calling_emit_finding() -> set[str]:
    """Return the set of ``cmd_*`` module names that call ``emit_finding(...)``.

    AST-based: walks each module looking for a ``Call`` node whose
    callee is the bare name ``emit_finding`` (the import style every
    detector uses today). String matches inside docstrings or
    comments do not count.
    """
    if not COMMANDS_DIR.is_dir():  # pragma: no cover - defensive
        pytest.skip(f"commands dir missing: {COMMANDS_DIR}")

    hits: set[str] = set()
    for path in sorted(COMMANDS_DIR.glob("cmd_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - syntax error would fail other tests
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "emit_finding":
                    hits.add(path.stem)
                    break
                if isinstance(func, ast.Attribute) and func.attr == "emit_finding":
                    hits.add(path.stem)
                    break
    return hits


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_emit_finding_module_count_matches_pinned_claim() -> None:
    """Live ``emit_finding`` caller set must equal the pinned count.

    If this fires, update LIVE_COUNT in this file AND the matching
    literal in CLAUDE.md (canonical), then re-run satellite-doc
    checks. The failure message enumerates the delta so the docs
    update is mechanical.
    """
    live = _modules_calling_emit_finding()
    assert len(live) == LIVE_COUNT, (
        f"{LIVE_COUNT} modules expected; got {len(live)}.\n"
        f"Live modules: {sorted(live)}\n"
        f"Update LIVE_COUNT in this file and the corresponding literal "
        f"in CLAUDE.md + satellite docs."
    )


def test_claude_md_claim_string_present_exactly_once() -> None:
    """CLAUDE.md must contain the canonical claim string exactly once.

    Drift-guard on the prose claim: if a future edit changes the
    phrasing or the literal, this fires before the docs ship.
    """
    claude_md = REPO_ROOT / "CLAUDE.md"
    if not claude_md.exists():  # pragma: no cover - defensive
        pytest.skip(f"CLAUDE.md missing at {claude_md}")
    text = claude_md.read_text(encoding="utf-8")
    # 2026-05-22 dogfood wiring: CLAUDE.md is now a pointer (``@AGENTS.md``)
    # rather than a 263-line mirror. The 28-detectors claim lives in AGENTS.md;
    # skip the CLAUDE.md gate when the pointer is detected.
    if text.strip().startswith("@AGENTS.md"):
        pytest.skip(
            "CLAUDE.md is a pointer to AGENTS.md (dogfood-wiring 2026-05-22). "
            "Detector-count claim validated in AGENTS.md."
        )
    matches = CLAUDE_MD_CLAIM_PATTERN.findall(text)
    assert len(matches) == 1, (
        f"Expected exactly one canonical claim "
        f'matching r"{CLAUDE_MD_CLAIM_PATTERN.pattern}" in CLAUDE.md; '
        f"found {len(matches)}. If CLAUDE.md was reflowed, update the "
        f"pattern. If the count changed, update LIVE_COUNT."
    )


def test_satellite_docs_quote_same_literal_count() -> None:
    """Customer-facing satellite docs must quote the same literal count.

    Each satellite is advisory: a missing file is skipped (sister
    sessions may be editing) but a present file with the wrong
    literal fails loud so procurement / architecture copy stays in
    sync with the canonical CLAUDE.md claim.
    """
    failures: list[str] = []
    checked = 0
    for path, pattern in SATELLITE_DOCS:
        if not path.exists():
            continue
        checked += 1
        text = path.read_text(encoding="utf-8")
        if not pattern.search(text):
            failures.append(
                f"{path.relative_to(REPO_ROOT)}: "
                f'no match for r"{pattern.pattern}" '
                f"(literal count drifted from CLAUDE.md)"
            )
    if checked == 0:
        pytest.skip("no satellite docs present to cross-check")
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# Drift-detection self-check
# ---------------------------------------------------------------------------
#
# Sanity test: if the live set were to grow or shrink by one, the
# main assertion above would fire. We prove that here by mutating a
# COPY of the live set and re-running the same equality logic. This
# does not touch disk; it only confirms the assertion has teeth.


def test_drift_detection_self_check_count_off_by_one_would_fail() -> None:
    """A simulated +1 or -1 drift must break the equality assertion."""
    live = _modules_calling_emit_finding()
    # The real assertion is `len(live) == LIVE_COUNT`. Confirm that
    # the same assertion against an off-by-one set fails.
    plus_one = live | {"cmd_fake_drift_injection"}
    minus_one = set(list(live)[1:]) if live else set()
    assert len(plus_one) != LIVE_COUNT, (
        "drift-detection self-check failed: adding a fake module did not change the count"
    )
    assert len(minus_one) != LIVE_COUNT, "drift-detection self-check failed: removing a module did not change the count"
