"""W1087 — SARIF tag-coverage lint (family-anchored ``properties.tags[]``).

The W1062 arc landed ``_derive_finding_tags()`` in
``src/roam/output/sarif.py`` and wired it into 12 SARIF emitters across 5
families (security: taint / vulns / secrets / auth_gaps; hygiene: dead /
smells / clones / orphan_imports; performance: n1 / missing_index /
over_fetch; ownership: bus_factor; plus audit-trail-conformance which
sits outside the W1062-followup-4 12-count canonical list). Every result
on those emitters now carries a normalised family-anchored
``properties.tags[]`` array (e.g. ``["security", "taint", "cwe-89",
"owasp-a03"]``) that GitHub Code Scanning / Defender / Sonar / Snyk
dashboards filter on.

The W1062-followup-4 report recommended LINTING the rule descriptors
rather than wiring the long tail of remaining emitters individually,
because most of the remaining surfaces are compound aggregators
(``critique``, ``fan``, ``health``, ``impact``) or thin advisory
projections (``py_modern``, ``py_types``, ``stale_refs``,
``orphan_routes``, ``flag_dead``) where the tag plumb adds no dashboard
value — they would just stamp ``family=<empty>`` and dilute the filter.

This lint pins two invariants going forward:

1. **PIN.** Every emitter on the WIRED roster MUST call
   ``_derive_finding_tags(`` somewhere in its body. Removing the call
   silently drops dashboard-filter tags from every result the emitter
   produces — a regression GitHub-Code-Scanning consumers cannot see
   without a side-by-side run diff.
2. **ALLOWLIST drift guard.** Every ``*_to_sarif`` (or ``_*_to_sarif``)
   function in ``src/roam/output/sarif.py`` AND in cmd_*.py modules
   MUST appear in EITHER the ``_WIRED`` set (calls
   ``_derive_finding_tags``) OR the ``_TAG_COVERAGE_EXEMPT`` set
   (deliberate no-tag with a 1-line rationale). A NEW emitter that
   skips the helper AND skips the allowlist trips this lint, forcing a
   deliberate decision: add the helper call OR add an exemption entry
   with rationale.

The dashboard-filtering trio (W1060 + W1061 + W1062 + followups) is the
canonical OASIS-spec advisory-warning + tag + override plumb consumers
expect. Drift here means new emitters silently regress the
dashboard-filter axis. Tracked under W1060 / W1061 / W1062 /
W1062-followup / W1087.

Companion lint: ``tests/test_w365_tool_metadata_annotations_parity.py``
(the W365 MCP ``ToolAnnotations`` parity lint that this test's
allowlist + AST-scan shape mirrors).
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Source locations the lint scans.
# ---------------------------------------------------------------------------

_REPO_ROOT = repo_root()
_SARIF_PY = _REPO_ROOT / "src" / "roam" / "output" / "sarif.py"
# cmd_*.py modules that define their own SARIF projection rather than
# routing through src/roam/output/sarif.py. The W1062-followup-4 audit
# called these out explicitly — ``_vulns_to_sarif`` (cmd_vulns) and the
# audit-trail conformance projection live alongside the command code
# instead of in the central module.
_CMD_LOCAL_EMITTERS = (
    _REPO_ROOT / "src" / "roam" / "commands" / "cmd_vulns.py",
    _REPO_ROOT / "src" / "roam" / "commands" / "cmd_audit_trail_conformance.py",
    _REPO_ROOT / "src" / "roam" / "commands" / "cmd_supply_chain.py",
    _REPO_ROOT / "src" / "roam" / "commands" / "cmd_boundary.py",
    _REPO_ROOT / "src" / "roam" / "commands" / "cmd_check_rules.py",
)


# ---------------------------------------------------------------------------
# WIRED roster (W1062 + W1062-followup canonical 12 + audit-trail-conformance).
#
# These 13 emitters MUST call ``_derive_finding_tags(`` in their body.
# Removing the call from any one of them is a silent dashboard-filter
# regression for that family. The W1062-followup-4 canonical 12-count
# list excludes the audit-trail one (it landed as part of the original
# W1062 batch alongside taint + vulns); we lint all 13 because the
# invariant is symmetric — drift on either is equally bad.
# ---------------------------------------------------------------------------

_WIRED: frozenset[str] = frozenset(
    {
        # Security family (4)
        "taint_to_sarif",
        "_vulns_to_sarif",  # cmd_vulns.py
        "secrets_to_sarif",
        "auth_gaps_to_sarif",
        # Hygiene family (4)
        "dead_to_sarif",
        "smells_to_sarif",
        "clones_to_sarif",
        "orphan_imports_to_sarif",
        # Performance family (3)
        "n1_to_sarif",
        "missing_index_to_sarif",
        "over_fetch_to_sarif",
        # Ownership family (1)
        "bus_factor_to_sarif",
        # Compliance / audit-trail (1 — outside the W1062-followup-4
        # canonical 12-count, but on the original W1062 wiring batch)
        "_checks_to_sarif",  # cmd_audit_trail_conformance.py
    }
)


# ---------------------------------------------------------------------------
# ALLOWLIST — emitters that deliberately do NOT carry family tags.
#
# Each entry needs a 1-line rationale capturing WHY no tag adds value.
# The W1062-followup-4 N/A taxonomy:
#
# - **Compound aggregator.** The emitter re-emits findings from other
#   detectors that already carry their own tags; stamping a generic
#   family tag here would either duplicate the upstream tag OR replace
#   it with a less specific bucket. Examples: critique, fan, health,
#   impact, llm_smells, hotspots, dark_matter.
# - **Thin advisory.** The emitter is a single-rule advisory band
#   (note-severity, no security/perf/hygiene framing) where dashboard
#   filtering does not help triage. Examples: py_modern, py_types,
#   stale_refs, orphan_routes, flag_dead, fitness, complexity.
# - **Schema / config surface.** The emitter projects rule-config or
#   schema-version state rather than findings on subjects; the
#   family-tag vocabulary does not apply. Examples: rules, health
#   (also compound), laws.
# - **CLI-mode signal.** The emitter projects an invocation-scoped
#   transient signal (workspace state, partition output, etc.) rather
#   than a persistent finding. Examples: partition, delete_check,
#   affected_tests, test_impact, impact, duplicates, verify_imports,
#   algo, flag_dead.
# - **Standalone supply-chain / boundary projection.** cmd_*-local
#   emitters whose subject vocabulary (SBOM dependency, architectural
#   boundary) is orthogonal to the finding-family taxonomy.
#
# Adding a new emitter? Pick the right path:
#   (a) Family-tag-applicable -> call ``_derive_finding_tags(`` in body
#       + nothing to add here.
#   (b) Genuinely outside the family-tag vocabulary -> add the function
#       name here + 1-line rationale.
#
# The drift guard below asserts EVERY ``*_to_sarif`` function is in
# ``_WIRED`` OR ``_TAG_COVERAGE_EXEMPT`` — never neither.
# ---------------------------------------------------------------------------

_TAG_COVERAGE_EXEMPT: dict[str, str] = {
    # ── Thin advisory bands (note-severity, no triage filter) ────────
    "fitness_to_sarif": "thin advisory — single fitness-violation rule, no family band",
    "stale_refs_to_sarif": "thin advisory — broken-link kinds (md_inline/anchor/...), not a finding family",
    "complexity_to_sarif": "thin advisory — per-symbol cyclomatic band, advisory-only",
    "py_types_to_sarif": "thin advisory — single coverage-pct rule, note-severity only",
    "py_modern_to_sarif": "thin advisory — legacy-typing + dot-format hints, note-severity",
    "flag_dead_to_sarif": "thin advisory — feature-flag staleness (heuristic, name-pattern only)",
    "orphan_routes_to_sarif": "thin advisory — Laravel orphan-endpoint, single closed-enum rule",
    # ── Compound aggregators (re-emit other detectors' tagged rows) ──
    "health_to_sarif": "compound aggregator — composes complexity/dead/cycles/smells with own tags",
    "critique_to_sarif": "compound aggregator — clones-not-edited + blast-radius surface check",
    "fan_to_sarif": "compound aggregator — fan-in/fan-out hotspots from graph builder",
    "impact_to_sarif": "compound aggregator — blast-radius derived from graph builder",
    "llm_smells_to_sarif": "compound aggregator — re-emits ai-rot patterns from vibe-check",
    "hotspots_to_sarif": "compound aggregator — runtime-trace classification (UPGRADE/CONFIRMED/DOWNGRADE)",
    "dark_matter_to_sarif": "compound aggregator — hidden co-change coupling derived from git history",
    "duplicates_to_sarif": "compound aggregator — exact-string duplicates derived from FTS5 scan",
    # ── CLI-mode / invocation-scoped signal ──────────────────────────
    "algo_to_sarif": "invocation-scoped — algorithm-catalog detector findings, task_id-keyed",
    "partition_to_sarif": "invocation-scoped — multi-agent work partition output (Louvain bisection)",
    "delete_check_to_sarif": "invocation-scoped — diff-time surviving-reference gate, transient",
    "affected_tests_to_sarif": "invocation-scoped — diff -> tests-to-rerun projection",
    "test_impact_to_sarif": "invocation-scoped — file -> dependent tests projection",
    "verify_imports_to_sarif": "invocation-scoped — import-graph round-trip diagnostic",
    # ── Schema / config / compliance surfaces ────────────────────────
    "rules_to_sarif": "config-surface — projects rule-config matches, not finding families",
    "laws_to_sarif": "config-surface — mined invariants from src/roam/laws/, not detector findings",
    # ── cmd_*-local emitters orthogonal to the family vocabulary ─────
    "supply_chain_to_sarif": "cmd_supply_chain — SBOM dependency subject, orthogonal taxonomy",
    "_boundary_to_sarif": "cmd_boundary — architectural-boundary subject, orthogonal taxonomy",
    "_results_to_sarif": "cmd_check_rules — rule-match projection (rule-config state, not findings)",
}


# ---------------------------------------------------------------------------
# AST scan helpers.
# ---------------------------------------------------------------------------


def _collect_emitter_funcs(
    source_path: Path,
) -> dict[str, ast.FunctionDef]:
    """Return all ``*_to_sarif`` function defs in *source_path*, keyed by name.

    Accepts both public (``foo_to_sarif``) and private (``_foo_to_sarif``)
    names because cmd_*.py local projections (cmd_vulns, cmd_check_rules,
    cmd_audit_trail_conformance) use the private form.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    emitters: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and re.fullmatch(r"_?\w*_to_sarif", node.name):
            emitters[node.name] = node
    return emitters


def _calls_derive_finding_tags(func_node: ast.FunctionDef) -> bool:
    """True iff *func_node*'s body contains a call to ``_derive_finding_tags``.

    Walks the function subtree looking for any ``Call`` whose target is a
    ``Name`` or ``Attribute`` ending in ``_derive_finding_tags``. The
    ``Attribute`` branch covers the cmd_*.py callers that import the
    helper as ``from roam.output.sarif import _derive_finding_tags`` AND
    the (unlikely) ``sarif._derive_finding_tags(...)`` attribute form.
    """
    for sub in ast.walk(func_node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name) and func.id == "_derive_finding_tags":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "_derive_finding_tags":
            return True
    return False


def _all_emitters() -> dict[str, ast.FunctionDef]:
    """Union of every ``*_to_sarif`` def across sarif.py + cmd_*-local files."""
    out: dict[str, ast.FunctionDef] = {}
    out.update(_collect_emitter_funcs(_SARIF_PY))
    for path in _CMD_LOCAL_EMITTERS:
        if not path.exists():
            continue
        out.update(_collect_emitter_funcs(path))
    return out


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_wired_emitters_call_derive_finding_tags() -> None:
    """PIN: each emitter in ``_WIRED`` MUST call ``_derive_finding_tags``.

    Removing the call silently regresses the dashboard-filter axis for an
    entire family (e.g. drop ``_derive_finding_tags`` from
    ``taint_to_sarif`` and every CWE / OWASP / family tag disappears
    from the SARIF output without any test elsewhere noticing).
    """
    emitters = _all_emitters()
    missing: list[str] = []
    for name in sorted(_WIRED):
        if name not in emitters:
            missing.append(f"{name} (function not found in scanned files)")
            continue
        if not _calls_derive_finding_tags(emitters[name]):
            missing.append(f"{name} (no _derive_finding_tags call in body)")
    assert not missing, (
        f"{len(missing)} WIRED emitter(s) no longer call "
        f"_derive_finding_tags():\n  "
        + "\n  ".join(missing)
        + "\n\nW1062 + followups wired these emitters with family-anchored "
        "properties.tags[]. Dropping the helper call regresses GitHub Code "
        "Scanning / Sonar / Snyk dashboard filtering for that family. "
        "Re-wire the call OR (if intentionally removing tags) move the "
        "emitter from _WIRED to _TAG_COVERAGE_EXEMPT with a 1-line rationale."
    )


def test_every_emitter_is_wired_or_exempt() -> None:
    """ALLOWLIST drift: every ``*_to_sarif`` must be ``_WIRED`` OR exempt.

    A NEW SARIF emitter merged without either calling
    ``_derive_finding_tags`` or being added to ``_TAG_COVERAGE_EXEMPT``
    trips this lint. The fix is a deliberate decision: family-tag-applicable
    surface -> call the helper; orthogonal subject vocabulary -> add an
    exemption entry with rationale.
    """
    emitters = _all_emitters()
    untracked: list[str] = []
    for name in sorted(emitters):
        if name in _WIRED:
            continue
        if name in _TAG_COVERAGE_EXEMPT:
            continue
        untracked.append(name)
    assert not untracked, (
        f"{len(untracked)} SARIF emitter(s) are neither WIRED nor "
        f"EXEMPT:\n  " + "\n  ".join(untracked) + "\n\nEvery ``*_to_sarif`` function must be either (a) on the "
        "_WIRED roster with a ``_derive_finding_tags()`` call in the "
        "body, OR (b) in _TAG_COVERAGE_EXEMPT with a 1-line rationale "
        "explaining why family tags don't apply (compound aggregator / "
        "thin advisory / invocation-scoped signal / orthogonal subject "
        "vocabulary). See the module docstring in this test file for "
        "the W1062-followup-4 N/A taxonomy."
    )


def test_wired_and_exempt_sets_are_disjoint() -> None:
    """An emitter is either WIRED or EXEMPT — never both.

    Drift here means someone added the same name to both lists, which
    silently hides the contradiction: the EXEMPT entry says "no tags
    needed" while the WIRED PIN says "must have tags". Pick one.
    """
    overlap = _WIRED & set(_TAG_COVERAGE_EXEMPT)
    assert not overlap, (
        f"{len(overlap)} emitter(s) appear in BOTH _WIRED and "
        f"_TAG_COVERAGE_EXEMPT: {sorted(overlap)}.\n"
        "Pick one — either the emitter calls _derive_finding_tags (WIRED) "
        "OR it deliberately doesn't (EXEMPT). Both is a contradiction."
    )


def test_exempt_rationales_are_nonempty() -> None:
    """Every exemption needs a 1-line rationale (LAW 4 disclosure).

    Empty rationale strings are forbidden — the whole point of the
    allowlist is that the next reader sees WHY the emitter skips tags
    and can decide whether the rationale still holds.
    """
    bad: list[str] = []
    for name, rationale in _TAG_COVERAGE_EXEMPT.items():
        if not isinstance(rationale, str) or not rationale.strip():
            bad.append(name)
    assert not bad, (
        f"{len(bad)} exemption(s) have empty rationale:\n  {bad}\n"
        "Every _TAG_COVERAGE_EXEMPT entry must explain (in one line) "
        "why the emitter skips family-anchored tags. Closed taxonomy: "
        "compound aggregator / thin advisory / invocation-scoped / "
        "orthogonal subject vocabulary / config surface."
    )


def test_derive_finding_tags_helper_still_exists() -> None:
    """Sanity check — the W1062 helper itself is reachable + signature stable.

    If a future refactor renames or removes ``_derive_finding_tags``,
    the WIRED PIN would still pass (the AST-walk wouldn't find a call,
    so every WIRED emitter would be flagged). But this assertion gives
    a clearer error message in that scenario: "the helper is gone"
    beats "12 emitters look broken".
    """
    from roam.output import sarif as sarif_mod

    assert hasattr(sarif_mod, "_derive_finding_tags"), (
        "src/roam/output/sarif.py no longer exposes _derive_finding_tags. "
        "Either the W1062 helper was renamed (update this lint + every "
        "_WIRED caller) or removed (re-think the whole tag-coverage "
        "invariant — the dashboard-filter axis has regressed across "
        "every family)."
    )
    sig = inspect.signature(sarif_mod._derive_finding_tags)
    # Pin the keyword-only signature so a drive-by edit that drops a
    # parameter (e.g. removing ``family``) doesn't silently pass.
    expected_params = {"cwe", "owasp_top10", "severity", "family", "extra"}
    assert expected_params.issubset(sig.parameters.keys()), (
        f"_derive_finding_tags signature changed: expected at least {expected_params}, got {set(sig.parameters)}"
    )


def test_wired_count_pins_w1062_canonical_roster() -> None:
    """Pin the WIRED count at 13 (W1062-followup-4 canonical 12 + audit-trail).

    The W1062-followup-4 audit froze the wired roster at the 12 emitters
    where family-anchored tags add dashboard value (security/hygiene/
    performance/ownership). The audit-trail-conformance emitter sits
    outside the 12-count canonical list but landed as part of the
    original W1062 batch alongside taint + vulns. Growing this pin
    requires a deliberate decision: either a new family-tag-applicable
    emitter shipped (good — bump the count) OR the lint scope drifted.
    """
    assert len(_WIRED) == 13, (
        f"_WIRED roster size = {len(_WIRED)}; expected 13 (W1062-followup-4 "
        f"canonical 12 + audit-trail-conformance). If growing the roster "
        f"is intentional (a new family-tag-applicable emitter shipped + "
        f"calls _derive_finding_tags), bump this assertion deliberately."
    )
