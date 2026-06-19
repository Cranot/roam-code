"""``roam boundary`` — public-by-accident exports + changed-range layer violations.

Emits two closed-enum finding kinds:

* ``public_by_accident`` (severity ``warning``) — a Python symbol whose
  name starts with ``_`` is listed in its module's ``__all__`` (the
  module advertises it as a public export despite the underscore-prefix
  privacy marker). The deterministic AST scan catches the LIE between
  the privacy convention and the export contract.

* ``wrong_direction_import`` (severity ``high``) — within the
  changed-range, an edge that crosses a layer boundary in the WRONG
  direction (source layer < target layer; higher-layer module imported
  from lower-layer module). Layers are derived from
  ``roam.graph.layers.detect_layers`` (longest-path-from-sources on the
  symbol graph). The two-fold scope cut: only edges whose **source** or
  **target** file is in the changed-range are reported, AND the kind is
  PARTIAL by design — without an explicit layer config (per CLAUDE.md
  the project doesn't pin a strict layer DAG today), we surface only
  the unambiguous layer-numbering violations and document the scope
  cut in the verdict.

Persistence follows the canonical mandate: ``emit_finding`` is called
with ``--persist``. Re-running the command upserts via the
deterministic ``finding_id_str`` so the registry stays stable.

CI mode: ``--ci`` exits 5 on any ``wrong_direction_import``;
public-by-accident is warning-only and does NOT trigger the CI gate.

Output formats: text (default), ``--json``, and a private
``--sarif PATH`` flag that writes the SARIF 2.1.0 projection to disk
via :func:`_boundary_to_sarif`. The boundary command emits per-finding
SARIF directly to a path; SARIF is deliberately NOT routed through the
global ``--sarif`` flag (``cli._SARIF_CONSUMERS``) because the
findings have changed-range scope semantics and ``--sarif PATH`` keeps
CI integration explicit per W1295. See W1148 audit memo +
(internal memo) §8 for the
disclosure framework.
"""

from __future__ import annotations

import ast
import json as _json
import sqlite3
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.changed_files import get_changed_files
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.db.findings import (
    CONFIDENCE_STATIC_ANALYSIS,
    CONFIDENCE_STRUCTURAL,
    FindingRecord,
    emit_finding,
    make_finding_id,
)
from roam.graph.builder import build_symbol_graph
from roam.graph.layers import detect_layers
from roam.output.formatter import format_table, json_envelope, loc, to_json

# W1295 — boundary is the first command to ship under the P1.3
# strategy lane. Bump on closed-enum kind changes, evidence shape
# changes, or layer-derivation semantics changes — agents consuming
# ``roam findings list --detector boundary`` rely on this stamp.
_BOUNDARY_DETECTOR_VERSION: str = "1.0.0"

# Closed enumeration of boundary finding kinds. Add new kinds to BOTH
# this tuple and ``_KIND_SEVERITY`` to keep the contract single-sourced.
_BOUNDARY_KINDS: tuple[str, ...] = ("public_by_accident", "wrong_direction_import")

_KIND_SEVERITY: dict[str, str] = {
    "public_by_accident": "warning",
    "wrong_direction_import": "high",
}


# ---------------------------------------------------------------------------
# Kind A: public-by-accident exports
# ---------------------------------------------------------------------------


def _extract_all_exports(source: str) -> list[tuple[str, int]] | None:
    """Return ``[(name, line), ...]`` for every literal in ``__all__``.

    Returns ``None`` when the module has no ``__all__`` assignment OR
    when the source can't be parsed (SyntaxError). The AST walk only
    accepts ``__all__ = [...]`` / ``__all__ = (...)`` shapes — the
    common case across the indexed Python corpus.

    Non-literal entries (e.g. ``__all__ = list(_PUBLIC.keys())``) are
    silently skipped; a dynamic ``__all__`` is genuinely ambiguous and
    a heuristic guess would re-introduce the LIE this detector exists
    to flag.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            continue
        value = node.value
        if not isinstance(value, (ast.List, ast.Tuple)):
            return None
        entries: list[tuple[str, int]] = []
        for elt in value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                entries.append((elt.value, elt.lineno))
        return entries
    return None


def _scan_public_by_accident(
    conn: sqlite3.Connection,
    project_root: Path,
) -> list[dict]:
    """Walk every indexed Python file for ``__all__`` entries beginning with ``_``.

    A leading underscore is the canonical Python privacy marker; including
    such a name in ``__all__`` is contradictory — the module advertises
    the symbol as a public export while the name itself says "private".
    Either the underscore prefix should be dropped (the symbol is genuinely
    public) or the entry should be removed from ``__all__`` (the symbol
    is genuinely private). Either way, the current state is wrong.
    """
    findings: list[dict] = []
    rows = conn.execute("SELECT path FROM files WHERE language = 'python' ORDER BY path").fetchall()
    for r in rows:
        rel_path = r[0]
        abs_path = project_root / rel_path
        if not abs_path.is_file():
            continue
        try:
            source = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue
        entries = _extract_all_exports(source)
        if not entries:
            continue
        for name, line in entries:
            if name.startswith("_") and not name.startswith("__"):
                findings.append(
                    {
                        "file": rel_path.replace("\\", "/"),
                        "line": line,
                        "kind": "public_by_accident",
                        "severity": "warning",
                        "evidence": {
                            "exported_name": name,
                            "reason": (
                                "underscore-prefixed name in __all__ — drop the "
                                "underscore (genuinely public) or remove from __all__ "
                                "(genuinely private)"
                            ),
                        },
                        "layer_from": None,
                        "layer_to": None,
                    }
                )
    return findings


# ---------------------------------------------------------------------------
# Kind B: changed-range layer violations
# ---------------------------------------------------------------------------


def _scan_wrong_direction_imports(
    conn: sqlite3.Connection,
    changed_files: set[str],
) -> list[dict]:
    """Edges that reach up the layer stack — foundation depending on caller.

    Numbering convention (``roam.graph.layers.detect_layers``): layer 0 =
    nodes with no incoming edges, i.e. ROOTS of the dependency graph
    (entry points, CLI verbs, top-level handlers). Deeper layers contain
    callees (utilities, base classes, helpers). The HEALTHY direction is
    layer 0 → layer N (callers depend on callees). The WRONG direction is
    layer N → layer 0 — foundation reaching back up into its callers,
    e.g. ``db/`` importing from ``commands/``. This matches the polarity
    of ``roam.graph.layers.find_violations`` (``src_layer > tgt_layer``).

    Scope cuts — this is the PARTIAL kind by design:

    * Only edges whose SOURCE file is in the changed-range surface
      (per W1295 strategy memo — keep PR-time signal local).
    * Both endpoints must resolve to a project file with a known layer.
      Edges that resolve into a property/method whose name happens to
      match a stdlib symbol (e.g. ``path``) typically produce a single
      cross-graph supernode at the deepest layer — those would dominate
      the report. Require a non-trivial layer jump (>=2) to filter out
      the near-trivial property-name collisions while still surfacing
      genuine `db/ -> commands/` shaped violations.
    * Without an explicit layer config the project doesn't pin a strict
      layer DAG (CLAUDE.md); the layer numbering is derived. We surface
      only unambiguous violations and disclose the scope cut in the
      verdict (``partial_success: true`` when a clean run lands on the
      changed-range slice).
    """
    if not changed_files:
        return []

    # Build the symbol graph + layer assignments. Cached per-connection.
    G = build_symbol_graph(conn)
    layers = detect_layers(G)
    if not layers:
        return []

    # Gather symbols by file path so we can scope edges to changed files
    # without iterating every edge in the graph.
    rows = conn.execute("SELECT s.id, f.path AS file_path FROM symbols s JOIN files f ON s.file_id = f.id").fetchall()
    sym_to_file: dict[int, str] = {}
    for sid, fpath in rows:
        sym_to_file[int(sid)] = (fpath or "").replace("\\", "/")

    # The "non-trivial layer jump" threshold filters the stdlib-name
    # collision class (where ``Path`` / ``path`` / similar resolves to
    # a deep-layer property and every project file appears to reach it).
    # A real architectural reach-up still typically spans 2+ layers in
    # the derived numbering.
    _MIN_JUMP = 2

    findings: list[dict] = []
    seen: set[tuple[str, str, int, int]] = set()
    for src, tgt in G.edges:
        src_file = sym_to_file.get(src)
        tgt_file = sym_to_file.get(tgt)
        if src_file is None or tgt_file is None:
            continue
        if src_file not in changed_files:
            continue
        # Both endpoints must be project-internal (resolved to indexed
        # files). Cross-file violations only — skip self-file edges.
        if src_file == tgt_file:
            continue
        src_layer = layers.get(src)
        tgt_layer = layers.get(tgt)
        if src_layer is None or tgt_layer is None:
            continue
        # Wrong direction = SOURCE deeper than TARGET — foundation
        # reaching back up to caller. Matches find_violations() polarity.
        jump = src_layer - tgt_layer
        if jump < _MIN_JUMP:
            continue
        src_data = G.nodes[src]
        tgt_data = G.nodes[tgt]
        src_name = src_data.get("name") or "?"
        tgt_name = tgt_data.get("name") or "?"
        edge_data = G.get_edge_data(src, tgt) or {}
        edge_line = int(edge_data.get("line") or 0)
        # Dedupe by (src_file, tgt_file, src_layer, tgt_layer) so a busy
        # caller importing the same target many times doesn't flood the
        # report with the same architectural violation. The first edge
        # per (file-pair, layer-pair) carries the canonical evidence.
        dedup_key = (src_file, tgt_file, src_layer, tgt_layer)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        findings.append(
            {
                "file": src_file,
                "line": edge_line,
                "kind": "wrong_direction_import",
                "severity": "high",
                "evidence": {
                    "source_symbol": src_name,
                    "target_symbol": tgt_name,
                    "source_file": src_file,
                    "target_file": tgt_file,
                    "edge_kind": edge_data.get("kind") or "calls",
                    "layer_jump": jump,
                    "reason": (
                        f"layer {src_layer} ({src_file}) reaches up to "
                        f"layer {tgt_layer} ({tgt_file}) — "
                        "lower-level module depends on a higher-level caller"
                    ),
                },
                "layer_from": src_layer,
                "layer_to": tgt_layer,
            }
        )
    # Sort: largest layer-jump first, then by file for determinism.
    findings.sort(
        key=lambda f: (
            -(int(f["layer_from"] or 0) - int(f["layer_to"] or 0)),
            f["file"],
            f["line"],
        )
    )
    return findings


# ---------------------------------------------------------------------------
# Findings registry emit
# ---------------------------------------------------------------------------


def _boundary_finding_id(f: dict) -> str:
    """Deterministic ``boundary:<kind>:<digest12>`` id for one finding.

    Re-running the detector on the same input upserts the row via
    ``emit_finding`` rather than duplicating it. The tuple folds in
    every field that disambiguates one finding from another within a
    kind so two distinct violations never collide on a digest.
    """
    kind = f.get("kind") or ""
    file_path = f.get("file") or ""
    line = int(f.get("line") or 0)
    if kind == "public_by_accident":
        ev = f.get("evidence") or {}
        subject = str(ev.get("exported_name") or "")
        return make_finding_id("boundary", kind, kind, file_path, subject, line)
    # wrong_direction_import
    ev = f.get("evidence") or {}
    subject = f"{ev.get('source_symbol', '?')}->{ev.get('target_symbol', '?')}"
    return make_finding_id(
        "boundary",
        kind,
        kind,
        file_path,
        subject,
        int(f.get("layer_from") or 0),
        int(f.get("layer_to") or 0),
        line,
    )


def _resolve_file_subject_id(conn: sqlite3.Connection, file_path: str) -> int | None:
    """Best-effort ``files.id`` lookup for the file carrying the finding."""
    try:
        row = conn.execute("SELECT id FROM files WHERE path = ? LIMIT 1", (file_path,)).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.OperationalError:
        return None


def _emit_boundary_findings(
    conn: sqlite3.Connection,
    findings: list[dict],
    source_version: str,
) -> int:
    """Mirror each boundary finding into the central findings registry.

    Returns the count of rows written. Confidence tiers follow the
    CLAUDE.md vocabulary:

    * ``public_by_accident`` → ``static_analysis`` (AST parse +
      ``__all__`` membership test — deterministic, no name heuristic).
    * ``wrong_direction_import`` → ``structural`` (graph traversal +
      layer-numbering — uses the edge topology, not pattern matching).

    Caller is responsible for opening ``conn`` writable; emit_finding
    does not commit (caller commits once at the end of the persist
    branch).
    """
    written = 0
    for f in findings:
        kind = f.get("kind") or ""
        if kind not in _BOUNDARY_KINDS:
            continue
        file_path = f.get("file") or ""
        line = int(f.get("line") or 0)
        subject_id = _resolve_file_subject_id(conn, file_path)
        finding_id = _boundary_finding_id(f)
        evidence = dict(f.get("evidence") or {})
        evidence.update(
            {
                "kind": kind,
                "file": file_path,
                "line": line,
                "layer_from": f.get("layer_from"),
                "layer_to": f.get("layer_to"),
            }
        )
        if kind == "public_by_accident":
            confidence = CONFIDENCE_STATIC_ANALYSIS
            claim = (
                f"boundary ({kind}): {file_path}:{line} — "
                f"underscore-prefixed name {evidence.get('exported_name')!r} "
                f"in __all__"
            )
        else:
            confidence = CONFIDENCE_STRUCTURAL
            claim = (
                f"boundary ({kind}): {file_path}:{line} — "
                f"layer {f.get('layer_from')} -> {f.get('layer_to')} "
                f"({evidence.get('source_symbol')} -> {evidence.get('target_symbol')})"
            )
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="file" if subject_id is not None else "module",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=confidence,
                source_detector="boundary",
                source_version=source_version,
            ),
        )
        written += 1
    return written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@roam_capability(
    name="boundary",
    category="quality",
    summary=("Surface public-by-accident exports + changed-range layer violations"),
    maturity="experimental",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("boundary")
@click.option(
    "--changed-range",
    type=click.Choice(["pr", "working", "staged", "head", "all"], case_sensitive=False),
    default="working",
    show_default=True,
    help=("Diff source for the wrong_direction_import scope. 'all' scans every indexed file (slow on large repos)."),
)
@click.option("--base-ref", default="main", show_default=True, help="Base branch for --changed-range pr.")
@click.option(
    "--ci",
    is_flag=True,
    default=False,
    help="Exit 5 on any wrong_direction_import (gate CI).",
)
@click.option(
    "--sarif",
    "sarif_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write SARIF 2.1.0 output to <PATH> (in addition to text/json).",  # W1117-followup
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each boundary finding into the central findings registry (``roam findings list --detector boundary``)."
    ),
)
@click.pass_context
def boundary(ctx, changed_range, base_ref, ci, sarif_path, persist) -> None:
    """Surface public-by-accident exports + changed-range layer violations.

    Two finding kinds (closed enum):

    \b
    * public_by_accident      (severity warning) — _name in __all__
    * wrong_direction_import  (severity high)    — layer N → layer M edge, N<M

    ``--ci`` exits 5 when any wrong_direction_import is detected.
    public_by_accident is warning-only and never triggers the CI gate.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()
    project_root = find_project_root()

    # --- Resolve changed-range ---
    # ``all`` widens the scope to every indexed file. The other four
    # values delegate to ``get_changed_files``.
    changed_files: set[str] = set()
    cr = changed_range.lower()
    with open_db(readonly=not persist) as conn:
        if cr == "all":
            rows = conn.execute("SELECT path FROM files WHERE language = 'python'").fetchall()
            changed_files = {(r[0] or "").replace("\\", "/") for r in rows}
        else:
            if cr == "pr":
                paths = get_changed_files(project_root, pr=True, base_ref=base_ref)
            elif cr == "staged":
                paths = get_changed_files(project_root, staged=True)
            elif cr == "head":
                paths = get_changed_files(project_root, commit_range="HEAD~1..HEAD")
            else:
                paths = get_changed_files(project_root)
            changed_files = {p.replace("\\", "/") for p in paths}

        # --- Kind A: public_by_accident (always scans full corpus) ---
        public_findings = _scan_public_by_accident(conn, project_root)

        # --- Kind B: wrong_direction_import (scoped to changed_files) ---
        wrong_findings = _scan_wrong_direction_imports(conn, changed_files)

        all_findings = public_findings + wrong_findings

        # --- Persist into the central findings registry. -------------------
        # Runs ONLY with --persist. The registry mirrors every finding the
        # detector surfaced regardless of CI/JSON display filters so
        # ``roam findings list --detector boundary`` stays comprehensive.
        if persist:
            try:
                _emit_boundary_findings(conn, all_findings, source_version=_BOUNDARY_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError as _exc:
                # Expected: pre-W89 schema (no findings table) — degrade
                # gracefully. Surface lineage so a non-expected variant
                # (locked / corrupt DB) is still discoverable.
                from roam.observability import log_swallowed

                log_swallowed("cmd_boundary:emit_findings", _exc)

        # W805: empty-corpus / no-imports disclosure (Pattern 2 silent-fallback fix).
        # Count import-edges to decide whether 0-findings means "really
        # clean" or "no imports to analyze". If the symbols table has 0
        # imports of the relevant kinds, the boundary check did not have
        # any analyzable input — surface that explicitly via partial_success
        # + a state flag. Mirrors W834 / W836. Runs inside the with-block
        # so ``conn`` is still open.
        try:
            _import_edges = (
                conn.execute("SELECT COUNT(*) FROM edges WHERE kind IN ('imports', 'import')").fetchone()[0] or 0
            )
        except sqlite3.OperationalError:
            _import_edges = -1  # unknown — schema may differ

    n_public = sum(1 for f in all_findings if f["kind"] == "public_by_accident")
    n_wrong = sum(1 for f in all_findings if f["kind"] == "wrong_direction_import")
    total = len(all_findings)
    empty_corpus = total == 0 and _import_edges == 0

    # Existing partial_success semantic (scope-clean run) preserved; the
    # empty-corpus case is a strictly stronger partial_success signal.
    partial_success = (n_wrong == 0 and total > 0 and cr != "all") or empty_corpus

    # PARTIAL scope disclosure — the wrong-direction kind is scoped to
    # the changed-range (W1295 strategy memo) AND the layer-numbering
    # is derived (not config-pinned) per CLAUDE.md. Surface both cuts
    # in the verdict so agents don't mistake a clean run for full
    # coverage.
    if empty_corpus:
        verdict = "no imports to analyze (corpus has 0 import edges — run `roam index --force` to populate)"
    elif total == 0:
        verdict = f"0 boundary findings (scope: {cr})"
    else:
        verdict = (
            f"{total} boundary findings — "
            f"{n_public} public-by-accident exports, "
            f"{n_wrong} wrong-direction imports (scope: {cr})"
        )

    # Build LAW-4-anchored facts. Anchor terminals: findings / exports
    # is not in the anchor set (verified via tests/test_law4_lint.py);
    # use ``imports`` / ``findings`` / ``violations`` instead.
    facts = [verdict]
    if n_public:
        facts.append(f"{n_public} public-by-accident findings")
    if n_wrong:
        facts.append(f"{n_wrong} wrong-direction imports")

    # --- SARIF output (writes to --sarif PATH, never stdout) ---
    if sarif_path:
        try:
            from roam.output.sarif import write_sarif

            sarif_doc = _boundary_to_sarif(all_findings)
            Path(sarif_path).write_text(write_sarif(sarif_doc), encoding="utf-8")
        except ImportError:
            # SARIF helpers absent — degrade gracefully; the JSON/text
            # paths still ship the findings.
            pass

    # --- JSON output ---
    if json_mode:
        _summary = {
            "verdict": verdict,
            "total": total,
            "public_by_accident": n_public,
            "wrong_direction_import": n_wrong,
            "partial_success": partial_success,
            "scope": cr,
        }
        if empty_corpus:
            # W805: closed-enum state for the empty-corpus path so agents
            # can distinguish "no imports yet" from "scope-clean run".
            _summary["state"] = "no_imports"
        click.echo(
            to_json(
                json_envelope(
                    "boundary",
                    summary=_summary,
                    agent_contract={"facts": facts},
                    findings=all_findings,
                )
            )
        )
        if ci and n_wrong > 0:
            ctx.exit(5)
        return

    # --- Text output ---
    click.echo(f"VERDICT: {verdict}")
    if total == 0:
        return
    click.echo()
    rows: list[list[str]] = []
    for f in all_findings[:50]:
        ev = f.get("evidence") or {}
        if f["kind"] == "public_by_accident":
            detail = f"_-prefixed in __all__: {ev.get('exported_name', '?')}"
        else:
            detail = (
                f"layer {f.get('layer_from')}->{f.get('layer_to')}: "
                f"{ev.get('source_symbol', '?')} -> {ev.get('target_symbol', '?')}"
            )
        rows.append(
            [
                f"[{f['severity']}]",
                f["kind"],
                loc(f["file"], int(f.get("line") or 0)),
                detail,
            ]
        )
    click.echo(
        format_table(
            ["Sev", "Kind", "Location", "Detail"],
            rows,
            budget=50,
        )
    )
    if len(all_findings) > 50:
        click.echo()
        click.echo(f"... {len(all_findings) - 50} more (use --json for the full list)")

    if ci and n_wrong > 0:
        ctx.exit(5)


# ---------------------------------------------------------------------------
# SARIF projection (module-local)
# ---------------------------------------------------------------------------


def _boundary_to_sarif(findings: list[dict]) -> dict:
    """Minimal SARIF 2.1.0 projection for boundary findings.

    Kept module-local rather than in ``roam.output.sarif`` because the
    projection is highly command-specific (two closed-enum rule ids
    only) and the kind→severity map already lives in
    ``_KIND_SEVERITY`` at the top of this module. Per CLAUDE.md
    "Adding a new CLI command" step 9 (module-local exception).
    """
    rules = [
        {
            "id": f"boundary/{kind}",
            "name": kind,
            "shortDescription": {"text": kind},
            "defaultConfiguration": {"level": "error" if _KIND_SEVERITY[kind] == "high" else "warning"},
        }
        for kind in _BOUNDARY_KINDS
    ]
    results = []
    for f in findings:
        results.append(
            {
                "ruleId": f"boundary/{f['kind']}",
                "level": "error" if f.get("severity") == "high" else "warning",
                "message": {"text": (f.get("evidence") or {}).get("reason") or f.get("kind", "")},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": f.get("file") or ""},
                            "region": {"startLine": int(f.get("line") or 1)},
                        }
                    }
                ],
            }
        )
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "roam-boundary",
                        "version": _BOUNDARY_DETECTOR_VERSION,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
