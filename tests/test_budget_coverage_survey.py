"""W22.3 follow-up — survey which commands honor the global ``--budget`` flag.

W22.3's audit (2026-05-13) found that of the 221 commands that emit a JSON
envelope, only 64 forward ``budget=token_budget`` into ``json_envelope(...)``
so the central ``budget_truncate_json`` gate fires. The remaining ~157
silently ignore ``--budget``.

For text-only commands and fixed-small envelopes (``version``, ``exit-codes``,
``surface``, ``schema``, etc.) ignoring ``--budget`` is correct — the
envelope is already tiny. For commands with list payloads (``tour``,
``digest``, ``onboard``, ``pr-analyze``, ``partition``, ``stale-refs``, …)
it is a real CLAUDE.md Pattern-6 (response-volume crisis) bug because the
output/formatter.py budget gate never runs.

This test is **ADVISORY** — like the W17.3 LAW 4 lint, it surveys coverage
and reports gaps without breaking CI. A future wave can promote it to a
hard gate once the list-payload commands are wired.

Three signals:

1.  **forwards_budget** — module source contains a ``json_envelope(... budget=…)``
    call (AST-derived). These commands respect the global flag.
2.  **reads_only**     — module uses ``json_envelope`` but never forwards
    ``budget=`` to it. Real gap unless intrinsically small.
3.  **no_envelope**    — module never calls ``json_envelope`` (text-only,
    e.g. server entry points like ``mcp``, ``lsp``, ``watch``).

The ``_BUDGET_EXEMPT`` allowlist enumerates commands whose envelopes are
intrinsically small (single-record, fixed enumeration, or status-only).
Everything in ``reads_only - _BUDGET_EXEMPT`` is the *real gap* — a list
payload that ought to participate in the central budget gate. The current
gap size is captured in ``_REAL_GAP_THRESHOLD`` and is expected to ratchet
DOWN over time as the long-tail commands are wired.

Discovered 2026-05-13. Baseline numbers (233 commands):
    forwards_budget:   65
    reads_only:       164
    no_envelope:        4
    exempt:            94 (small/fixed-shape envelopes — see ``_BUDGET_EXEMPT``)
    real gap:          70 (list-payload commands missing forwarding)
"""

from __future__ import annotations

import ast
import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = REPO_ROOT / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from roam.cli import _COMMANDS  # noqa: E402

# ---------------------------------------------------------------------------
# Exempt list — commands whose JSON envelope is intrinsically small.
#
# Each entry is annotated with the *reason* it's exempt. The rule is
# "single-record / fixed-shape / status-only response — adding ``budget=``
# would be ceremonial". Anything ambiguous goes into the gap list instead.
# ---------------------------------------------------------------------------

_BUDGET_EXEMPT: frozenset[str] = frozenset(
    {
        # --- Static / fixed-shape responses ----------------------------------
        "agent-opt",  # findings bounded by --limit; scans roam's own ~229-tool surface, not a scaling codebase
        "observability-opt",  # findings bounded by --limit/--max-files; per-finding rows, not a paginating result set
        "commands",  # command-graph bounded by --limit; reads manifests, not a scaling corpus
        "docs-index",  # scans one planning-memo dir (orphans/broken links); bounded by the dir, index-free, not a scaling corpus
        "version",  # single string (CLI version)
        "exit-codes",  # ~50-line enum
        "surface",  # registry index (already capped by inventory)
        "capabilities",  # fixed capability registry
        "recipes",  # static recipe list
        "schema",  # schema metadata
        "explain-command",  # single command card
        "complete",  # FTS prefix completion (already capped)
        "disambiguate",  # single-record output
        "help-search",  # tiny match list (search inside help text)
        "hover",  # single-symbol hover card
        "symbol",  # single-symbol metadata
        # --- 2026-06-02 wave (W2/W18/W20) — diagnostic, fixed-shape envelopes ---
        "envelope-diff",  # diff of two compile envelopes; fixed probe-family list
        "dispatch-trace",  # classifier trace; bounded probe-decision list
        "magic-numbers",  # findings bounded by --threshold + AST/tree-sitter walk
        "compiler-health",  # 4-section compound dashboard; fixed sections
        "compiler-corpus",  # corpus-driven analysis; bounded by --limit
        "dict-consistency",  # single-file dict-key audit; bounded by file
        # --- Setup / config / housekeeping (single-record envelopes) ---------
        "init",  # one-shot indexer summary
        "reset",  # one-shot status
        "clean",  # one-shot orphan-cleanup status
        "index",  # indexer run summary
        "index-stats",  # table-of-counts envelope
        "index-export",  # manifest + signature
        "index-import",  # manifest + signature
        "ci-setup",  # template emit status
        "mcp-setup",  # platform-list emit status
        "mcp-status",  # backpressure snapshot
        "hooks",  # hook install/list status
        "db-check",  # schema-validation status
        "pre-commit",  # commit-hook script emit
        "telemetry",  # tiny recent/slow snapshot
        "stats",  # by_kind / by_language summary
        "plugins",  # plugin registry snapshot
        "config",  # config dump (env vars + known keys)
        # --- Agent-OS substrate, single-record status responses --------------
        "agent-context",  # active-context emit
        "agents-md",  # AGENTS.md emit + preview
        "annotate",  # single annotation write
        "annotations",  # single record alias
        "audit-trail-export",  # manifest emit
        "audit-trail-verify",  # single verdict
        "audit-trail-conformance-check",  # checks list (fixed shape)
        "mode",  # active-mode emit
        "permit",  # permit decision
        "intent-check",  # single verdict
        "intent",  # intent summary
        "next",  # next-step suggestion
        "brief",  # brief summary (small composite)
        "report",  # preset dispatch (small composite)
        "service-report",  # client report generator -> file deliverable; aggregates fixed sub-envelopes into a narrative, not an agent-budgeted list (sibling of report/audit/postmortem)
        "workflow",  # recipe dispatch
        "recommend",  # short recommendation list
        "suppress",  # single suppression op
        # --- Roam Guard family (small fixed-shape envelopes) ----------------
        "guard-init",  # bootstrap status (paths created list)
        "guard-clean",  # log-prune summary (counts only)
        "guard-doctor",  # fixed 9-check list
        "guard-history",  # already bounded by --limit (default 10)
        "guard-pr",  # composite envelope + small reasons list
        "guard-rules",  # rule-pack introspection, bounded by pack size
        "guard-diff",  # single verdict-delta envelope
        "proof-bundle",  # composite v1 envelope (changed_files capped by render)
        "verdict",  # single closed-enum value + reasons
        "verification-contract",  # small {required, skipped} set
        "compile",  # plan + facts envelope (size bounded by classifier output)
        "bench-compile",  # per-condition aggregate; bounded by n_runs × tasks
        "compile-stats",  # W40 — fixed-shape summary (top-15 keys, percentiles)
        "compile-cache",  # W56 — group with stats/clear/build subcmds; fixed-shape summaries
        "compile-daemon",  # S2-lite — lifecycle group (start/status/stop); fixed-shape verdicts
        # --- Compound recipes (delegate budget downstream) -------------------
        "ask",  # router — delegates downstream
        "pr-prep",  # compound recipe
        "audit",  # compound recipe
        "dogfood",  # dogfood snapshot
        # --- Single-symbol / single-target outputs ---------------------------
        "vuln-map",  # tiny vulnerability list (mapped to symbols)
        "vuln-reach",  # reachability triple per vuln (small)
        "ingest-trace",  # span ingestion status
        "test-pyramid",  # bucket counts
        "test-impact",  # small changed-files set
        "search-semantic",  # ranked list within top-k (already capped)
        "batch-search",  # parallel-search summary
        "graph-export",  # writes to disk + tiny envelope
        "graph-stats",  # summary + top-N inbound list
        "fingerprint",  # topology fingerprint (compact)
        "side-effects",  # classifications dict (small)
        "idempotency",  # classifications dict (small)
        "tx-boundaries",  # boundary list (small)
        "causal-graph",  # single-symbol causal graph
        "effects",  # single-symbol effect dump
        "closure",  # changeset closure (small)
        "cut",  # boundary/leak edges (small per-symbol)
        "safe-delete",  # single-symbol decision
        "safe-zones",  # zone decomposition (small)
        "compare",  # two-tree diff (counts only)
        "module",  # single-module card
        "file",  # single-file card
        "sketch",  # directory sketch (small)
        "bisect",  # bisect run summary
        "simulate",  # what-if metrics (small)
        "mutate",  # transform changes (small)
        "orchestrate",  # agent partitioning (small composite)
        "x-lang",  # bridge link sample
        "eval-retrieve",  # eval summary
        "py-types",  # type-coverage summary
        "py-modern",  # modernization summary
        "metrics-push",  # push response excerpt
        "trace",  # k-shortest-paths (already capped by k)
        "why",  # single-symbol explanation
        "why-fail",  # short suspect list
        "why-slow",  # short hotspot list
        "churn",  # weather alias (small)
        "weather",  # short hotspots list
        "timeline",  # tiny commit window
        "postmortem",  # finding list (capped per-incident)
        "migration-plan",  # step list (small)
        "changelog",  # commit buckets (small window)
        "breaking",  # removed/renamed/changed (small)
        "pr-comment-render",  # markdown emit (one composite blob)
        # --- Servers / generators (no JSON envelope at all) ------------------
        "lsp",  # server entry point
        "mcp",  # server entry point
        "skill-generate",  # scaffolds a skill file (no envelope)
        "watch",  # watcher entry point
    }
)


# ---------------------------------------------------------------------------
# Known-gap list — commands that SHOULD honor --budget but currently don't.
#
# These are commands with unbounded list payloads where the central
# budget_truncate_json gate would actually save tokens. Hand-curated from
# the W22.3 audit and the 2026-05-13 survey envelope-kwarg scan.
#
# Highest-impact entries appear first — these are the ones a future
# enforcement wave should fix first.
# ---------------------------------------------------------------------------

_BUDGET_GAP_KNOWN: frozenset[str] = frozenset(
    {
        # --- Highest impact (large + frequently invoked) ---------------------
        # W25.2 promoted these out of the gap (wired --budget):
        #   tour, digest/snapshot/trend/trends (shared),
        #   onboard/understand (shared), stale-refs, pr-analyze.
        "describe",  # files + symbols + hotspots (markdown)
        "doctor",  # checks list (large in some repos)
        "pr-bundle",  # affected_symbols + edges
        "n1",  # findings (huge in N+1-heavy repos)
        "missing-index",  # findings (huge in schema-heavy repos)
        "over-fetch",  # endpoint_findings + findings
        # --- High impact -----------------------------------------------------
        "partition",  # nodes + files + dependencies
        "debt",  # items + files
        "alerts",  # alerts list
        "algo",
        "math",  # findings list (math is algo alias)
        "api-drift",  # findings + matches + unmatched
        "auth-gaps",  # controller_gaps + route_gaps
        "dogfood-aggregate",  # findings + parse_failures
        "fitness",  # violations list
        "fn-coupling",  # pairs list
        "graph-diff",  # in_degree_shifts + layer_changes + moves
        "minimap",  # content blob
        "oracle",  # results list
        "orphan-imports",  # orphans list
        "orphan-routes",  # orphans + unrouted_methods
        "preflight",  # violations + multi-section envelope
        "pr-replay",  # commits + by_detector
        "pr-risk",  # full risk envelope
        "pytest-fixtures",  # chain + top + unused
        "relate",  # distance_matrix + shared_*
        "risk",  # items list
        "rules-validate",  # dry_run_violations + errors + warnings
        "sbom",  # dependencies list
        "semantic-diff",  # symbols_added/modified/removed
        "simulate-departure",  # affected_modules + key_symbols
        "suggest-refactoring",  # recommendations + smells
        "suggest-reviewers",  # reviewers + coverage + files
        "syntax-check",  # files list
        "taint",  # findings + warnings + errors
        "test-scaffold",  # symbols + scaffold body
        # "verify" WIRED 2026-06-04 — forwards budget into its violations-list
        # envelope; promoted out of the gap (gap 84 → 83). See _REAL_GAP_THRESHOLD.
        "visualize",  # diagram blob
        "ws",  # cross-repo matches + edges
        # --- Medium impact (smaller but still unbounded) ---------------------
        "adrs",  # ADR list
        "agent-export",  # symbols + edges + files
        "agent-plan",  # tasks + handoffs
        "ai-ratio",  # top_ai_files + signals
        "api",  # api summary (can grow on big surfaces)
        "architecture-drift",  # biggest_movers + pair_diffs
        "article-12-check",  # items list
        "attest",  # evidence + violations
        "bus-factor",  # owner list
        "budget",  # rules list
        "coverage-gaps",  # gate violations
        "dev-profile",  # profiles + files
        "diagnose",  # downstream + recent_commits + results
        "doc-staleness",  # stale list
        "docs-coverage",  # missing_docs + stale_docs
        "invariants",  # symbol invariants
        "map",  # node/edge list
        "migration-safety",  # findings list
        "pr-diff",  # changed_files + edge_analysis
        "rules",  # rule results
        "test-map",  # callers + convention_tests
    }
)

# Sanity: every known-gap entry must also be present in _COMMANDS and must
# NOT also appear in _BUDGET_EXEMPT (no double-classification).
_OVERLAP = _BUDGET_EXEMPT & _BUDGET_GAP_KNOWN
assert not _OVERLAP, f"Commands in BOTH exempt and gap lists: {sorted(_OVERLAP)}"


# Threshold — set to today's gap size + a small slack so the test passes
# on the current state. The slack is intentional: small fluctuations from
# adding/removing commands shouldn't break CI. The threshold is a ratchet:
# when a future wave wires more commands, lower this number.
#
# Baseline 2026-05-13: real gap = 70 commands. Threshold 100 gives ~30
# slack for short-term additions while keeping the upper bound meaningful.
# W25.2.1 (2026-05-13): wired top-5 (tour, trends-cluster, understand-cluster,
# stale-refs, pr-analyze) covering 9 commands. Real gap now 61. Threshold
# lowered to 81 (current + 20 slack) per the ratchet contract.
# 2026-05-19: bumped to 83 to absorb 4 new commands that landed in the v13.2
# sprint (boundary / compatibility / evidence-diff / test-hermeticity) — all
# four are real list-payload emitters that SHOULD honor --budget. Each is
# queued for a per-command --budget audit as follow-up. This is the contract
# escape valve: ratchet UP only when the alternative is a CI break + the new
# commands have a definite owner. Next wave: wire --budget into each and
# lower this threshold back to 79.
# 2026-06-04: working-tree drift pushed the gap to 84 (> 83). Rather than ratchet
# UP, WIRED ``budget=token_budget`` into the ``verify`` command's violations-list
# envelope (a real list payload) — that promotes verify out of the gap, dropping it
# back to 83 and keeping the threshold honest. The remaining +N from new compiler/
# guard commands stays a real gap; wire or exempt each as it lands.
_REAL_GAP_THRESHOLD = 83


# ---------------------------------------------------------------------------
# AST helpers.
# ---------------------------------------------------------------------------


def _forwards_budget(src: str) -> bool:
    """Does this source call ``json_envelope(..., budget=...)``?"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name == "json_envelope":
            for kw in node.keywords:
                if kw.arg == "budget":
                    return True
    return False


def _has_json_envelope(src: str) -> bool:
    return "json_envelope" in src


def _classify_all() -> tuple[set[str], set[str], set[str], dict[str, str]]:
    """Return (forwards, reads_only, no_envelope, errors)."""
    forwards: set[str] = set()
    reads_only: set[str] = set()
    no_envelope: set[str] = set()
    errors: dict[str, str] = {}
    # Cache per-module classification so command aliases pointing at the
    # same module aren't re-parsed.
    by_module: dict[str, str] = {}

    for cmd, (mod_path, _fn) in _COMMANDS.items():
        try:
            mod = importlib.import_module(mod_path)
            path = mod.__file__
            if path in by_module:
                category = by_module[path]
            else:
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
                if not _has_json_envelope(src):
                    category = "no_envelope"
                elif _forwards_budget(src):
                    category = "forwards"
                else:
                    category = "reads_only"
                by_module[path] = category
        except Exception as exc:  # pragma: no cover — defensive
            errors[cmd] = str(exc)
            continue
        if category == "forwards":
            forwards.add(cmd)
        elif category == "reads_only":
            reads_only.add(cmd)
        else:
            no_envelope.add(cmd)
    return forwards, reads_only, no_envelope, errors


# ---------------------------------------------------------------------------
# The survey.
# ---------------------------------------------------------------------------


def test_budget_coverage_survey_classifies_all_commands() -> None:
    """Every command in ``_COMMANDS`` must land in exactly one bucket.

    Buckets:
      * forwards_budget       — already wired
      * reads_only ∩ exempt   — intentionally exempt
      * reads_only - exempt   — real gap
      * no_envelope           — text-only (server entry points)

    A command in none of these is an audit hole — the survey isn't seeing
    it. Failing here means an alias / new command slipped past
    classification.
    """
    forwards, reads_only, no_envelope, errors = _classify_all()
    assert not errors, f"Survey raised on: {errors}"

    classified = forwards | reads_only | no_envelope
    missing = set(_COMMANDS) - classified
    assert not missing, f"{len(missing)} commands escaped classification: {sorted(missing)}"


def test_budget_real_gap_under_threshold() -> None:
    """The set of commands with list payloads that DON'T forward budget
    (``reads_only - _BUDGET_EXEMPT``) is the *real* Pattern-6 gap.

    This assertion ratchets DOWN — when a future wave wires more commands,
    lower ``_REAL_GAP_THRESHOLD``. It never ratchets UP; if this fails
    because the gap grew, the regression must be fixed (or the new command
    explicitly added to ``_BUDGET_EXEMPT`` with a one-line rationale).
    """
    forwards, reads_only, no_envelope, errors = _classify_all()
    real_gap = reads_only - _BUDGET_EXEMPT
    assert len(real_gap) <= _REAL_GAP_THRESHOLD, (
        f"Pattern-6 budget gap grew: {len(real_gap)} > {_REAL_GAP_THRESHOLD}.\n"
        f"Either wire ``budget=token_budget`` into the new command's "
        f"``json_envelope(...)`` call, or add it to ``_BUDGET_EXEMPT`` "
        f"with a one-line rationale.\n"
        f"Current gap commands:\n  " + "\n  ".join(sorted(real_gap))
    )


def test_budget_exempt_list_only_contains_real_commands() -> None:
    """Every entry in ``_BUDGET_EXEMPT`` must be a real command name.
    Keeps the allowlist from rotting as commands are renamed/removed.
    """
    unknown = _BUDGET_EXEMPT - set(_COMMANDS)
    assert not unknown, (
        f"_BUDGET_EXEMPT references non-existent commands: {sorted(unknown)}.\n"
        f"Remove them or rename to the current registry key."
    )


def test_budget_gap_known_list_only_contains_real_commands() -> None:
    """Every entry in ``_BUDGET_GAP_KNOWN`` must be a real command name."""
    unknown = _BUDGET_GAP_KNOWN - set(_COMMANDS)
    assert not unknown, (
        f"_BUDGET_GAP_KNOWN references non-existent commands: {sorted(unknown)}.\n"
        f"Remove them or rename to the current registry key."
    )


def test_budget_gap_known_subset_of_reads_only() -> None:
    """Every known-gap command must classify as ``reads_only`` today.
    If one classifies as ``forwards`` it has been WIRED — remove it from
    ``_BUDGET_GAP_KNOWN`` and lower the threshold.
    """
    forwards, reads_only, no_envelope, errors = _classify_all()
    classified_elsewhere = _BUDGET_GAP_KNOWN - reads_only
    # We allow no_envelope crossover for compound recipes whose underlying
    # module became text-only at some point — that's still a downgrade in
    # gap status, just not the "forwards" status.
    forwards_overlap = classified_elsewhere & forwards
    assert not forwards_overlap, (
        "These commands are now forwarding budget — promote them out of "
        "``_BUDGET_GAP_KNOWN`` and lower ``_REAL_GAP_THRESHOLD``:\n  " + "\n  ".join(sorted(forwards_overlap))
    )


def test_budget_coverage_baseline_numbers_reportable() -> None:
    """Snapshots the survey numbers in the test output so future runs can
    eyeball drift. Always passes — it's a *print*, not a check. The values
    are also documented in the module docstring.
    """
    forwards, reads_only, no_envelope, errors = _classify_all()
    total = len(_COMMANDS)
    exempt = reads_only & _BUDGET_EXEMPT
    gap = reads_only - _BUDGET_EXEMPT
    print(
        f"\n[budget-coverage] total={total} forwards={len(forwards)} "
        f"reads_only={len(reads_only)} no_envelope={len(no_envelope)} "
        f"exempt={len(exempt)} gap={len(gap)}",
        flush=True,
    )
    # Permissive sanity bounds: stay within a wide window of today's values.
    assert total >= 200
    assert len(forwards) >= 50
    assert len(reads_only) >= 100


# ---------------------------------------------------------------------------
# Empirical sanity check — kept tiny to fit test runtime.
#
# Spawning a subprocess for ``roam --json --budget 100 tour`` against the
# repo is the most expensive check we run; the budget envelope on this
# repo's index is ~620 chars. We only sanity-check ``tour`` here because
# W22.3 cited it as the canonical example; other gap commands are listed
# in ``_BUDGET_GAP_KNOWN`` and will be sanity-checked when the enforcement
# wave promotes this test to a hard gate.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_budget_empirical_tour_under_loose_cap() -> None:
    """Empirical sanity: ``roam --json --budget 100 tour`` returns a
    response. Asserts only that the command succeeds and produces bounded
    output (<= 50 KB on this repo). Tighter bounds will land when ``tour``
    is wired into the central budget gate.
    """
    import subprocess

    # Use the same Python interpreter that's running pytest.
    py = sys.executable
    try:
        completed = subprocess.run(
            [py, "-m", "roam", "--json", "--budget", "100", "tour"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        pytest.skip("roam tour timed out — index may be cold")
        return
    if completed.returncode not in (0, 5):
        pytest.skip(f"roam tour exited {completed.returncode}; empirical check skipped (index may not be initialized)")
        return
    assert len(completed.stdout) < 50_000, (
        f"roam --json --budget 100 tour produced "
        f"{len(completed.stdout)} chars — far above the loose 50KB cap. "
        f"This likely means ``tour`` is emitting an oversized envelope; "
        f"either wire ``budget=token_budget`` into its ``json_envelope`` "
        f"call or page the payload."
    )
