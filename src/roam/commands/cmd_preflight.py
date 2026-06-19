"""Compound pre-change safety check.

Combines blast radius, affected tests, complexity, coupling, conventions,
and fitness violations into a single call -- reducing round-trips for AI
agents from 5-6 calls to 1.

Naming-conventions detection delegates to the canonical helper in
``roam.commands.conventions_helper`` so preflight, describe, understand,
minimap, and the standalone conventions command all agree on what
violates a convention.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because preflight findings are invocation-scoped verdicts
(CRITICAL/HIGH/MEDIUM/LOW) tied to a single target at invocation time --
not per-location violations. The gate is informational-only (preflight
does not block PRs; ``health`` is the gate-failing signal). Multi-file
location expansion would distort SARIF semantics ("target has HIGH risk"
is not a per-location rule violation). See action.yml line 401
_SUPPORTED_SARIF allowlist and W1149 audit memo.
"""

from __future__ import annotations

import re
import sqlite3

import click

from roam.capability import roam_capability
from roam.commands.changed_files import (
    get_changed_files,
    resolve_changed_to_db,
)
from roam.commands.cmd_affected_tests import (
    _gather_affected_tests,
    _looks_like_file,
    _resolve_file_symbols,
)
from roam.commands.cmd_conventions import classify_case
from roam.commands.cmd_fitness import _CHECKERS, _load_rules
from roam.commands.conventions_helper import compute_conventions
from roam.commands.next_steps import format_next_steps_text, suggest_next_steps
from roam.commands.resolve import ensure_index, find_symbol
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import (
    json_envelope,
    loc,
    resolution_disclosure,
    to_json,
)
from roam.output.metric_definitions import (
    BLAST_RADIUS_AFFECTED_FILES,
    BLAST_RADIUS_AFFECTED_SYMBOLS,
    COGNITIVE_COMPLEXITY_DEFINITION,
    PREFLIGHT_RISK_LEVEL_DEFINITION,
)
from roam.runs.helpers import auto_log

# ---------------------------------------------------------------------------
# Risk-level helpers
# ---------------------------------------------------------------------------
#
# W847 — every UPPER-case string in this section is INTERNAL VOCABULARY
# (agent-facing risk-tier display: CRITICAL/HIGH/MEDIUM/LOW/WARNING/OK),
# NOT envelope severity-slot vocabulary. The W762 drift-guard already
# scopes itself narrowly (dict-value under a literal "severity" key);
# helper returns, rank-table keys, and risk-comparison branches are out
# of scope by design and STAY UPPER-case. The W759 cleanup wave (when it
# lands) only touches the four envelope-slot sites pinned in
# tests/test_w762_severity_upper_drift.py::_PRE_W762_PENDING. Do NOT
# lowercase the helper-return / rank-table / verdict-comparison sites in
# this section — that would degrade the agent-facing display contract.


def _blast_severity(affected_syms: int, affected_files: int) -> str:
    if affected_syms >= 50 or affected_files >= 15:
        return "CRITICAL"
    if affected_syms >= 20 or affected_files >= 8:
        return "HIGH"
    if affected_syms >= 5 or affected_files >= 3:
        return "MEDIUM"
    return "LOW"


def _test_severity(direct: int, transitive: int, colocated: int) -> str:
    total = direct + transitive + colocated
    if total == 0:
        return "WARNING"
    return "OK"


def _complexity_severity(cc: float, nesting: int) -> str:
    if cc >= 25:
        return "CRITICAL"
    if cc >= 15 or nesting >= 5:
        return "HIGH"
    if cc >= 8 or nesting >= 4:
        return "MEDIUM"
    return "LOW"


def _coupling_severity(missing_count: int) -> str:
    if missing_count >= 5:
        return "HIGH"
    if missing_count >= 2:
        return "MEDIUM"
    if missing_count >= 1:
        return "LOW"
    return "OK"


def _convention_severity(violation_count: int) -> str:
    if violation_count >= 5:
        return "HIGH"
    if violation_count >= 1:
        return "WARNING"
    return "OK"


def _fitness_severity(failed_rules: int) -> str:
    if failed_rules >= 3:
        return "CRITICAL"
    if failed_rules >= 1:
        return "WARNING"
    return "OK"


# W1088 — rank-table keys are lowercase to align with the W547 / W762
# canonical-severity discipline. UPPER-cased aliases are preserved so
# the W847 INTERNAL-VOCAB call-sites (helper returns + ``_risk_driver``
# upper-cased compare) keep resolving without a forced rewrite. Both
# cases route through ``.lower()`` at the ``_overall_risk`` callsite so
# the W759 envelope-slot lowercase values (``"low"`` / ``"warning"``)
# no longer silently miss the lookup and resolve to 0 by default.
_SEVERITY_ORDER = {
    "critical": 4,
    "high": 3,
    "warning": 2,
    "medium": 2,
    "low": 1,
    "ok": 0,
    # UPPER aliases — kept for the W847 INTERNAL-VOCAB sites
    # (helper-classifier returns like ``return "HIGH"`` and
    # ``_risk_driver``'s ``sev.upper()`` precondition) so existing
    # callers stay byte-identical.
    "CRITICAL": 4,
    "HIGH": 3,
    "WARNING": 2,
    "MEDIUM": 2,
    "LOW": 1,
    "OK": 0,
}


def _overall_risk(*severities: str) -> str:
    """Compute overall risk from individual severity labels."""
    # W1088 — normalize case at the lookup site so envelope-slot
    # lowercase values (W759: ``"low"`` / ``"warning"``) and INTERNAL
    # VOCAB UPPER values (W847: ``"HIGH"`` / ``"CRITICAL"``) both
    # resolve to their intended rank. Pre-W1088 the lowercase forms
    # silently defaulted to 0 — Pattern-2 silent-fallback territory.
    max_val = max(_SEVERITY_ORDER.get(s.lower() if isinstance(s, str) else s, 0) for s in severities)
    if max_val >= 4:
        return "CRITICAL"
    if max_val >= 3:
        return "HIGH"
    if max_val >= 2:
        return "MEDIUM"
    return "LOW"


def _risk_driver(blast, tests, compl, coupl, convs, fitns) -> str:
    """Identify the row driving the overall risk verdict.

    Returns a one-line summary like ``complexity (cc=17, HIGH)`` so
    an agent reading the preflight output knows *why* the overall
    verdict is what it is — it doesn't have to scan all six rows
    looking for the worst severity.

    Tie-break by actionability: complexity > fitness > tests > coupling
    > blast > conventions. A complexity warning is more actionable than
    a convention warning even at equal severity.
    """
    rows = [
        ("complexity", compl, f"cc={compl['max_cognitive_complexity']:.0f}"),
        ("fitness", fitns, f"{fitns.get('rules_failed', 0)} rules currently fail"),
        ("tests", tests, f"{tests.get('direct', 0)} direct, {tests.get('transitive', 0)} transitive"),
        ("coupling", coupl, f"{coupl.get('coupled_files', 0)} coupled files"),
        (
            "blast radius",
            blast,
            f"{blast.get('affected_symbols', 0)} symbols in {blast.get('affected_files', 0)} files",
        ),
        ("conventions", convs, f"{convs.get('violation_count', 0)} violations"),
    ]
    # Find max severity
    worst: tuple[int, str, str] | None = None
    for label, row, detail in rows:
        sev = row.get("severity", "OK").upper()
        order = _SEVERITY_ORDER.get(sev, 0)
        if order <= 1:  # OK / LOW — not driving the verdict
            continue
        if worst is None or order > worst[0]:
            worst = (order, label, f"{label} ({detail}, {sev})")
    return worst[2] if worst else ""


def _severity_tag(sev: str) -> str:
    return f"[{sev}]"


# ---------------------------------------------------------------------------
# 1. Blast radius
# ---------------------------------------------------------------------------


def _check_blast_radius(conn, sym_ids, file_paths):
    """Compute blast radius: affected symbols and files via reverse edges."""
    try:
        import networkx as nx

        from roam.graph.builder import build_symbol_graph
    except ImportError:
        return {
            "affected_symbols": 0,
            "affected_files": 0,
            "affected_file_list": [],
            "severity": "low",
        }

    G = build_symbol_graph(conn)
    RG = G.reverse()

    all_affected_syms = set()
    all_affected_files = set()

    for sid in sym_ids:
        if sid in RG:
            deps = nx.descendants(RG, sid)
            all_affected_syms.update(deps)
            for d in deps:
                node = G.nodes.get(d, {})
                fp = node.get("file_path")
                if fp and fp not in file_paths:
                    all_affected_files.add(fp)

    severity = _blast_severity(len(all_affected_syms), len(all_affected_files))

    return {
        "affected_symbols": len(all_affected_syms),
        "affected_files": len(all_affected_files),
        "affected_file_list": sorted(all_affected_files)[:20],
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# 2. Affected tests
# ---------------------------------------------------------------------------


# Once the blast radius dumps every test file in the repo, the
# "Suggested tests:" line stops being a suggestion and becomes a wall
# of text. This cap is the boundary between actionable and unactionable.
_MAX_SUGGESTED_TEST_FILES = 15


def _check_affected_tests(conn, sym_ids, file_paths):
    """Find tests that need to run."""
    results = _gather_affected_tests(conn, sym_ids, file_paths)

    # W847 — DIRECT/TRANSITIVE/COLOCATED are upstream kind tags from
    # ``_gather_affected_tests`` (internal vocabulary), not envelope
    # severity slots. They flow into the count fields below, never into
    # a ``"severity"`` key — out of W762 scope by design.
    direct = sum(1 for r in results if r["kind"] == "DIRECT")
    transitive = sum(1 for r in results if r["kind"] == "TRANSITIVE")
    colocated = sum(1 for r in results if r["kind"] == "COLOCATED")

    # Unique test files
    seen = set()
    test_files = []
    for r in results:
        if r["file"] not in seen:
            seen.add(r["file"])
            test_files.append(r["file"])

    # Pick the actual test runner from package.json / pyproject when
    # possible — noted preflight suggesting `pytest tests/`
    # for Vitest projects.
    try:
        from roam.db.connection import find_project_root
        from roam.output.project_shape import _detect_test_runner

        runner_name, _runner_cmd = _detect_test_runner(find_project_root())
    except Exception:
        runner_name = None
    runner_token = "pytest"
    if runner_name == "vitest":
        runner_token = "npx vitest run"
    elif runner_name == "jest":
        runner_token = "npx jest"
    elif runner_name == "mocha":
        runner_token = "npx mocha"
    elif runner_name == "playwright":
        runner_token = "npx playwright test"
    elif runner_name == "go test":
        runner_token = "go test"
    elif runner_name == "cargo test":
        runner_token = "cargo test"
    elif runner_name == "rspec":
        runner_token = "bundle exec rspec"

    truncated_files = len(test_files) > _MAX_SUGGESTED_TEST_FILES
    if truncated_files:
        suggested = test_files[:_MAX_SUGGESTED_TEST_FILES]
        suffix = f"  # (+{len(test_files) - _MAX_SUGGESTED_TEST_FILES} more)"
        pytest_cmd = f"{runner_token} " + " ".join(suggested) + suffix
    else:
        pytest_cmd = f"{runner_token} " + " ".join(test_files) if test_files else ""
    severity = _test_severity(direct, transitive, colocated)

    return {
        "direct": direct,
        "transitive": transitive,
        "colocated": colocated,
        "total": len(results),
        "test_files": test_files,
        "pytest_command": pytest_cmd,
        "pytest_command_truncated": truncated_files,
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# 3. Complexity
# ---------------------------------------------------------------------------


def _check_complexity(conn, sym_ids):
    """Check complexity for target symbols."""
    if not sym_ids:
        return {
            "max_cognitive_complexity": 0,
            "max_nesting_depth": 0,
            "high_complexity_symbols": [],
            "severity": "low",
        }

    ph = ",".join("?" for _ in sym_ids)
    rows = conn.execute(
        f"""SELECT sm.cognitive_complexity, sm.nesting_depth,
                   sm.param_count, sm.line_count, sm.return_count,
                   sm.bool_op_count, sm.callback_depth,
                   s.name, s.kind, s.line_start, f.path as file_path
            FROM symbol_metrics sm
            JOIN symbols s ON sm.symbol_id = s.id
            JOIN files f ON s.file_id = f.id
            WHERE sm.symbol_id IN ({ph})
            ORDER BY sm.cognitive_complexity DESC""",
        list(sym_ids),
    ).fetchall()

    if not rows:
        return {
            "max_cognitive_complexity": 0,
            "max_nesting_depth": 0,
            "high_complexity_symbols": [],
            "severity": "low",
        }

    max_cc = max(r["cognitive_complexity"] for r in rows)
    max_nest = max(r["nesting_depth"] for r in rows)

    high = [
        {
            "name": r["name"],
            "kind": r["kind"],
            "file": r["file_path"],
            "line": r["line_start"],
            "cognitive_complexity": r["cognitive_complexity"],
            "nesting_depth": r["nesting_depth"],
        }
        for r in rows
        if r["cognitive_complexity"] >= 8
    ]

    severity = _complexity_severity(max_cc, max_nest)

    return {
        "max_cognitive_complexity": round(max_cc, 1),
        "max_nesting_depth": max_nest,
        "high_complexity_symbols": high[:10],
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# 4. Coupling (temporal co-change)
# ---------------------------------------------------------------------------


def _check_coupling(conn, file_ids, file_paths):
    """Find temporally-coupled files that should change together."""
    if not file_ids:
        return {
            "coupled_files": 0,
            "missing_partners": [],
            "severity": "OK",
        }

    change_set = set(file_ids)

    # Build lookups
    id_to_path = {}
    file_commits = {}
    for f in conn.execute("SELECT id, path FROM files").fetchall():
        id_to_path[f["id"]] = f["path"]
    for fs in conn.execute("SELECT file_id, commit_count FROM file_stats").fetchall():
        file_commits[fs["file_id"]] = fs["commit_count"] or 1

    missing = []
    min_strength = 0.3
    min_cochanges = 2

    for fid in file_ids:
        partners = conn.execute(
            """SELECT file_id_a, file_id_b, cochange_count
               FROM git_cochange
               WHERE file_id_a = ? OR file_id_b = ?""",
            (fid, fid),
        ).fetchall()

        for p in partners:
            partner_fid = p["file_id_b"] if p["file_id_a"] == fid else p["file_id_a"]
            cochanges = p["cochange_count"]
            if cochanges < min_cochanges:
                continue

            avg = (file_commits.get(fid, 1) + file_commits.get(partner_fid, 1)) / 2
            strength = cochanges / avg if avg > 0 else 0
            if strength < min_strength:
                continue

            if partner_fid not in change_set:
                partner_path = id_to_path.get(partner_fid, f"file_id={partner_fid}")
                source_path = id_to_path.get(fid, f"file_id={fid}")
                missing.append(
                    {
                        "path": partner_path,
                        "strength": round(strength, 2),
                        "cochanges": cochanges,
                        "partner_of": source_path,
                    }
                )

    # Deduplicate by path (keep highest strength)
    seen = {}
    for m in missing:
        if m["path"] not in seen or m["strength"] > seen[m["path"]]["strength"]:
            seen[m["path"]] = m
    missing = sorted(seen.values(), key=lambda x: -x["strength"])

    severity = _coupling_severity(len(missing))

    return {
        "coupled_files": len(missing),
        "missing_partners": missing[:10],
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# 5. Convention compliance
# ---------------------------------------------------------------------------


def _check_conventions(conn, sym_ids, min_majority_pct: float = 70.0):
    """Check if target symbols follow codebase naming conventions.

    Uses the canonical detector in ``roam.commands.conventions_helper``
    so this gate agrees with what ``roam describe`` / ``roam understand``
    / ``roam minimap`` / ``roam conventions`` say about the same codebase.

    Pattern 4 of the dogfood corpus called out that preflight produced
    "45 violations, many false positives" because it flagged any symbol
    whose case differed from the codebase-wide mode for its kind-group —
    even on kinds where no convention had a real majority (51/49 splits
    treated as "violations"). The fix: only flag a symbol when its
    *kind* has a >70% majority convention AND the symbol violates that
    convention. The threshold is the ``min_majority_pct`` argument.
    """
    if not sym_ids:
        return {
            "violations": [],
            "violation_count": 0,
            "severity": "OK",
            "majority_threshold_pct": min_majority_pct,
            "kinds_with_majority": 0,
        }

    # Canonical detector — single source of truth across all roam
    # commands. Applies the default exclusion list (.github, docs, etc.)
    # so a YAML constant in a workflow file doesn't get treated as a
    # naming violation.
    result = compute_conventions(conn, min_majority_pct=min_majority_pct)
    by_kind = result["by_kind"]

    # Build a {kind -> expected_style} map, but ONLY for kinds whose
    # majority crosses the threshold. Kinds without a strong majority
    # (e.g. 55% methods snake_case) don't contribute violations — there
    # is no real convention to violate.
    expected_by_kind: dict[str, str] = {kind: info["style"] for kind, info in by_kind.items() if info["has_majority"]}

    ph = ",".join("?" for _ in sym_ids)
    target_syms = conn.execute(
        f"""SELECT s.name, s.kind, s.line_start, f.path as file_path
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            WHERE s.id IN ({ph})""",
        list(sym_ids),
    ).fetchall()

    violations = []
    for sym in target_syms:
        kind = sym["kind"]
        expected = expected_by_kind.get(kind)
        if not expected:
            continue
        style = classify_case(sym["name"])
        if not style or style == expected:
            continue
        violations.append(
            {
                "name": sym["name"],
                "kind": kind,
                "actual_style": style,
                "expected_style": expected,
                "majority_pct": by_kind[kind]["pct"],
                "file": sym["file_path"],
                "line": sym["line_start"],
            }
        )

    severity = _convention_severity(len(violations))

    return {
        "violations": violations,
        "violation_count": len(violations),
        "severity": severity,
        "majority_threshold_pct": min_majority_pct,
        "kinds_with_majority": len(expected_by_kind),
    }


# ---------------------------------------------------------------------------
# 6. Fitness rule violations
# ---------------------------------------------------------------------------


def _check_fitness(conn, root, target_paths: set[str] | None = None):
    """Run fitness rules and split target failures from sibling failures.

    Round 4 #11: when ``target_paths`` is provided, every rule's
    violations are bucketed by whether they touch a target file. The
    return now distinguishes ``rules_failing_on_target`` (the question
    the user actually asked) from ``rules_failing_on_siblings`` (other
    code in the same files — context, not blame).
    """
    rules = _load_rules(root)
    if not rules:
        return {
            "rules_checked": 0,
            "rules_failed": 0,
            "rules_currently_failing": 0,
            "rules_failing_on_target": 0,
            "rules_failing_on_siblings": 0,
            "total_violations": 0,
            "failed_rules": [],
            "failed_rules_on_siblings": [],
            "rule_details": [],
            "severity": "OK",
        }

    target_set = {p.replace("\\", "/").lower() for p in (target_paths or set())}

    def _violation_touches_target(violation: dict) -> bool:
        if not target_set:
            return True
        src = (violation.get("source") or "").replace("\\", "/").lower()
        if not src:
            return False
        path = src.split(":", 1)[0]
        return path in target_set

    all_violations = []
    rule_results = []
    target_fail_count = 0
    sibling_fail_count = 0

    for rule in rules:
        rtype = rule.get("type", "")
        checker = _CHECKERS.get(rtype)
        if checker is None:
            continue

        try:
            violations = checker(rule, conn)
        except (ImportError, re.error, sqlite3.DatabaseError):
            violations = []

        on_target = [v for v in violations if _violation_touches_target(v)]
        on_siblings = [v for v in violations if v not in on_target]
        # W847 — PASS/FAIL is rule-status vocabulary (per-rule outcome),
        # not envelope-severity vocabulary. Stays UPPER to match the
        # ``status`` field convention used by fitness consumers (e.g.
        # ``rule_details[*].status`` read by guard / next_steps).
        status = "PASS" if not violations else "FAIL"
        rule_results.append(
            {
                "name": rule.get("name", "unnamed"),
                "type": rtype,
                "status": status,
                "violations": len(violations),
                "violations_on_target": len(on_target),
                "violations_on_siblings": len(on_siblings),
            }
        )
        if on_target:
            target_fail_count += 1
        elif on_siblings:
            sibling_fail_count += 1
        all_violations.extend(violations)

    failed = sum(1 for r in rule_results if r["status"] == "FAIL")
    failed_names = [r["name"] for r in rule_results if r["status"] == "FAIL"]
    # W-dogfood-K: target-only severity. When a target_paths scope is
    # provided (the normal preflight call shape), the user asked "is
    # editing THIS symbol risky?" — they did NOT ask "does the codebase
    # have any cycle anywhere?". Falling back to the global ``failed``
    # count when the target is clean is Pattern-2 silent-fallback
    # territory: it paints every probe with the same codebase-wide
    # severity, inflating MEDIUM symbols to CRITICAL and emitting an
    # identical fitness_violations list against every target.
    #
    # When target_paths is empty (no scope provided — gate-mode), keep
    # the legacy global rollup so the project-wide ``roam fitness``
    # verdict path stays byte-identical.
    if target_paths:
        severity = _fitness_severity(target_fail_count)
        # Names emitted as ``failed_rules`` describe what the TARGET
        # violates. Sibling-only failures are still preserved in
        # ``rules_failing_on_siblings`` + ``rule_details[*].violations_on_siblings``
        # for callers that want global codebase signal.
        failed_names_emit = [r["name"] for r in rule_results if r["status"] == "FAIL" and r["violations_on_target"] > 0]
    else:
        severity = _fitness_severity(target_fail_count or failed)
        failed_names_emit = failed_names

    # Sibling-only failures: rules that FAIL but touch ZERO target files.
    # ``failed_rules`` is target-attributed by design (W-dogfood-K) and is
    # legitimately empty when the target itself is clean — but the
    # sibling-failure text branch and JSON consumers still need the rule
    # NAMES that ``rules_failing_on_siblings`` is counting, so they agree
    # with ``rule_details``. Without this the preflight Fitness line printed
    # a hollow ``()`` and the envelope reported an empty list against a
    # non-zero count.
    failed_names_on_siblings = [
        r["name"]
        for r in rule_results
        if r["status"] == "FAIL" and r["violations_on_target"] == 0 and r["violations_on_siblings"] > 0
    ]

    return {
        "rules_checked": len(rule_results),
        "rules_failed": failed,
        "rules_currently_failing": failed,
        "rules_failing_on_target": target_fail_count,
        "rules_failing_on_siblings": sibling_fail_count,
        "total_violations": len(all_violations),
        "failed_rules": failed_names_emit,
        "failed_rules_on_siblings": failed_names_on_siblings,
        "rule_details": rule_results,
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def _resolve_targets(conn, target, staged, root):
    """Resolve CLI arguments into (sym_ids, file_paths, file_ids, label, resolution).

    Returns a tuple of:
    - sym_ids: set of symbol IDs
    - file_paths: set of file paths (str)
    - file_ids: list of file IDs (int)
    - label: human-readable label for the target
    - resolution: W1241 Pattern-2 variant-D state — one of
      ``{"symbol", "file", "file_substring", "fuzzy", "unresolved", "staged"}``.
      The ``"staged"`` value is preflight-specific (not in the canonical
      ``_RESOLUTION_KINDS`` enum) and signals that the caller should
      omit the W1241 disclosure block — preflight on staged changes
      isn't a single-target resolution. The other five pass through to
      ``resolution_disclosure()`` directly. Pattern-1 Variant D Wave B
      added the ``file_substring`` distinction so substring-LIKE
      fallback matches surface separately from exact-path resolutions
      instead of collapsing both into ``"file"`` (closes the audit
      MEDIUM-severity vocab-mismatch entry).
    """
    sym_ids = set()
    file_paths = set()
    file_ids = []
    label = target or "staged changes"

    if staged:
        changed = get_changed_files(root, staged=True)
        if not changed:
            return sym_ids, file_paths, file_ids, "staged (no changes)", "staged"
        file_map = resolve_changed_to_db(conn, changed)
        if not file_map:
            return sym_ids, file_paths, file_ids, "staged (not in index)", "staged"
        for path, fid in file_map.items():
            file_paths.add(path)
            file_ids.append(fid)
            syms = conn.execute("SELECT id FROM symbols WHERE file_id = ?", (fid,)).fetchall()
            sym_ids.update(s["id"] for s in syms)
        label = f"staged changes ({len(file_map)} files)"
        return sym_ids, file_paths, file_ids, label, "staged"

    if target:
        target_norm = target.replace("\\", "/")
        if _looks_like_file(target_norm):
            sids, fpaths, file_tier = _resolve_file_symbols(conn, target_norm)
            # Pattern-1 Variant D Wave B: gate the not-found branch on
            # tier=None rather than ``not sids`` so a file indexed with
            # zero symbols stays tier-disclosable (the resolved-but-empty
            # ``file`` shape is valid; collapsing it into ``unresolved``
            # would mis-attribute the resolution state).
            if file_tier is None:
                return sym_ids, file_paths, file_ids, f"{target} (not found)", "unresolved"
            sym_ids.update(sids)
            file_paths.update(fpaths)
            # Get file IDs for the resolved paths
            for fp in fpaths:
                row = conn.execute("SELECT id FROM files WHERE path = ?", (fp,)).fetchone()
                if row:
                    file_ids.append(row["id"])
            label = target_norm
            return sym_ids, file_paths, file_ids, label, file_tier
        sym = find_symbol(conn, target)
        if sym is None:
            return sym_ids, file_paths, file_ids, f"{target} (not found)", "unresolved"
        sym_ids.add(sym["id"])
        file_paths.add(sym["file_path"])
        # Get file ID
        row = conn.execute("SELECT id FROM files WHERE path = ?", (sym["file_path"],)).fetchone()
        if row:
            file_ids.append(row["id"])
        label = f"{sym['name']} ({loc(sym['file_path'], sym['line_start'])})"
        # W1243 / W1249 — Pattern-2 variant-D resolution disclosure.
        # ``find_symbol`` stamps ``_resolution_tier`` on the returned row
        # (``"symbol"`` for exact-name rungs, ``"fuzzy"`` for the LIKE
        # fallback); read it straight off the row instead of re-deriving by
        # string-comparing name / qualified_name against the input.
        resolution = sym.get("_resolution_tier", "symbol")
        return sym_ids, file_paths, file_ids, label, resolution

    # No target and not staged — caller's preflight() function already
    # guards this case with a SystemExit(1) before we're called, so this
    # branch is unreachable in practice. Default to "unresolved" so the
    # tuple shape stays stable for any future caller.
    return sym_ids, file_paths, file_ids, label, "unresolved"


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@roam_capability(
    category="review",
    summary="Run a pre-change safety checklist: blast, tests, complexity, coupling, conventions.",
    inputs=["target"],
    outputs=["checklist", "verdict"],
    examples=[
        "roam preflight handleSave",
        "roam preflight --staged",
        "roam preflight src/auth.py",
    ],
    tags=["review", "gate", "agent"],
    ai_safe=True,
    requires_index=True,
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=True,
    destructive=False,
    stale_sensitive=True,
)
@click.command("preflight")
@click.argument("target", required=False, default=None)
@click.option("--staged", is_flag=True, help="Check staged changes")
@click.pass_context
def preflight(ctx, target, staged):
    """Run a pre-change safety checklist for a symbol, file, or staged changes.

    Combines blast radius, affected tests, complexity, coupling, conventions,
    and fitness checks into a single report. Ideal for AI agents that want
    one-call risk assessment before making changes.

    Unlike ``guard`` (which provides a deep 0-100 risk score for a single
    symbol with layer analysis and move-sensitive edges), this command
    handles files, staged changes, and multiple symbols at once, combining
    6 signal dimensions into a single CRITICAL/HIGH/MEDIUM/LOW verdict.

    \b
    Examples:
      roam preflight handle_login
      roam preflight src/auth.py
      roam preflight --staged          # checks anything git-staged

    See also ``critique`` (post-change clones-not-edited check),
    ``impact`` (blast radius alone), and ``guard`` (deep 0-100 risk
    for a single symbol).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False

    if not target and not staged:
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "preflight",
                        summary={
                            "verdict": "no TARGET symbol/file or --staged provided",
                            "state": "usage_error",
                            "partial_success": True,
                        },
                        status="usage_error",
                        isError=True,
                        error_code="USAGE_ERROR",
                        error="no TARGET symbol/file or --staged provided",
                        hint="Pass a TARGET symbol/file or use --staged.",
                    )
                )
            )
        else:
            click.echo("Provide a TARGET symbol/file or use --staged.")
        raise SystemExit(1)

    ensure_index()
    root = find_project_root()

    # W607-R — substrate-CALL marker accumulator (eighteenth-in-batch
    # W607 consumer-layer arc). cmd_preflight composes 6 substrate
    # helpers (resolve_targets / blast / tests / complexity / coupling
    # / conventions / fitness) into the 5-signal pre-change safety
    # envelope. Each helper has its own internal try/except returning a
    # safe floor, but a helper itself can still raise BEFORE reaching
    # that floor (downstream SQL-shape refactor, networkx blowing up
    # during build_symbol_graph, YAML loader surfacing an unexpected
    # raise from .roam-fitness.yml). The outer call sites previously had
    # no guards, so the envelope crashed whole. W607-R wraps each
    # substrate boundary with ``_run_check(phase, fn, *args)`` so the
    # raise becomes a ``preflight_<phase>_failed:<exc_class>:<detail>``
    # marker via ``_w607r_warnings_out`` and the envelope still emits
    # the remaining sections cleanly.
    #
    # Marker family ``preflight_*`` — distinct from W607-Q's ``pr_risk_*``,
    # W607-P's ``audit_*``, W607-O's ``dashboard_*``, W607-N's
    # ``doctor_*``, W607-M's ``health_*``, W607-L's ``minimap_*``,
    # W607-K's ``describe_*``. The marker-prefix discipline test pins
    # this closed-enum distinction.
    #
    # Empty bucket → byte-identical envelope (no warnings_out key in
    # either summary or top-level, no W607-R-driven partial_success flip;
    # the W1243 resolution-disclosure path still flips partial_success on
    # its own axis).
    _w607r_warnings_out: list[str] = []

    def _run_check(phase: str, fn, *args, default=None, **kwargs):
        """Run one substrate helper with W607-R marker emission.

        On a clean call the result is returned as-is. On an uncaught
        exception (the helper itself raised before producing its own
        floor value), surface a ``preflight_<phase>_failed:<exc_class>:<detail>``
        marker via ``_w607r_warnings_out`` and return *default* — the
        envelope still emits cleanly with the remaining substrates.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607r_warnings_out.append(f"preflight_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-AW — ADDITIVE plumbing on top of the W607-R substrate-CALL
    # markers. W607-R already wrapped the seven helper boundaries
    # (resolve_targets / blast / tests / complexity / coupling /
    # conventions / fitness); W607-AW extends marker coverage to the
    # AGGREGATION-PHASE boundaries that W607-R left unguarded:
    #
    #   - ``overall_risk``       — ``_overall_risk(...)`` rollup compute
    #   - ``risk_driver``        — ``_risk_driver(...)`` row-picker
    #   - ``fitness_violations`` — flat list build for summary contract
    #   - ``auto_log``           — active-run ledger write (silent no-op
    #                              if no run is active, but the underlying
    #                              ``auto_log`` can still raise on HMAC
    #                              chain misshape or filesystem failures)
    #
    # cmd_preflight is the AGENT-OS PRE-EDIT SAFETY GATE per CLAUDE.md
    # LAW 1: agents are instructed to run ``roam preflight <symbol>``
    # BEFORE every code change. A silent failure or partial-success in
    # preflight is the highest-blast-radius bug class in the entire roam
    # surface. The 5-signal degradation discipline (blast / complexity /
    # conventions / coupling / fitness) is preserved by W607-R; W607-AW
    # adds the same plumbing to the *post-compute* boundaries so the
    # envelope still surfaces a marker even when the aggregation phase
    # itself raises.
    #
    # Marker family ``preflight_*`` — same family as W607-R (additive,
    # not a separate prefix). Empty bucket → byte-identical envelope.
    _w607aw_warnings_out: list[str] = []

    def _run_check_aw(phase: str, fn, *args, default=None, **kwargs):
        """Run one aggregation-phase boundary with W607-AW marker emission.

        Mirror of ``_run_check`` shape (same ``preflight_<phase>_failed:``
        marker family) but writes into ``_w607aw_warnings_out`` so the
        additive bucket stays distinguishable in tests + audits.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607aw_warnings_out.append(f"preflight_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    # W607-EC: post-capture substrate-CALL plumbing LAYERED on top of
    # W607-R (substrate-CALL helper boundaries) and W607-AW
    # (aggregation-phase boundaries). cmd_preflight is the FLAGSHIP
    # gate command — its 5-signal envelope (blast / complexity /
    # conventions / coupling / fitness) is the canonical "dominant
    # variable" per CLAUDE.md LAW 1 for agent-decision speed. A raise
    # inside the POST-capture path (verdict-cascade f-strings, summary
    # dict assembly, envelope serialization, text formatting) would
    # silently torpedo the gate envelope WITHOUT lineage.
    #
    # W607-EC splits the post-capture boundary into 5 wrapped substrate
    # calls — DISJOINT phase-name sub-vocabulary from W607-R and
    # W607-AW so the shared ``preflight_*`` marker family carries no
    # collision:
    #
    #   compute_scores       — pre-verdict score-derivation + label normalization
    #   compose_verdict      — LAW 1+6 single-line verdict floor (THE
    #                          canonical agent-decision driver)
    #   assemble_sections    — summary_dict + envelope_kwargs build
    #                          (5-signal envelope shape)
    #   serialize_envelope   — to_json(json_envelope("preflight", ...)) projection
    #   format_text          — text-mode click.echo formatting
    #
    # Markers merge into BOTH ``summary.warnings_out`` and the
    # top-level ``warnings_out`` at output time. Empty bucket →
    # byte-identical 5-signal envelope. Helper template returns
    # ``default`` VERBATIM on raise (NOT
    # ``default if default is not None else {}``) so ``rendered is None``
    # guards work on the serialize_envelope degraded path.
    _w607ec_warnings_out: list[str] = []

    def _run_check_ec(phase: str, fn, *args, default=None, **kwargs):
        """Run one W607-EC post-capture substrate with marker emission.

        Clean call returns the result as-is. On an uncaught raise,
        surface ``preflight_<phase>_failed:<exc_class>:<detail>`` via
        ``_w607ec_warnings_out`` and substitute *default* — the envelope
        still emits the remaining substrates cleanly.

        ``default`` is returned VERBATIM on raise (including ``None``)
        so callers can distinguish a degraded-but-empty result (``{}``)
        from a degraded-no-output result (``None``). Critical for the
        ``serialize_envelope`` phase whose ``rendered is None`` guard
        precedes the minimal-fallback echo (W978 #6).
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — top-level disclosure
            _w607ec_warnings_out.append(f"preflight_{phase}_failed:{type(exc).__name__}:{exc}")
            return default

    with open_db(readonly=True) as conn:
        # Resolve targets
        _resolve_default = (set(), set(), [], target or "staged changes", "unresolved")
        sym_ids, file_paths, file_ids, label, resolution = _run_check(
            "resolve_targets",
            _resolve_targets,
            conn,
            target,
            staged,
            root,
            default=_resolve_default,
        )

        if not sym_ids:
            # Strip the "(not found)" suffix added by the symbol resolver
            # so the verdict / next-step hint show the bare query text.
            display_label = label.removesuffix(" (not found)")
            verdict = f"target not found — `{display_label}` is not in the index"
            # W1243 — Pattern-2 variant-D disclosure on the unresolved
            # path. ``_resolve_targets`` stamps ``"unresolved"`` for the
            # symbol-not-found and file-not-found branches, and
            # ``"staged"`` for the staged-but-empty branches; only emit
            # the canonical W1241 disclosure for the four enum kinds.
            disclosure = (
                resolution_disclosure("unresolved", target=display_label) if resolution == "unresolved" else None
            )
            not_found_summary = {
                "verdict": verdict,
                "target": display_label,
                # W847 — ``risk_level`` is preflight's canonical rollup
                # field (CRITICAL/HIGH/MEDIUM/LOW/UNKNOWN, defined by
                # PREFLIGHT_RISK_LEVEL_DEFINITION), distinct from the
                # W762-scoped ``severity`` envelope slot. Agent-facing
                # risk-tier vocabulary — STAYS UPPER.
                "risk_level": "UNKNOWN",
                "partial_success": True,
                "error": "No symbols found",
            }
            if disclosure is not None:
                not_found_summary["resolution"] = disclosure["resolution"]
            not_found_envelope_kwargs: dict = {"summary": not_found_summary}
            if disclosure is not None:
                not_found_envelope_kwargs["resolution"] = disclosure["resolution"]
                not_found_envelope_kwargs["partial_success"] = disclosure["partial_success"]
            # W607-R + W607-AW — surface substrate-CALL markers on the
            # not-found path. This branch is reached when
            # ``_resolve_targets`` returns an empty sym_ids set; if it
            # raised before that (W607-R wrapper floored to the
            # unresolved default), the marker lives in
            # ``_w607r_warnings_out`` and must reach the envelope.
            # W607-AW bucket is included for parity (empty here since
            # aggregation-phase boundaries fire AFTER the not-found
            # branch returns, but the field shape stays uniform across
            # paths). partial_success already True on this branch.
            # W607-R + W607-AW + W607-EC — surface substrate-CALL markers
            # on the not-found path. W607-EC bucket is included for
            # parity (empty here since post-capture phases haven't run
            # yet, but the field shape stays uniform across paths).
            _combined_warnings_out_nf = (
                list(_w607r_warnings_out) + list(_w607aw_warnings_out) + list(_w607ec_warnings_out)
            )
            if _combined_warnings_out_nf:
                not_found_summary["warnings_out"] = list(_combined_warnings_out_nf)
                not_found_envelope_kwargs["warnings_out"] = list(_combined_warnings_out_nf)
            not_found_envelope = json_envelope(
                "preflight",
                **not_found_envelope_kwargs,
            )
            auto_log(not_found_envelope, action="preflight", target=display_label, repo_root=root)
            if json_mode:
                click.echo(to_json(not_found_envelope))
            else:
                click.echo(f"VERDICT: {verdict}")
                click.echo()
                click.echo(f"  Try `roam search {display_label}` to find similar names,")
                click.echo("  or `roam index --force` if the symbol was just added.")
            return

        # Run all checks. W607-R — wrap each substrate boundary so an
        # unexpected raise surfaces as a marker via warnings_out rather
        # than crashing the whole envelope. Defaults mirror each
        # helper's own internal floor shape so downstream consumers
        # (severity helpers, _risk_driver, _overall_risk) keep reading
        # the same dict keys.
        blast = _run_check(
            "blast_radius",
            _check_blast_radius,
            conn,
            sym_ids,
            file_paths,
            default={
                "affected_symbols": 0,
                "affected_files": 0,
                "affected_file_list": [],
                "severity": "low",
            },
        )
        tests = _run_check(
            "affected_tests",
            _check_affected_tests,
            conn,
            sym_ids,
            file_paths,
            default={
                "direct": 0,
                "transitive": 0,
                "colocated": 0,
                "total": 0,
                "test_files": [],
                "pytest_command": "",
                "pytest_command_truncated": False,
                # W759: envelope-slot ``severity`` is the W547 canonical
                # lowercase vocabulary. The helper ``_test_severity``
                # returns UPPER (``"WARNING"`` / ``"OK"``) under the
                # W847 internal-vocabulary carve-out, but ``_run_check``
                # defaults that surface to the SAME envelope slot must
                # use lowercase so the W762 drift-guard stays green.
                # ``_overall_risk`` does ``.lower()`` at lookup time
                # (W1088) so ``"warning"`` resolves to the same rank as
                # ``"WARNING"`` (both map to 2 in ``_SEVERITY_ORDER``).
                "severity": "warning",
            },
        )
        compl = _run_check(
            "complexity",
            _check_complexity,
            conn,
            sym_ids,
            default={
                "max_cognitive_complexity": 0,
                "max_nesting_depth": 0,
                "high_complexity_symbols": [],
                "severity": "low",
            },
        )
        coupl = _run_check(
            "coupling",
            _check_coupling,
            conn,
            file_ids,
            file_paths,
            default={
                "coupled_files": 0,
                "missing_partners": [],
                "severity": "OK",
            },
        )
        convs = _run_check(
            "conventions",
            _check_conventions,
            conn,
            sym_ids,
            default={
                "violations": [],
                "violation_count": 0,
                "severity": "OK",
                "majority_threshold_pct": 70.0,
                "kinds_with_majority": 0,
            },
        )
        fitns = _run_check(
            "fitness",
            _check_fitness,
            conn,
            root,
            target_paths=set(file_paths),
            default={
                "rules_checked": 0,
                "rules_failed": 0,
                "rules_currently_failing": 0,
                "rules_failing_on_target": 0,
                "rules_failing_on_siblings": 0,
                "total_violations": 0,
                "failed_rules": [],
                "failed_rules_on_siblings": [],
                "rule_details": [],
                "severity": "OK",
            },
        )

        # Overall risk
        # W607-AW — wrap the rollup compute so an unexpected raise in
        # ``_overall_risk`` (e.g. a future severity dict-lookup refactor)
        # surfaces as ``preflight_overall_risk_failed:...`` and the
        # envelope still emits with a safe "UNKNOWN" floor.
        risk = _run_check_aw(
            "overall_risk",
            _overall_risk,
            blast["severity"],
            tests["severity"],
            compl["severity"],
            coupl["severity"],
            convs["severity"],
            fitns["severity"],
            default="UNKNOWN",
        )

        # W607-EC ``compute_scores`` substrate: derive the verdict-input
        # tuple (risk-tier kind + interpolation args) from per-signal
        # capture results. A raise inside arithmetic / dict-lookups on a
        # degraded capture-result surfaces as
        # ``preflight_compute_scores_failed:...`` and the floor scores
        # produce the verdict floor downstream.
        def _compute_scores():
            return {
                "risk": risk,
                "blast_affected_symbols": blast["affected_symbols"],
                "label": label,
            }

        _scores = _run_check_ec(
            "compute_scores",
            _compute_scores,
            default={"risk": "UNKNOWN", "blast_affected_symbols": 0, "label": label},
        )

        # W607-EC ``compose_verdict`` substrate: LAW 1 + LAW 6 single-line
        # verdict floor — preflight's verdict is THE canonical
        # agent-decision driver. A raise inside the f-string composition
        # returns the literal floor verdict instead of crashing.
        # W847 — LOW/MEDIUM/HIGH branches compare against the canonical
        # ``risk_level`` rollup (agent-facing risk-tier display, NOT a
        # W762-scoped envelope severity slot). The interpolated ``{risk}``
        # also reads as UPPER in the human-facing verdict text on
        # purpose. Out of W759 scope — STAYS UPPER.
        def _compose_verdict():
            _risk = _scores.get("risk", "UNKNOWN") if isinstance(_scores, dict) else "UNKNOWN"
            _bs = _scores.get("blast_affected_symbols", 0) if isinstance(_scores, dict) else 0
            _label = _scores.get("label", label) if isinstance(_scores, dict) else label
            if _risk == "LOW":
                _v = f"Safe to proceed — {_risk} risk for {_label}"
            elif _risk == "MEDIUM":
                _v = f"Proceed with caution — {_risk} risk for {_label}"
            elif _risk == "HIGH":
                _v = f"Review carefully — {_risk} risk, {_bs} symbols affected"
            else:
                _v = f"Significant risk — {_risk}, {_bs} symbols in blast radius"

            # W1243 — Pattern-2 variant-D suffix on the verdict when the
            # resolver succeeded through a degraded tier. The underlying
            # check is still valid (the symbol set we built is real), but
            # the success verdict must reflect that the input string did
            # not exact-match a single symbol — agents need to know to
            # re-issue with a qualified name. Pattern-1 Variant D Wave B
            # adds the ``file_substring`` distinction so a substring
            # LIKE-fallback match surfaces separately from an exact-path
            # ``file`` resolution.
            if resolution == "fuzzy":
                _v = f"{_v} [fuzzy resolution]"
            elif resolution == "file_substring":
                _v = f"{_v} [file substring match]"
            elif resolution == "file":
                _v = f"{_v} [file fallback]"
            return _v

        # W978 #1: verdict floor is a non-empty literal string so a
        # degraded compose_verdict still satisfies LAW 6.
        verdict = _run_check_ec(
            "compose_verdict",
            _compose_verdict,
            default="preflight gate degraded",
        )

        # Build the W1241 disclosure block. ``staged`` resolutions are
        # multi-target and don't map onto the canonical enum — omit the
        # disclosure rather than lie about which tier fired. Pattern-1
        # Variant D Wave B added ``file_substring`` for the LIKE-fallback
        # substring-match path (distinct from exact-path ``file``).
        if resolution in {"symbol", "file", "file_substring", "fuzzy"}:
            target_for_disclosure = label if target else (target or "")
            disclosure = resolution_disclosure(
                resolution,  # type: ignore[arg-type]
                target=target_for_disclosure,
            )
        else:
            disclosure = None

        # Build a flat list of fitness violations for the summary so
        # downstream contracts (e.g. ``roam_validate_plan``'s
        # FITNESS_VIOLATIONS warning, which reads
        # ``summary['fitness_violations']`` as a *list*) can fire
        # without having to dig into ``r['fitness']['rule_details']``.
        # Additive: we keep ``r['fitness']['rule_details']`` and
        # ``r['fitness']['failed_rules']`` untouched for existing
        # consumers.
        target_label_for_fitness = label.split(" (", 1)[0] if isinstance(label, str) else ""

        # W-dogfood-K: surface ONLY rules the target actually violates.
        # When ``violations_on_target == 0`` the rule is failing on
        # sibling files (other code in the same files OR elsewhere in
        # the codebase) — the target itself is clean. Listing sibling-
        # only failures as if the target had broken them is the
        # Pattern-2 silent-fallback shape: every preflight against a
        # clean symbol emits the same global-codebase failures, training
        # agents to ignore the field.
        # Sibling-only failures remain visible in
        # ``r['fitness']['rule_details'][*].violations_on_siblings``
        # and ``r['fitness']['rules_failing_on_siblings']`` for callers
        # that explicitly want global codebase signal.
        # W607-AW — wrap the flat-list build so a malformed
        # ``rule_details`` row (e.g. a downstream contract change that
        # makes ``detail`` a non-dict, or a future ``rule_details``
        # producer that surfaces a None where a list was expected)
        # surfaces as ``preflight_fitness_violations_failed:...`` and
        # the envelope still emits a safe empty list instead of
        # crashing the whole command.
        def _build_fitness_violations_list():
            return [
                {
                    "symbol": target_label_for_fitness,
                    "rule": detail.get("name", "unnamed"),
                    "severity": fitns.get("severity", "warning"),
                }
                for detail in fitns.get("rule_details") or []
                if detail.get("status") == "FAIL" and detail.get("violations_on_target", 0) > 0
            ]

        fitness_violations_list = _run_check_aw(
            "fitness_violations",
            _build_fitness_violations_list,
            default=[],
        )

        # W607-EC ``assemble_sections`` substrate: build the
        # ``summary_dict`` + ``envelope_kwargs`` for the 5-signal
        # envelope in one wrapped call. A raise inside the dict
        # construction (e.g. ``.get`` on a degraded sub-envelope, an
        # f-string on a non-stringifiable value) surfaces the canonical
        # marker; the floor produces a minimal but structurally valid
        # ``{summary_dict, envelope_kwargs}`` pair so the serialize path
        # still composes.
        def _assemble_sections():
            # Build the envelope once — used for JSON output and auto-log.
            # W1243 — Pattern-2 variant-D disclosure: when ``resolution`` is
            # a degraded tier (file / fuzzy), the verdict already carries
            # the suffix; the structured ``resolution`` + ``partial_success``
            # fields go on both ``summary`` (so agents reading only the
            # summary see them) and at the envelope top level (so the
            # canonical envelope shape lives alongside risk_level).
            _summary_dict: dict = {
                "verdict": verdict,
                "target": label,
                # W847 — ``risk_level`` is preflight's canonical rollup field
                # (not the W762-scoped ``severity`` slot). UPPER values flow
                # from ``_overall_risk``'s agent-facing risk-tier vocabulary.
                "risk_level": risk,
                "symbols_checked": len(sym_ids),
                "files_checked": len(file_paths),
                "fitness_violations": fitness_violations_list,
                # W331: preflight aggregates 6 dimensions into one
                # CRITICAL/HIGH/MEDIUM/LOW verdict. Name the rollup so
                # agents don't conflate it with a per-dimension severity.
                "risk_level_definition": PREFLIGHT_RISK_LEVEL_DEFINITION,
            }
            _envelope_kwargs: dict = {
                "summary": _summary_dict,
                "blast_radius": {
                    "affected_symbols": blast["affected_symbols"],
                    "affected_files": blast["affected_files"],
                    "affected_file_list": blast["affected_file_list"],
                    "severity": blast["severity"],
                    # W331: same definition as cmd_impact so two commands
                    # don't disagree on what "affected_symbols" means.
                    "affected_symbols_definition": BLAST_RADIUS_AFFECTED_SYMBOLS,
                    "affected_files_definition": BLAST_RADIUS_AFFECTED_FILES,
                },
                "tests": {
                    "direct": tests["direct"],
                    "transitive": tests["transitive"],
                    "colocated": tests["colocated"],
                    "total": tests["total"],
                    "test_files": tests["test_files"],
                    "pytest_command": tests["pytest_command"],
                    "severity": tests["severity"],
                },
                "complexity": {
                    "max_cognitive_complexity": compl["max_cognitive_complexity"],
                    "max_nesting_depth": compl["max_nesting_depth"],
                    "high_complexity_symbols": compl["high_complexity_symbols"],
                    "severity": compl["severity"],
                    # W331: same canonical definition as cmd_complexity.
                    "complexity_definition": COGNITIVE_COMPLEXITY_DEFINITION,
                },
                "coupling": {
                    "coupled_files": coupl["coupled_files"],
                    "missing_partners": coupl["missing_partners"],
                    "severity": coupl["severity"],
                },
                "conventions": {
                    "violation_count": convs["violation_count"],
                    "violations": convs["violations"],
                    "severity": convs["severity"],
                    "majority_threshold_pct": convs.get("majority_threshold_pct"),
                    "kinds_with_majority": convs.get("kinds_with_majority"),
                },
                "fitness": {
                    "rules_checked": fitns["rules_checked"],
                    "rules_failed": fitns["rules_failed"],
                    "rules_failing_on_target": fitns.get("rules_failing_on_target", 0),
                    "rules_failing_on_siblings": fitns.get("rules_failing_on_siblings", 0),
                    "total_violations": fitns["total_violations"],
                    "failed_rules": fitns["failed_rules"],
                    "failed_rules_on_siblings": fitns.get("failed_rules_on_siblings", []),
                    "rule_details": fitns["rule_details"],
                    "severity": fitns["severity"],
                },
            }
            if disclosure is not None:
                _summary_dict["resolution"] = disclosure["resolution"]
                _summary_dict["partial_success"] = disclosure["partial_success"]
                _envelope_kwargs["resolution"] = disclosure["resolution"]
                _envelope_kwargs["partial_success"] = disclosure["partial_success"]
            return {"summary_dict": _summary_dict, "envelope_kwargs": _envelope_kwargs}

        # Floor on degrade: minimal summary + empty kwargs so the
        # serialize_envelope substrate still has structurally valid
        # input. The verdict literal floor preserved separately.
        _assembled = _run_check_ec(
            "assemble_sections",
            _assemble_sections,
            default={
                "summary_dict": {"verdict": verdict, "partial_success": True},
                "envelope_kwargs": {},
            },
        )
        summary_dict: dict = (
            _assembled.get("summary_dict", {"verdict": verdict})
            if isinstance(_assembled, dict)
            else {"verdict": verdict}
        )
        envelope_kwargs: dict = _assembled.get("envelope_kwargs", {}) if isinstance(_assembled, dict) else {}
        # The assemble_sections floor builds a free-standing summary_dict
        # so the envelope_kwargs lookup happens BEFORE we re-tie
        # ``summary`` in. Re-stamp so non-degraded callers see the same
        # shape as the pre-W607-EC envelope.
        if "summary" not in envelope_kwargs:
            envelope_kwargs["summary"] = summary_dict

        # W607-R + W607-AW + W607-EC — surface substrate-CALL markers on
        # the success path. All three buckets share the ``preflight_*``
        # marker family (W607-AW is additive coverage of aggregation-phase
        # boundaries on top of W607-R's helper-call boundaries; W607-EC
        # is additive coverage of POST-capture phases on top of both).
        # Combine all three BEFORE threading into the envelope so
        # consumers see the full degradation lineage in marker-emission
        # order.
        #
        # Empty combined bucket → byte-identical envelope (no warnings_out
        # keys added; no partial_success flip from this axis). Non-empty
        # → warnings_out at top-level AND summary mirror, plus
        # summary.partial_success=True so the agent can distinguish
        # "clean preflight" from "preflight ran with substrate
        # degradation" via the summary alone.
        _combined_warnings_out = list(_w607r_warnings_out) + list(_w607aw_warnings_out) + list(_w607ec_warnings_out)
        if _combined_warnings_out:
            summary_dict["warnings_out"] = list(_combined_warnings_out)
            summary_dict["partial_success"] = True
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)
            envelope_kwargs["partial_success"] = True
        preflight_envelope = json_envelope("preflight", **envelope_kwargs)

        # Auto-log into the active run (silent no-op if no run is active).
        # Strip the "(file:line)" suffix the resolver appends so the
        # target on disk matches what the agent typed.
        # W607-AW — wrap the active-run write so HMAC chain-misshape /
        # filesystem failures / .roam/runs corruption surface as
        # ``preflight_auto_log_failed:...`` instead of crashing the
        # envelope after it was already built. Discipline mirror of the
        # W607-AS HMAC-failure-aborts-write pattern in cmd_runs: the
        # marker emission keeps the envelope intact while still
        # disclosing the ledger-write failure to the agent.
        _auto_target = label or ""
        if isinstance(_auto_target, str):
            _auto_target = _auto_target.removesuffix(" (not found)").split(" (", 1)[0]
        _run_check_aw(
            "auto_log",
            auto_log,
            preflight_envelope,
            action="preflight",
            target=_auto_target,
            repo_root=root,
            default=None,
        )
        # W607-AW — if ``auto_log`` raised, rebuild the envelope so the
        # marker reaches the JSON output. Empty bucket (clean auto_log)
        # → preflight_envelope stays byte-identical to the version
        # already built above.
        if _w607aw_warnings_out and not any(
            m.startswith("preflight_auto_log_failed:") for m in (summary_dict.get("warnings_out") or [])
        ):
            _combined_warnings_out = list(_w607r_warnings_out) + list(_w607aw_warnings_out) + list(_w607ec_warnings_out)
            summary_dict["warnings_out"] = list(_combined_warnings_out)
            summary_dict["partial_success"] = True
            envelope_kwargs["warnings_out"] = list(_combined_warnings_out)
            envelope_kwargs["partial_success"] = True
            preflight_envelope = json_envelope("preflight", **envelope_kwargs)

        # JSON output
        if json_mode:
            # W607-EC ``serialize_envelope`` substrate boundary: a raise
            # in ``to_json`` (e.g. a non-JSON-serializable section
            # payload, a __str__ raise on a degraded check entry)
            # surfaces as ``preflight_serialize_envelope_failed:...`` and
            # the command still emits a minimal hand-rolled JSON envelope
            # on the degraded path (Pattern-1 variant C guard — never
            # empty stdout).
            def _serialize_envelope():
                return to_json(preflight_envelope)

            rendered = _run_check_ec("serialize_envelope", _serialize_envelope, default=None)
            # W978 #6: ``rendered is None`` guard before echo so a
            # degraded serialize_envelope does not crash on the print
            # path. The minimal hand-rolled fallback re-surfaces the
            # markers + verdict so consumers reading stdout see the
            # disclosure.
            if rendered is None:
                import json as _json_fallback

                _fallback_summary = {
                    "verdict": verdict,
                    "partial_success": True,
                    "warnings_out": (
                        list(_w607r_warnings_out) + list(_w607aw_warnings_out) + list(_w607ec_warnings_out)
                    ),
                }
                click.echo(
                    _json_fallback.dumps(
                        {
                            "command": "preflight",
                            "summary": _fallback_summary,
                            "warnings_out": _fallback_summary["warnings_out"],
                        }
                    )
                )
            else:
                click.echo(rendered)
            return

        # W607-EC ``format_text`` substrate boundary: a raise during any
        # click.echo formatting (e.g. a __str__ raise on a degraded
        # check entry, a missing key on a degraded sub-envelope) surfaces
        # a ``preflight_format_text_failed:...`` marker rather than
        # crashing the whole command.
        def _format_text():
            # Text output
            click.echo(f"VERDICT: {verdict}\n")
            click.echo(f"Pre-flight check for `{label}`:\n")

            # Blast radius
            blast_desc = f"{blast['affected_symbols']} symbols in {blast['affected_files']} files"
            click.echo(f"  Blast radius:     {blast_desc:<40s} {_severity_tag(blast['severity'])}")

            # Affected tests
            test_desc = f"{tests['direct']} direct, {tests['transitive']} transitive"
            if tests["colocated"]:
                test_desc += f", {tests['colocated']} colocated"
            click.echo(f"  Affected tests:   {test_desc:<40s} {_severity_tag(tests['severity'])}")

            # Complexity
            cc = compl["max_cognitive_complexity"]
            nest = compl["max_nesting_depth"]
            compl_desc = f"cc={cc:.0f}, nest={nest}"
            click.echo(f"  Complexity:       {compl_desc:<40s} {_severity_tag(compl['severity'])}")

            # Coupling
            if coupl["coupled_files"] > 0:
                coupl_desc = f"{coupl['coupled_files']} files often change together"
            else:
                coupl_desc = "no missing co-change partners"
            click.echo(f"  Coupling:         {coupl_desc:<40s} {_severity_tag(coupl['severity'])}")

            # Conventions
            if convs["violation_count"] > 0:
                conv_desc = f"{convs['violation_count']} naming violations"
            else:
                conv_desc = "no violations"
            click.echo(f"  Conventions:      {conv_desc:<40s} {_severity_tag(convs['severity'])}")

            # Fitness — distinguish target-attributed vs sibling failures
            # . The same rule can fail because of OTHER code in
            # the same file ("Max function complexity 50" tripped by a
            # 700-cc neighbour); blaming the changing symbol for that is
            # misleading. We surface both buckets explicitly.
            # Helper: append a ``(name, name, ...)`` parenthetical only when
            # there are names to show — never print a hollow ``()``.
            def _with_names(text: str, names: list[str]) -> str:
                shown = [n for n in names[:3] if n]
                return f"{text} ({', '.join(shown)})" if shown else text

            if fitns["rules_checked"] == 0:
                fit_desc = "no rules configured"
            elif fitns.get("rules_failing_on_target", 0) > 0:
                fit_desc = _with_names(
                    f"{fitns['rules_failing_on_target']} rules currently fail on target",
                    fitns.get("failed_rules") or [],
                )
            elif fitns.get("rules_failing_on_siblings", 0) > 0:
                # Sibling-only failures: the target is clean. Name them from
                # ``failed_rules_on_siblings`` (which agrees with rule_details)
                # — ``failed_rules`` is target-attributed and empty here.
                fit_desc = _with_names(
                    f"target passes; {fitns['rules_failing_on_siblings']} rule(s) fail on sibling symbols",
                    fitns.get("failed_rules_on_siblings") or [],
                )
            elif fitns["rules_failed"] > 0:
                fit_desc = _with_names(
                    f"{fitns['rules_failed']} rules currently fail",
                    fitns.get("failed_rules") or [],
                )
            else:
                fit_desc = f"all {fitns['rules_checked']} rules pass"
            click.echo(f"  Fitness:          {fit_desc:<40s} {_severity_tag(fitns['severity'])}")

            # Overall
            click.echo(f"\n  Overall risk: {risk}")

            # Risk driver — name the row that's pushing the verdict so an
            # agent doesn't have to deduce it.
            # Pick the highest-severity row, breaking ties by category
            # priority (complexity > fitness > tests > coupling > blast >
            # conventions — most actionable first).
            # W607-AW — wrap the driver compute so a malformed severity dict
            # surfaces as ``preflight_risk_driver_failed:...`` and the text
            # output still emits cleanly (driver becomes empty string —
            # ``_risk_driver``'s own clean floor when no row drives the
            # verdict).
            driver = _run_check_aw(
                "risk_driver",
                _risk_driver,
                blast,
                tests,
                compl,
                coupl,
                convs,
                fitns,
                default="",
            )
            if driver:
                click.echo(f"  Risk driver:  {driver}")

            # Suggested tests
            if tests["pytest_command"]:
                click.echo(f"  Suggested tests: {tests['pytest_command']}")

            # — synergy with the rest of the surface. After a
            # preflight verdict the natural follow-ups depend on the risk
            # level: HIGH/CRITICAL → impact + diagnose; MEDIUM → affected-
            # tests; LOW → roam diff after editing. Centralised in
            # ``next_steps.suggest_next_steps`` so the wording stays
            # consistent across CLI and the JSON envelope. Strip the
            # ``(file:line)`` suffix the resolver appends to ``label`` so
            # the next-step commands carry only the bare symbol name.
            _ns_symbol = label or ""
            if isinstance(_ns_symbol, str):
                _ns_symbol = _ns_symbol.removesuffix(" (not found)").split(" (", 1)[0]
            _ns = suggest_next_steps(
                "preflight",
                {"symbol": _ns_symbol, "risk_level": risk},
            )
            _ns_text = format_next_steps_text(_ns)
            if _ns_text:
                click.echo(_ns_text)
            return None

        _run_check_ec("format_text", _format_text, default=None)
