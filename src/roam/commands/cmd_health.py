"""Detect and report code health issues.

Baseline-diff mode (``--baseline <ref>``)
-----------------------------------------

When ``--baseline <ref>`` is supplied, ``roam health`` reports DELTAS against
a previously-stored snapshot instead of the absolute set of findings. The
delta surface is what most users actually care about: "what is new since the
last green run, what got fixed, what regressed."

``<ref>`` accepts three forms:

* a git ref (``main``, ``v12.0``, a SHA prefix) — compare against the most
  recent snapshot recorded with a matching ``git_branch`` or ``git_commit``;
* ``last`` — compare against the most recent snapshot on file regardless of
  ref (useful for "did I make things worse since I last saved?");
* ``auto`` — compare against the most recent snapshot whose ``git_branch``
  matches the project's main branch (``main`` or ``master``). Sensible CI
  default.

Verdict semantics in baseline mode:

* ``OK`` — no new findings and no score regression.
* ``REVIEW`` — at least one new high-severity finding, even if the score
  did not move.
* ``BAD`` — composite ``health_score`` regressed against the baseline.

If no baseline snapshot can be located for ``<ref>`` the command prints a
friendly explanation, marks the run as ``DEGRADED`` (``summary.reason =
"no_baseline_snapshot"``), and exits cleanly. Snapshots are populated by
``roam trends --save`` (typically run in CI on the main branch).
"""

from __future__ import annotations

import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import click

from roam.commands.next_steps import format_next_steps_text, suggest_next_steps
from roam.commands.resolve import ensure_index
from roam.coverage_reports import imported_coverage_overview
from roam.db.connection import batched_in, open_db
from roam.db.queries import TOP_BY_BETWEENNESS, TOP_BY_DEGREE
from roam.graph.builder import build_symbol_graph
from roam.graph.cycles import (
    algebraic_connectivity,
    find_cycles,
    find_weakest_edge,
    format_cycles,
    mark_actionable_cycles,
    propagation_cost,
)
from roam.graph.layers import detect_layers, find_violations
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    summary_envelope,
    to_json,
)
from roam.output.framework_filter import FRAMEWORK_PRIMITIVE_NAMES as _FRAMEWORK_NAMES

# ---- Location-aware utility detection ----

_UTILITY_PATH_PATTERNS = (
    "composables/",
    "utils/",
    "services/",
    "lib/",
    "helpers/",
    "shared/",
    "config/",
    "core/",
    "hooks/",
    "stores/",
    "output/",
    "db/",
    "common/",
    "internal/",
    "infra/",
    # infrastructure hubs that are EXPECTED to have high
    # fan-in. Without these patterns the health-score classifier
    # mislabels architectural roots (Click root group, MCP dispatch,
    # graph builder, file-role classifier) as actionable refactor
    # targets, which they are not.
    "graph/",
    "mcp_extras/",
    "languages/",
)

_UTILITY_FILE_PATTERNS = (
    "resolve.py",
    "helpers.py",
    "common.py",
    "base.py",
    # single-file architectural hubs. Same reasoning as
    # ``_UTILITY_PATH_PATTERNS`` additions above.
    "cli.py",
    "mcp_server.py",
    "file_roles.py",
)

# Paths that are NOT production code — treat as expected utilities
_NON_PRODUCTION_PATH_PATTERNS = (
    "tests/",
    "test/",
    "__tests__/",
    "spec/",
    "dev/",
    "scripts/",
    "bin/",
    "benchmark/",
    "conftest.py",
)


def _is_utility_path(file_path):
    """Check if a file is in a utility/infrastructure directory or is a known utility file."""
    p = file_path.replace("\\", "/").lower()
    if any(pat in p for pat in _UTILITY_PATH_PATTERNS):
        return True
    if any(pat in p for pat in _NON_PRODUCTION_PATH_PATTERNS):
        return True
    basename = p.rsplit("/", 1)[-1] if "/" in p else p
    return basename in _UTILITY_FILE_PATTERNS


def _percentile(sorted_values, pct):
    """Linear-interpolated percentile from a sorted numeric list."""
    if not sorted_values:
        return 0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, n - 1)
    if lo == hi:
        return sorted_values[lo]
    frac = k - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _unique_dirs(file_paths):
    """Extract unique parent directory names from a list of file paths."""
    dirs = set()
    for fp in file_paths:
        p = fp.replace("\\", "/")
        last_slash = p.rfind("/")
        if last_slash >= 0:
            dirs.add(p[:last_slash])
        else:
            dirs.add(".")
    return dirs


def _severity_counts(items):
    counts = {"CRITICAL": 0, "WARNING": 0, "INFO": 0}
    for item in items:
        sev = item.get("severity", "INFO")
        if sev in counts:
            counts[sev] += 1
    return counts


def _format_severity_counts(counts):
    parts = []
    for sev in ("CRITICAL", "WARNING", "INFO"):
        if counts.get(sev, 0):
            parts.append(f"{counts[sev]} {sev}")
    return ", ".join(parts) if parts else "0 issues"


def _parse_simple_yaml(text: str) -> dict:
    """Parse a flat YAML file with one top-level section (no PyYAML needed)."""
    result: dict[str, dict] = {}
    current_section = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace() and stripped.endswith(":"):
            current_section = stripped[:-1]
            result[current_section] = {}
        elif current_section and ":" in stripped:
            key, _, val = stripped.partition(":")
            val = val.strip()
            # Try numeric conversion
            try:
                val = int(val)
            except ValueError:
                try:
                    val = float(val)
                except ValueError:
                    pass
            result[current_section][key.strip()] = val
    return result


def _load_gate_config() -> dict:
    """Load quality gate thresholds from .roam-gates.yml or use defaults."""
    defaults = {"health_min": 60}
    from pathlib import Path

    config_path = Path(".roam-gates.yml")
    if not config_path.exists():
        return defaults
    try:
        text = config_path.read_text(encoding="utf-8")
        try:
            import yaml

            data = yaml.safe_load(text)
        except ImportError:
            data = _parse_simple_yaml(text)
        if data and "health" in data:
            defaults.update(data["health"])
        return defaults
    except Exception:
        return defaults


# ---------------------------------------------------------------------------
# Baseline-diff mode helpers
# ---------------------------------------------------------------------------

# Per-category metric definitions used for delta synthesis. Each entry maps
# the snapshot column to a finding "kind" + a default severity + the polarity
# (lower_is_better=True means an INCREASE is a regression). ``health_score``
# is the only inverted metric (higher is better).
_BASELINE_METRICS = (
    # (snapshot_col, kind, severity, lower_is_better)
    ("cycles", "cycle", "WARNING", True),
    ("god_components", "god_component", "WARNING", True),
    ("bottlenecks", "bottleneck", "WARNING", True),
    ("dead_exports", "dead_export", "INFO", True),
    ("layer_violations", "layer_violation", "WARNING", True),
)


def _resolve_main_branch(root: Path) -> str:
    """Detect the local main branch (main or master) for ``--baseline auto``."""
    for branch in ("main", "master"):
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--verify", branch],
                cwd=str(root),
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return branch
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return "main"


def _find_baseline_snapshot(conn, ref: str) -> dict | None:
    """Look up a baseline snapshot for the given ref.

    Returns a row dict on success, ``None`` if nothing matched.
    Refs are resolved as follows:

    * ``last`` — most recent snapshot regardless of ref.
    * ``auto`` — most recent snapshot whose ``git_branch`` equals the local
      main/master branch.
    * anything else — most recent snapshot whose ``git_branch`` equals
      ``ref`` OR whose ``git_commit`` starts with ``ref``.
    """
    if ref == "last":
        row = conn.execute("SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    if ref == "auto":
        from roam.db.connection import find_project_root

        try:
            root = find_project_root()
        except Exception:
            root = Path(".")
        branch = _resolve_main_branch(root)
        row = conn.execute(
            "SELECT * FROM snapshots WHERE git_branch = ? ORDER BY timestamp DESC LIMIT 1",
            (branch,),
        ).fetchone()
        return dict(row) if row else None

    # Treat ref as either a branch name or a commit prefix.
    row = conn.execute(
        "SELECT * FROM snapshots WHERE git_branch = ? ORDER BY timestamp DESC LIMIT 1",
        (ref,),
    ).fetchone()
    if row:
        return dict(row)

    row = conn.execute(
        "SELECT * FROM snapshots WHERE git_commit LIKE ? ORDER BY timestamp DESC LIMIT 1",
        (f"{ref}%",),
    ).fetchone()
    return dict(row) if row else None


def _format_baseline_timestamp(ts: int | None) -> str | None:
    """Render a unix timestamp as ISO 8601 UTC, or ``None`` if missing."""
    if not ts:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(microsecond=0)
        return dt.isoformat().replace("+00:00", "Z")
    except (ValueError, OSError, OverflowError):
        return None


def _compute_baseline_delta(current: dict, baseline: dict) -> dict:
    """Compute new / fixed / regressed findings + per-metric score deltas.

    The snapshots table only stores aggregate counts, not per-finding rows,
    so each "finding" in the delta arrays is a synthetic per-category entry
    of shape ``{kind, target, severity, was, now}``. ``new_findings`` is
    emitted when the current count is strictly higher than the baseline,
    ``fixed_findings`` when strictly lower, and ``regressed`` when the
    composite ``health_score`` (or any tracked count) moved in the bad
    direction.
    """
    new_findings: list[dict] = []
    fixed_findings: list[dict] = []
    regressed: list[dict] = []
    score_delta: dict[str, float] = {}

    for col, kind, severity, lower_is_better in _BASELINE_METRICS:
        was = baseline.get(col) or 0
        now = current.get(col) or 0
        delta = now - was
        score_delta[col] = delta
        if delta == 0:
            continue
        # Severity bumps to CRITICAL when the swing is large in the bad direction.
        worsened = (delta > 0) if lower_is_better else (delta < 0)
        improved = not worsened
        finding = {
            "kind": kind,
            "target": col,
            "severity": severity,
            "was": was,
            "now": now,
        }
        if worsened:
            # If the metric grew by more than 50% (and at least 2 absolute),
            # promote to CRITICAL — that's the "high-severity new finding"
            # signal the verdict logic looks for.
            if abs(delta) >= 2 and (was == 0 or abs(delta) / max(was, 1) >= 0.5):
                finding["severity"] = "CRITICAL"
            new_findings.append(finding)
            regressed.append(finding)
        elif improved:
            fixed_findings.append(finding)

    # Composite health_score handled separately (higher = better).
    cur_score = current.get("health_score") or 0
    base_score = baseline.get("health_score") or 0
    score_diff = cur_score - base_score
    score_delta["health_score"] = score_diff
    if score_diff < 0:
        regressed.append(
            {
                "kind": "health_score",
                "target": "health_score",
                "severity": "CRITICAL" if abs(score_diff) >= 10 else "WARNING",
                "was": base_score,
                "now": cur_score,
            }
        )

    return {
        "new_findings": new_findings,
        "fixed_findings": fixed_findings,
        "regressed": regressed,
        "score_delta": score_delta,
    }


def _baseline_verdict(delta: dict) -> str:
    """Apply the documented verdict policy to a delta block.

    * ``BAD``    — composite health_score regressed against baseline.
    * ``REVIEW`` — at least one new CRITICAL finding (even if score held).
    * ``OK``     — otherwise.
    """
    score_diff = delta["score_delta"].get("health_score", 0)
    if score_diff < 0:
        return "BAD"
    for f in delta["new_findings"]:
        if f.get("severity") == "CRITICAL":
            return "REVIEW"
    return "OK"


@click.command()
@click.option(
    "--no-framework",
    is_flag=True,
    help="Filter out framework/boilerplate symbols from god components and bottlenecks",
)
@click.option("--gate", is_flag=True, help="Run quality gate checks (exit 5 on failure)")
@click.option(
    "--explain",
    is_flag=True,
    help="Show how the 0-100 score decomposes into category contributions.",
)
@click.option(
    "--baseline",
    "baseline_ref",
    default=None,
    metavar="REF",
    help=(
        "Compare against a stored baseline snapshot and report deltas instead of "
        "the absolute set. REF can be a git ref (e.g. 'main', a tag, a SHA), "
        "'last' for the most recent snapshot regardless of ref, or 'auto' for "
        "the most recent snapshot taken on the main branch. "
        "Run `roam trends --save` regularly (or in CI) to populate baseline snapshots."
    ),
)
@click.pass_context
def health(ctx, no_framework, gate, explain, baseline_ref):
    """Show code health: cycles, god components, bottlenecks."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    detail = ctx.obj.get("detail", False) if ctx.obj else False
    ensure_index()
    with open_db(readonly=True) as conn:
        G = build_symbol_graph(conn)

        # --- Cycles ---
        cycles = find_cycles(G)
        formatted_cycles = format_cycles(cycles, conn) if cycles else []
        mark_actionable_cycles(formatted_cycles)

        raw_by_formatted_cycle = list(zip(cycles, formatted_cycles))

        # --- Cycle break suggestions ---
        break_suggestions: list[dict] = []
        for scc, cyc_info in raw_by_formatted_cycle:
            if not cyc_info.get("actionable"):
                continue
            if len(scc) < 3:
                continue
            result = find_weakest_edge(G, scc)
            if result is None:
                continue
            src_id, tgt_id, reason = result
            src_name = G.nodes[src_id].get("name", "?") if src_id in G else "?"
            tgt_name = G.nodes[tgt_id].get("name", "?") if tgt_id in G else "?"
            break_suggestions.append(
                {
                    "source_id": src_id,
                    "target_id": tgt_id,
                    "source_name": src_name,
                    "target_name": tgt_name,
                    "reason": reason,
                    "scc_size": len(scc),
                }
            )

        # --- God components ---
        degree_rows = conn.execute(TOP_BY_DEGREE, (50,)).fetchall()
        god_items = []
        for r in degree_rows:
            total = (r["in_degree"] or 0) + (r["out_degree"] or 0)
            if total > 20:
                god_items.append(
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "degree": total,
                        "file": r["file_path"],
                    }
                )

        # --- Bottlenecks (percentile-based severity) ---
        # Fetch all non-zero betweenness values to compute percentile thresholds.
        # Raw betweenness is unnormalized (shortest-path counts), so absolute
        # thresholds don't scale across codebase sizes. Percentiles do.
        all_bw = sorted(
            r[0] for r in conn.execute("SELECT betweenness FROM graph_metrics WHERE betweenness > 0").fetchall()
        )
        bn_p70 = _percentile(all_bw, 70)
        bn_p90 = _percentile(all_bw, 90)

        bw_rows = conn.execute(TOP_BY_BETWEENNESS, (15,)).fetchall()
        bn_items = []
        for r in bw_rows:
            bw = r["betweenness"] or 0
            if bw > 0.5:
                bn_items.append(
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "betweenness": round(bw, 1),
                        "file": r["file_path"],
                    }
                )

        # --- Framework filtering ---
        filtered_count = 0
        if no_framework:
            before = len(god_items) + len(bn_items)
            god_items = [g for g in god_items if g["name"] not in _FRAMEWORK_NAMES]
            bn_items = [b for b in bn_items if b["name"] not in _FRAMEWORK_NAMES]
            filtered_count = before - len(god_items) - len(bn_items)

        # --- Layer violations ---
        layer_map = detect_layers(G)
        violations = find_violations(G, layer_map) if layer_map else []
        v_lookup = {}
        if violations:
            all_ids = {v["source"] for v in violations} | {v["target"] for v in violations}
            for r in batched_in(
                conn,
                "SELECT s.id, s.name, f.path as file_path "
                "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.id IN ({ph})",
                list(all_ids),
            ):
                v_lookup[r["id"]] = r

        # ---- Classify issue severity (location-aware) ----
        sev_counts = {"CRITICAL": 0, "WARNING": 0, "INFO": 0}

        # Cycle severity: directory-aware, but local/test-involved SCCs are
        # informational and excluded from health scoring. They commonly come
        # from Vue <script setup> local symbol references or test helpers with
        # duplicate names; neither is an architectural cycle.
        for cyc in formatted_cycles:
            dirs = _unique_dirs(cyc["files"])
            cyc["directories"] = len(dirs)
            if not cyc["actionable"]:
                cyc["severity"] = "INFO"
            elif len(cyc["files"]) > 3:
                cyc["severity"] = "CRITICAL"
            else:
                cyc["severity"] = "WARNING"
            sev_counts[cyc["severity"]] += 1

        actionable_cycles = [c for c in formatted_cycles if c.get("actionable")]
        ignored_cycles = [c for c in formatted_cycles if not c.get("actionable")]

        # God component severity: location-aware thresholds
        actionable_count = 0
        utility_count = 0
        for g in god_items:
            is_util = _is_utility_path(g["file"])
            g["category"] = "utility" if is_util else "actionable"
            if is_util:
                utility_count += 1
                # Relaxed thresholds for utilities (3x)
                if g["degree"] > 150:
                    g["severity"] = "CRITICAL"
                elif g["degree"] > 90:
                    g["severity"] = "WARNING"
                else:
                    g["severity"] = "INFO"
            else:
                actionable_count += 1
                # Standard thresholds for non-utility code
                if g["degree"] > 50:
                    g["severity"] = "CRITICAL"
                elif g["degree"] > 30:
                    g["severity"] = "WARNING"
                else:
                    g["severity"] = "INFO"
            sev_counts[g["severity"]] += 1

        # Sort: actionable first, then utilities; within each group by degree desc
        god_items.sort(
            key=lambda g: (
                0 if g["category"] == "actionable" else 1,
                -g["degree"],
            )
        )

        # Bottleneck severity: percentile-based thresholds.
        # Utilities get 1.5x multiplied thresholds (higher bar for severity).
        _BN_UTIL_MULT = 1.5
        bn_actionable = 0
        bn_utility = 0
        for b in bn_items:
            is_util = _is_utility_path(b["file"])
            b["category"] = "utility" if is_util else "actionable"
            mult = _BN_UTIL_MULT if is_util else 1.0
            if is_util:
                bn_utility += 1
            else:
                bn_actionable += 1
            if b["betweenness"] > bn_p90 * mult:
                b["severity"] = "CRITICAL"
            elif b["betweenness"] > bn_p70 * mult:
                b["severity"] = "WARNING"
            else:
                b["severity"] = "INFO"
            sev_counts[b["severity"]] += 1

        # Sort: actionable first, then utilities; within each group by betweenness desc
        bn_items.sort(
            key=lambda b: (
                0 if b["category"] == "actionable" else 1,
                -b["betweenness"],
            )
        )

        for v in violations:
            v["severity"] = "WARNING"
            sev_counts["WARNING"] += 1

        # --- Tangle ratio ---
        total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] or 1
        cycle_symbol_ids = set()
        for scc, cyc_info in raw_by_formatted_cycle:
            if cyc_info.get("actionable"):
                cycle_symbol_ids.update(scc)
        tangle_ratio = round(len(cycle_symbol_ids) / total_symbols * 100, 1)

        # --- Propagation Cost (MacCormack et al. 2006) ---
        # Fraction of the system affected by a change to any component.
        # Uses transitive closure: PC = sum(V) / n^2
        prop_cost = propagation_cost(G)

        # --- Algebraic Connectivity (Fiedler 1973) ---
        # Second-smallest Laplacian eigenvalue; low = fragile architecture
        fiedler = algebraic_connectivity(G)

        # --- Composite health score (0-100) ---
        # Weighted geometric mean: score = 100 * product(h_i ^ w_i)
        # Non-compensatory: a zero in any dimension cannot be masked by
        # high scores in others, unlike a linear sum.  Each factor h_i
        # is a "health fraction" in (0, 1] derived from a sigmoid:
        #   h = e^(-signal / scale)   (1 = pristine, → 0 = worst)
        # Weights sum to 1 and encode relative importance.
        def _health_factor(value, scale):
            """Sigmoid health factor: 1 for no issues, → 0 for many."""
            return math.exp(-value / scale) if scale > 0 else 1.0

        # Score signals — count *actionable* items only. Utilities
        # (string/path/datetime helpers) are expected to have high fan-in
        # and would dominate the formula otherwise. Per dogfood notes
        # 2026-05-01: this repo had 50 god components total but 27 were
        # expected utilities; the old formula penalised the score for
        # all 50 and produced a misleading 2/100 verdict. The display
        # already classifies them ("23 actionable, 27 expected utilities");
        # the score should match the display.
        god_actionable = [g for g in god_items if g.get("category") == "actionable"]
        god_critical = sum(1 for g in god_actionable if g.get("severity") == "CRITICAL")
        # Normalise by codebase size so a 14k-symbol repo with 23 actionable
        # god components (0.16%) doesn't score the same as a 100-symbol
        # repo with 23 (23%). 1k symbols is the unit; signal scales linearly.
        size_norm = max(1.0, total_symbols / 1000.0)
        god_signal = (god_critical * 3 + len(god_actionable) * 0.5) / size_norm
        bn_actionable_items = [b for b in bn_items if b.get("category") == "actionable"]
        bn_critical = sum(1 for b in bn_actionable_items if b.get("severity") == "CRITICAL")
        bn_signal = (bn_critical * 2 + len(bn_actionable_items) * 0.3) / size_norm

        coverage_import = imported_coverage_overview(conn)

        # Base factors (weights sum to 1.0 before optional imported coverage).
        # Scales tuned post-normalisation so a normal repo (low percent of
        # actionable god components) scores in the 60-90 range.
        base_factors = [
            (_health_factor(tangle_ratio, 10), 0.30),  # tangle ratio
            (_health_factor(god_signal, 1.5), 0.20),  # god components (normalised /1k symbols)
            (_health_factor(bn_signal, 1.0), 0.15),  # bottlenecks (normalised /1k symbols)
            (_health_factor(len(violations), 5), 0.15),  # layer violations
        ]
        # File-level health: map avg [0-10] to a factor
        try:
            avg_file_health = conn.execute(
                "SELECT AVG(health_score) FROM file_stats WHERE health_score IS NOT NULL"
            ).fetchone()[0]
            if avg_file_health is not None:
                base_factors.append((min(1.0, avg_file_health / 10.0), 0.20))
            else:
                base_factors.append((1.0, 0.20))
        except Exception:
            base_factors.append((1.0, 0.20))

        # Imported test coverage (#134): when available, reserve 10% score weight
        # and scale existing weights to 90%. This avoids over-dominance while
        # still penalizing high-centrality codebases with low real coverage.
        if coverage_import.get("coverable_lines", 0) > 0 and coverage_import.get("coverage_pct") is not None:
            cov_factor = min(1.0, max(0.05, coverage_import["coverage_pct"] / 100.0))
            _health_factors = [(h, w * 0.90) for h, w in base_factors]
            _health_factors.append((cov_factor, 0.10))
        else:
            _health_factors = base_factors

        # Weighted geometric mean in log space
        log_score = sum(w * math.log(max(h, 1e-9)) for h, w in _health_factors)
        health_score = max(0, min(100, int(100 * math.exp(log_score))))

        # record per-factor contributions so --explain can show
        # WHY the score is what it is. Each factor's "loss" (1 - h) is
        # what's pulling the score down; the weight scales the impact.
        _factor_names = ["tangle_ratio", "god_components", "bottlenecks", "layer_violations", "file_health"]
        if len(_health_factors) > len(_factor_names):
            _factor_names.append("imported_coverage")
        score_breakdown = []
        for (h, w), name in zip(_health_factors, _factor_names):
            loss_pp = round((1 - h) * w * 100, 1)
            score_breakdown.append(
                {
                    "factor": name,
                    "health": round(h, 3),
                    "weight": round(w, 2),
                    "loss_pp": loss_pp,
                }
            )
        score_breakdown.sort(key=lambda b: b["loss_pp"], reverse=True)

        # — name the dominant issue category. The four
        # category counts (cycles, god_components, bottlenecks,
        # layer_violations) lead the user at a fix; the largest is
        # the highest-leverage next action. Without this hint a user
        # sees "25 critical" and has to dig into the breakdown to
        # know whether to fix cycles first or god components first.
        _cat_counts = {
            "cycles": sum(1 for c in actionable_cycles if c.get("severity") == "CRITICAL"),
            "god_components": sum(1 for g in god_items if g.get("severity") == "CRITICAL"),
            "bottlenecks": sum(1 for b in bn_items if b.get("severity") == "CRITICAL"),
            "layer_violations": sum(1 for v in violations if v.get("severity") == "CRITICAL"),
        }
        _top_category, _top_count = max(_cat_counts.items(), key=lambda x: x[1])
        _focus_hint = f", focus: {_top_category}" if _top_count > 0 else ""
        # when 0 actionable items remain (everything was
        # ignored by category or framework filter), the verdict should say
        # so explicitly. Otherwise users see "29 critical issues" but the
        # next line says "0 actionable" — confusing.
        if actionable_count == 0 and sev_counts["CRITICAL"] > 0:
            _focus_hint = " (all flagged as utility / non-actionable)"

        # --- Verdict ---
        if health_score >= 80:
            verdict = f"Healthy codebase ({health_score}/100) — {sev_counts['CRITICAL']} critical issues{_focus_hint}"
        elif health_score >= 60:
            verdict = (
                f"Fair codebase ({health_score}/100) — "
                f"{sev_counts['CRITICAL']} critical, {sev_counts['WARNING']} warnings{_focus_hint}"
            )
        elif health_score >= 40:
            verdict = (
                f"Needs attention ({health_score}/100) — "
                f"{sev_counts['CRITICAL']} critical, {sev_counts['WARNING']} warnings{_focus_hint}"
            )
        else:
            verdict = (
                f"Unhealthy codebase ({health_score}/100) — "
                f"{sev_counts['CRITICAL']} critical, {sev_counts['WARNING']} warnings{_focus_hint}"
            )

        # --- Baseline-diff mode ---
        # When --baseline is set, swap the absolute-findings output for a
        # delta report against a stored snapshot. Runs and exits before
        # gate/sarif/json/text branches so existing behaviour is untouched
        # when the flag is absent.
        if baseline_ref:
            # Dead-export count: mirror the query metrics_history uses so the
            # current vs. baseline comparison is apples-to-apples. Tests that
            # don't care about exports are unaffected by 0-vs-0 deltas.
            from roam.db.queries import UNREFERENCED_EXPORTS as _UNREF_EXPORTS

            try:
                _dead_rows = conn.execute(_UNREF_EXPORTS).fetchall()
                _dead_exports = sum(
                    1
                    for r in _dead_rows
                    if not (r["file_path"] or "").lower().rsplit("/", 1)[-1].startswith("test_")
                    and not (r["file_path"] or "").lower().endswith("_test.py")
                )
            except Exception:
                _dead_exports = 0

            current_metrics = {
                "health_score": health_score,
                "cycles": len(actionable_cycles),
                "god_components": len(god_items),
                "bottlenecks": len(bn_items),
                "dead_exports": _dead_exports,
                "layer_violations": len(violations),
            }

            baseline = _find_baseline_snapshot(conn, baseline_ref)

            if baseline is None:
                degraded_msg = (
                    f"No baseline snapshot found for ref `{baseline_ref}`. "
                    "Run `roam trends --save` first, or use `--baseline last`."
                )
                if json_mode:
                    envelope = json_envelope(
                        "health",
                        budget=token_budget,
                        summary={
                            "verdict": "DEGRADED",
                            "reason": "no_baseline_snapshot",
                            "baseline_ref": baseline_ref,
                            "health_score": health_score,
                        },
                        baseline_ref=baseline_ref,
                        message=degraded_msg,
                    )
                    click.echo(to_json(envelope))
                    return
                click.echo(f"VERDICT: DEGRADED — {degraded_msg}")
                return

            delta = _compute_baseline_delta(current_metrics, baseline)
            baseline_verdict = _baseline_verdict(delta)
            baseline_taken_at = _format_baseline_timestamp(baseline.get("timestamp"))
            new_count = len(delta["new_findings"])
            fixed_count = len(delta["fixed_findings"])
            regressed_count = len(delta["regressed"])
            delta_block = {
                "new_findings": delta["new_findings"],
                "fixed_findings": delta["fixed_findings"],
                "regressed": delta["regressed"],
                "score_delta": delta["score_delta"],
                "baseline_ref": baseline_ref,
                "baseline_taken_at": baseline_taken_at,
                "baseline_git_branch": baseline.get("git_branch"),
                "baseline_git_commit": baseline.get("git_commit"),
            }

            if json_mode:
                envelope = json_envelope(
                    "health",
                    budget=token_budget,
                    summary={
                        "verdict": baseline_verdict,
                        "baseline_ref": baseline_ref,
                        "baseline_taken_at": baseline_taken_at,
                        "new_findings_count": new_count,
                        "fixed_findings_count": fixed_count,
                        "regressed_count": regressed_count,
                        "health_score": health_score,
                        "score_delta": delta["score_delta"],
                    },
                    delta=delta_block,
                    health_score=health_score,
                )
                click.echo(to_json(envelope))
                return

            # Text output for baseline mode.
            click.echo(f"VERDICT: {baseline_verdict} (baseline: {baseline_ref})\n")
            click.echo(
                "Δ +{new} findings, {fixed} fixed, {regressed} regressed".format(
                    new=new_count, fixed=fixed_count, regressed=regressed_count
                )
            )
            score_diff = delta["score_delta"].get("health_score", 0)
            score_sign = "+" if score_diff > 0 else ""
            base_score = baseline.get("health_score") or 0
            click.echo(
                f"Score: {base_score} -> {health_score} ({score_sign}{score_diff})"
                f"   Baseline taken: {baseline_taken_at or '(unknown)'}"
            )
            if delta["new_findings"]:
                click.echo("\nNew findings:")
                for f in delta["new_findings"][:10]:
                    click.echo(
                        f"  [{f['severity']}] +{f['now'] - f['was']} {f['kind']} "
                        f"(was {f['was']}, now {f['now']})"
                    )
                if len(delta["new_findings"]) > 10:
                    click.echo(f"  (+{len(delta['new_findings']) - 10} more)")
            if delta["regressed"]:
                # Avoid double-listing items already shown under "new_findings"
                # — only score regressions are exclusive to this section.
                score_regressions = [r for r in delta["regressed"] if r["kind"] == "health_score"]
                if score_regressions:
                    click.echo("\nRegressed:")
                    for r in score_regressions:
                        click.echo(
                            f"  [{r['severity']}] {r['kind']}: {r['was']} -> {r['now']}"
                        )
            if not delta["new_findings"] and not delta["regressed"]:
                click.echo("\nNo regressions detected.")
            return

        # --- Quality Gate ---
        if gate:
            gate_config = _load_gate_config()
            gate_results = []
            all_passed = True

            # Health minimum
            h_min = gate_config.get("health_min", 60)
            passed = health_score >= h_min
            gate_results.append({"gate": "health_min", "threshold": h_min, "actual": health_score, "passed": passed})
            if not passed:
                all_passed = False

            # Optional gates
            c_max = gate_config.get("complexity_max")
            if c_max is not None:
                try:
                    max_cc = (
                        conn.execute("SELECT MAX(complexity) FROM symbols WHERE complexity IS NOT NULL").fetchone()[0]
                        or 0
                    )
                except Exception:
                    max_cc = 0
                passed = max_cc <= c_max
                gate_results.append(
                    {
                        "gate": "complexity_max",
                        "threshold": c_max,
                        "actual": max_cc,
                        "passed": passed,
                    }
                )
                if not passed:
                    all_passed = False

            cyc_max = gate_config.get("cycle_max")
            if cyc_max is not None:
                passed = len(actionable_cycles) <= cyc_max
                gate_results.append(
                    {
                        "gate": "cycle_max",
                        "threshold": cyc_max,
                        "actual": len(actionable_cycles),
                        "passed": passed,
                    }
                )
                if not passed:
                    all_passed = False

            t_max = gate_config.get("tangle_max")
            if t_max is not None:
                passed = tangle_ratio <= t_max
                gate_results.append(
                    {
                        "gate": "tangle_max",
                        "threshold": t_max,
                        "actual": tangle_ratio,
                        "passed": passed,
                    }
                )
                if not passed:
                    all_passed = False

            if json_mode:
                envelope = json_envelope(
                    "health",
                    budget=token_budget,
                    summary={
                        "verdict": verdict,
                        "health_score": health_score,
                        "gate_passed": all_passed,
                        "imported_coverage_pct": coverage_import.get("coverage_pct"),
                    },
                    gate_results=gate_results,
                    health_score=health_score,
                    imported_coverage_pct=coverage_import.get("coverage_pct"),
                    imported_coverage_files=coverage_import.get("files_with_coverage", 0),
                )
                click.echo(to_json(envelope))
                if not all_passed:
                    from roam.exit_codes import GateFailureError

                    raise GateFailureError("Quality gate failed")
                return

            # Text output for gate mode
            click.echo(f"VERDICT: {verdict}\n")
            click.echo("=== Quality Gates ===")
            for gr in gate_results:
                status = "PASS" if gr["passed"] else "FAIL"
                click.echo(f"  [{status}] {gr['gate']}: {gr['actual']} (threshold: {gr['threshold']})")

            if all_passed:
                click.echo("\nAll gates passed.")
            else:
                failed = [g["gate"] for g in gate_results if not g["passed"]]
                click.echo(f"\nFailed gates: {', '.join(failed)}")
                from roam.exit_codes import GateFailureError

                raise GateFailureError(f"Quality gate failed: {', '.join(failed)}")
            return

        if sarif_mode:
            from roam.output.sarif import health_to_sarif, write_sarif

            issues = {
                "cycles": [
                    {
                        "size": c["size"],
                        "severity": c.get("severity", "WARNING"),
                        "symbols": [s["name"] for s in c["symbols"]],
                        "files": c["files"],
                    }
                    for c in formatted_cycles
                ],
                "god_components": [
                    {
                        "name": g["name"],
                        "kind": g["kind"],
                        "degree": g["degree"],
                        "file": g["file"],
                        "severity": g.get("severity", "WARNING"),
                    }
                    for g in god_items
                ],
                "bottlenecks": [
                    {
                        "name": b["name"],
                        "kind": b["kind"],
                        "betweenness": b["betweenness"],
                        "file": b["file"],
                        "severity": b.get("severity", "WARNING"),
                    }
                    for b in bn_items
                ],
                "layer_violations": [
                    {
                        "severity": "WARNING",
                        "source": v_lookup.get(v["source"], {}).get("name", "?"),
                        "source_layer": v["source_layer"],
                        "target": v_lookup.get(v["target"], {}).get("name", "?"),
                        "target_layer": v["target_layer"],
                    }
                    for v in violations
                ],
            }
            sarif = health_to_sarif(issues)
            click.echo(write_sarif(sarif))
            return

        if json_mode:
            cycle_severity = _severity_counts(actionable_cycles)
            god_severity = _severity_counts(god_items)
            bottleneck_severity = _severity_counts(bn_items)
            layer_severity = _severity_counts(violations)
            j_issue_count = len(actionable_cycles) + len(god_items) + len(bn_items) + len(violations)
            next_steps = suggest_next_steps(
                "health",
                {
                    "score": health_score,
                    "critical_issues": sev_counts["CRITICAL"],
                    "cycles": len(actionable_cycles),
                },
            )
            envelope = json_envelope(
                "health",
                budget=token_budget,
                summary={
                    "verdict": verdict,
                    "health_score": health_score,
                    "tangle_ratio": tangle_ratio,
                    "propagation_cost": prop_cost,
                    "algebraic_connectivity": fiedler,
                    "issue_count": j_issue_count,
                    "severity": sev_counts,
                    "category_severity": {
                        "cycles": cycle_severity,
                        "god_components": god_severity,
                        "bottlenecks": bottleneck_severity,
                        "layer_violations": layer_severity,
                    },
                    "actionable_cycles": len(actionable_cycles),
                    "ignored_cycles": len(ignored_cycles),
                    "imported_coverage_pct": coverage_import.get("coverage_pct"),
                    "imported_coverage_files": coverage_import.get("files_with_coverage", 0),
                },
                next_steps=next_steps,
                health_score=health_score,
                tangle_ratio=tangle_ratio,
                propagation_cost=prop_cost,
                algebraic_connectivity=fiedler,
                issue_count=j_issue_count,
                severity=sev_counts,
                category_severity={
                    "cycles": cycle_severity,
                    "god_components": god_severity,
                    "bottlenecks": bottleneck_severity,
                    "layer_violations": layer_severity,
                },
                actionable_cycles=len(actionable_cycles),
                ignored_cycles=len(ignored_cycles),
                total_cycles=len(formatted_cycles),
                imported_coverage_pct=coverage_import.get("coverage_pct"),
                imported_coverage_files=coverage_import.get("files_with_coverage", 0),
                imported_covered_lines=coverage_import.get("covered_lines", 0),
                imported_coverable_lines=coverage_import.get("coverable_lines", 0),
                score_breakdown=score_breakdown,
                framework_filtered=filtered_count,
                actionable_count=actionable_count,
                utility_count=utility_count,
                cycles=[
                    {
                        "size": c["size"],
                        "severity": c["severity"],
                        "directories": c["directories"],
                        "symbols": [s["name"] for s in c["symbols"]],
                        "files": c["files"],
                    }
                    for c in formatted_cycles
                ],
                cycle_break_suggestions=[
                    {
                        "source": bs["source_name"],
                        "target": bs["target_name"],
                        "reason": bs["reason"],
                        "scc_size": bs["scc_size"],
                    }
                    for bs in break_suggestions
                ],
                god_components=[{**g, "severity": g["severity"], "category": g["category"]} for g in god_items],
                bottleneck_thresholds={
                    "p70": round(bn_p70, 1),
                    "p90": round(bn_p90, 1),
                    "utility_multiplier": _BN_UTIL_MULT,
                    "population": len(all_bw),
                },
                bottlenecks=[{**b, "severity": b["severity"], "category": b["category"]} for b in bn_items],
                layer_violations=[
                    {
                        "severity": "WARNING",
                        "source": v_lookup.get(v["source"], {}).get("name", "?"),
                        "source_layer": v["source_layer"],
                        "target": v_lookup.get(v["target"], {}).get("name", "?"),
                        "target_layer": v["target_layer"],
                    }
                    for v in violations
                ],
            )
            # Round 4 #20 / U: top-level index_status field so JSON
            # consumers see the staleness warning without scanning
            # nested sections.
            from roam.commands.resolve import index_status as _index_status_json

            _idx_status_json = _index_status_json()
            if _idx_status_json is not None:
                envelope["index_status"] = _idx_status_json
            if not detail:
                envelope = summary_envelope(envelope)
            click.echo(to_json(envelope))
            return

        # --- Text output ---
        # Round 4 #20 / U: surface the staleness warning BEFORE the
        # verdict so an agent reading top-down can't miss it. The
        # health composite leans on git-derived metrics (churn,
        # co-change), so an out-of-date index quietly skews all of them.
        from roam.commands.resolve import index_status as _index_status

        _idx_status = _index_status()
        if _idx_status and not _idx_status.get("fresh"):
            click.echo(f"NOTE: {_idx_status['hint']}\n")
        click.echo(f"VERDICT: {verdict}\n")
        # when --explain, decompose the score before everything
        # else so the user understands which factor is dragging it down.
        if explain:
            click.echo("=== Score Breakdown (sorted by impact) ===")
            click.echo("Factor               Health  Weight  Loss (pp)")
            click.echo("-------------------  ------  ------  ---------")
            for b in score_breakdown:
                click.echo(f"{b['factor']:<19}  {b['health']:>6.3f}  {b['weight']:>6.2f}  {b['loss_pp']:>9.1f}")
            click.echo()
        issue_count = len(actionable_cycles) + len(god_items) + len(bn_items) + len(violations)
        parts = []
        if formatted_cycles:
            cycle_detail = f"{len(actionable_cycles)} actionable cycle{'s' if len(actionable_cycles) != 1 else ''}"
            if ignored_cycles:
                cycle_detail += (
                    f", {len(ignored_cycles)} local/test cycle{'s' if len(ignored_cycles) != 1 else ''} ignored"
                )
            parts.append(cycle_detail)
        if god_items:
            god_detail = f"{len(god_items)} god component{'s' if len(god_items) != 1 else ''}"
            god_detail += f" ({actionable_count} actionable, {utility_count} expected utilities)"
            parts.append(god_detail)
        if bn_items:
            bn_detail = f"{len(bn_items)} bottleneck{'s' if len(bn_items) != 1 else ''}"
            bn_detail += f" ({bn_actionable} actionable, {bn_utility} expected utilities)"
            parts.append(bn_detail)
        if violations:
            parts.append(f"{len(violations)} layer violation{'s' if len(violations) != 1 else ''}")
        click.echo(
            f"Health Score: {health_score}/100  |  "
            f"Tangle: {tangle_ratio}% ({len(cycle_symbol_ids)}/{total_symbols} symbols in cycles)"
        )
        click.echo(f"Propagation Cost: {prop_cost:.1%}  |  Algebraic Connectivity: {fiedler:.4f}")
        if coverage_import.get("coverable_lines", 0) > 0:
            click.echo(
                f"Imported Coverage: {coverage_import['coverage_pct']}% "
                f"({coverage_import['covered_lines']}/{coverage_import['coverable_lines']} lines, "
                f"{coverage_import['files_with_coverage']} files)"
            )
        click.echo()
        if issue_count == 0:
            click.echo("Issues: None detected")
            if ignored_cycles:
                click.echo(f"  ({len(ignored_cycles)} informational local/test cycle(s) ignored for scoring)")
        else:
            sev_parts = []
            if sev_counts["CRITICAL"]:
                sev_parts.append(f"{sev_counts['CRITICAL']} CRITICAL")
            if sev_counts["WARNING"]:
                sev_parts.append(f"{sev_counts['WARNING']} WARNING")
            if sev_counts["INFO"]:
                sev_parts.append(f"{sev_counts['INFO']} INFO")
            click.echo(f"Health: {issue_count} issue{'s' if issue_count != 1 else ''} — {', '.join(sev_parts)}")
            detail_str = ", ".join(parts)
            if filtered_count:
                detail_str += f"; {filtered_count} framework symbols filtered"
            click.echo(f"  ({detail_str})")
            click.echo(
                "  Breakdown: "
                f"cycles [{_format_severity_counts(_severity_counts(actionable_cycles))}], "
                f"god [{_format_severity_counts(_severity_counts(god_items))}], "
                f"bottlenecks [{_format_severity_counts(_severity_counts(bn_items))}], "
                f"layers [{_format_severity_counts(_severity_counts(violations))}]"
            )
        click.echo()

        # --- Summary mode (no --detail): only show top 3 issues ---
        if not detail:
            top_critical = [
                item
                for item_list in [
                    [(c, "cycle") for c in formatted_cycles if c.get("severity") == "CRITICAL"],
                    [(g, "god") for g in god_items if g.get("severity") == "CRITICAL"],
                    [(b, "bottleneck") for b in bn_items if b.get("severity") == "CRITICAL"],
                ]
                for item in item_list
            ]
            if top_critical:
                click.echo("Top CRITICAL issues (run `roam --detail health` for the full breakdown):")
                for item, kind in top_critical[:3]:
                    if kind == "cycle":
                        names = [s["name"] for s in item["symbols"][:3]]
                        click.echo(f"  cycle ({item['size']} symbols): {', '.join(names)}")
                    elif kind == "god":
                        click.echo(
                            f"  god component: {item['name']} ({abbrev_kind(item['kind'])}, degree={item['degree']})"
                        )
                    elif kind == "bottleneck":
                        click.echo(
                            f"  bottleneck: {item['name']} ({abbrev_kind(item['kind'])}, betweenness={item['betweenness']})"
                        )
            else:
                click.echo(
                    "(run `roam --detail health` for the full breakdown of "
                    "cycles, god components, bottlenecks, and layer violations)"
                )
            return

        click.echo("=== Cycles ===")
        if formatted_cycles:
            for i, cyc in enumerate(formatted_cycles, 1):
                names = [s["name"] for s in cyc["symbols"]]
                sev = cyc["severity"]
                dir_note = f", {cyc['directories']} dir{'s' if cyc['directories'] != 1 else ''}"
                click.echo(f"  [{sev}] cycle {i} ({cyc['size']} symbols{dir_note}): {', '.join(names[:10])}")
                if len(names) > 10:
                    click.echo(f"    (+{len(names) - 10} more)")
                click.echo(f"    files: {', '.join(cyc['files'][:5])}")
            click.echo(f"  total: {len(actionable_cycles)} actionable cycle(s), {len(ignored_cycles)} informational")
            if break_suggestions:
                click.echo()
                click.echo("  Cycle break suggestions:")
                for bs in break_suggestions:
                    click.echo(
                        f"    Break: remove dependency {bs['source_name']} -> {bs['target_name']} ({bs['reason']})"
                    )
        else:
            click.echo("  (none)")

        click.echo("\n=== God Components (degree > 20) ===")
        if god_items:
            god_rows = [
                [
                    g["severity"],
                    g["name"],
                    abbrev_kind(g["kind"]),
                    str(g["degree"]),
                    "util" if g["category"] == "utility" else "act",
                    loc(g["file"]),
                ]
                for g in god_items
            ]
            click.echo(format_table(["Sev", "Name", "Kind", "Degree", "Cat", "File"], god_rows, budget=20))
        else:
            click.echo("  (none)")

        click.echo("\n=== Bottlenecks (high betweenness) ===")
        if bn_items:
            bn_rows = []
            for b in bn_items:
                bw_str = f"{b['betweenness']:.0f}" if b["betweenness"] >= 10 else f"{b['betweenness']:.1f}"
                bn_rows.append(
                    [
                        b["severity"],
                        b["name"],
                        abbrev_kind(b["kind"]),
                        bw_str,
                        "util" if b["category"] == "utility" else "act",
                        loc(b["file"]),
                    ]
                )
            click.echo(format_table(["Sev", "Name", "Kind", "Betweenness", "Cat", "File"], bn_rows, budget=15))
        else:
            click.echo("  (none)")

        click.echo(f"\n=== Layer Violations ({len(violations)}) ===")
        if violations:
            v_rows = []
            for v in violations[:20]:
                src = v_lookup.get(v["source"], {})
                tgt = v_lookup.get(v["target"], {})
                v_rows.append(
                    [
                        src.get("name", "?"),
                        f"L{v['source_layer']}",
                        tgt.get("name", "?"),
                        f"L{v['target_layer']}",
                    ]
                )
            click.echo(format_table(["Source", "Layer", "Target", "Layer"], v_rows, budget=20))
            if len(violations) > 20:
                click.echo(f"  (+{len(violations) - 20} more)")
        elif layer_map:
            click.echo("  (none)")
        else:
            click.echo("  (no layers detected)")

        next_steps = suggest_next_steps(
            "health",
            {
                "score": health_score,
                "critical_issues": sev_counts["CRITICAL"],
                "cycles": len(actionable_cycles),
            },
        )
        ns_text = format_next_steps_text(next_steps)
        if ns_text:
            click.echo(ns_text)
