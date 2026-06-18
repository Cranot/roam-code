"""Compositional generator for ``AGENTS.md``.

See :mod:`roam.agents_md` for the API and rationale. Every section
function consults an existing subsystem (conventions helper, laws
miner, constitution loader, capability registry) so the output stays
consistent with the rest of the agent-OS.

Design notes
------------

* **Read-only.** No section function writes anything, mutates the DB,
  or shells out. The generator must be safe to call from inside other
  read-only commands.

* **Fast.** Target is < 3s on roam-sized repos (~2k files,
  ~10k symbols). We delegate to ``compute_conventions`` (one SQL
  query) and ``mine_laws`` (a handful of SQL queries) but we do NOT
  invoke sibling Click commands. Danger zones use the same single
  SQL query as :func:`roam.commands.cmd_dashboard._top_danger_files`.

* **Graceful degradation.** Every optional subsystem (laws, rules,
  constitution) is wrapped in try/except. Missing data yields an
  empty section but never raises -- this matches the constitution
  loader's "substrate failure must not derail the caller" policy.

* **No emoji / no colors.** Output is plain ASCII markdown so it round-
  trips through CI grep and small-context LLMs without surprises.

Public API surface (W15.2 followup — promoted out of the private
``_section_*`` namespace because ``cmd_brief`` reuses them and a rename
would break the brief silently):

* :func:`section_stack`
* :func:`section_danger_zones`
* :func:`section_laws`
* :func:`generate_agents_md`
* :func:`render_agents_markdown`
* :class:`AgentsMd`

The remaining ``_section_*`` helpers stay module-private because no
external caller consumes them today. Underscore-prefixed aliases are
kept for the three promoted helpers as a backward-compat shim (no
external callers exist today; aliases can be removed in a future
cleanup).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from roam.observability import log_swallowed

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AgentsMd:
    """Structured view of a generated ``AGENTS.md``.

    Each attribute corresponds to one rendered section. The
    :data:`sources` map records which subsystem contributed which
    field, so consumers can audit provenance.
    """

    title: str = "AGENTS.md"
    generated_at: str = ""
    summary: str = ""
    stack: list[dict[str, Any]] = field(default_factory=list)
    conventions: dict[str, Any] = field(default_factory=dict)
    danger_zones: list[dict[str, Any]] = field(default_factory=list)
    pre_edit_gates: list[str] = field(default_factory=list)
    after_edit_gates: list[str] = field(default_factory=list)
    before_pr_gates: list[str] = field(default_factory=list)
    # W14.2 Synergy 3 — active-mode summary. ``current_mode`` carries
    # the resolved ModePolicy.name, the count of allowed commands at
    # that mode, a short list of representative allowed commands (for
    # the "highlights" sub-section), and a list of representative
    # commands that are blocked at this mode but available at a higher
    # one (for the "blocked" sub-section).
    current_mode: dict[str, Any] = field(default_factory=dict)
    test_conventions: dict[str, Any] = field(default_factory=dict)
    laws: list[dict[str, Any]] = field(default_factory=list)
    rules_files: list[str] = field(default_factory=list)
    capability_summary: dict[str, Any] = field(default_factory=dict)
    constitution_path: Optional[str] = None
    sources: dict[str, str] = field(default_factory=dict)

    def section_names(self) -> list[str]:
        """Names of sections that have content (used in the envelope)."""
        out: list[str] = ["Quick read"]
        if self.stack:
            out.append("Stack")
        if self.conventions:
            out.append("Naming conventions")
        if self.danger_zones:
            out.append("Danger zones")
        if self.pre_edit_gates or self.after_edit_gates or self.before_pr_gates:
            out.append("Workflow gates")
        # Current-mode section is positioned AFTER workflow gates and
        # BEFORE test conventions (modes are gate-related context).
        if self.current_mode:
            out.append("Current mode")
        if self.test_conventions:
            out.append("Test conventions")
        if self.laws:
            out.append("Architectural invariants")
        if self.rules_files:
            out.append("Graph-aware rules")
        if self.capability_summary:
            out.append("Capability roster")
        out.append("Where to look next")
        return out

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict view used by ``roam agents-md --json``."""
        return {
            "title": self.title,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "stack": self.stack,
            "conventions": self.conventions,
            "danger_zones": self.danger_zones,
            "pre_edit_gates": self.pre_edit_gates,
            "after_edit_gates": self.after_edit_gates,
            "before_pr_gates": self.before_pr_gates,
            "current_mode": self.current_mode,
            "test_conventions": self.test_conventions,
            "laws": self.laws,
            "rules_files": self.rules_files,
            "capability_summary": self.capability_summary,
            "constitution_path": self.constitution_path,
            "sources": self.sources,
            "sections": self.section_names(),
        }


# ---------------------------------------------------------------------------
# Subsystem helpers (each is best-effort and never raises)
# ---------------------------------------------------------------------------


def _project_name(repo_root: Path) -> str:
    try:
        return Path(repo_root).resolve().name
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # path-resolution failure degrades the AGENTS.md headline to a
        # generic placeholder; surface the lineage so the cause is known.
        log_swallowed("agents_md.generator:project_name", exc)
        return "this repository"


def section_stack(conn) -> list[dict[str, Any]]:
    """Language mix derived from ``files.language``.

    Mirrors the breakdown shown in ``roam describe`` so an agent
    reading AGENTS.md and an agent invoking ``roam describe`` see the
    same numbers.

    Public API (W15.2). Use this name in new callers; the
    underscore-prefixed alias remains for backward compat.
    """
    try:
        rows = conn.execute(
            "SELECT COALESCE(language, '') AS language, COUNT(*) AS n FROM files GROUP BY language"
        ).fetchall()
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a SQL
        # failure (missing table, schema drift) produces the same empty
        # Stack section as a genuinely unindexed tree. Surface the lineage
        # so a broken query has a discoverable cause (visible under
        # ROAM_VERBOSE=1).
        log_swallowed("agents_md.generator:section_stack:sql", exc)
        return []

    total = 0
    counts: list[tuple[str, int]] = []
    for r in rows:
        lang = (r["language"] or "").strip()
        n = int(r["n"] or 0)
        if not lang or n == 0:
            continue
        total += n
        counts.append((lang, n))

    counts.sort(key=lambda t: t[1], reverse=True)
    out: list[dict[str, Any]] = []
    for lang, n in counts[:12]:
        pct = round(100 * n / total, 1) if total else 0.0
        out.append({"language": lang, "files": n, "pct": pct})
    return out


def _section_conventions(conn) -> dict[str, Any]:
    """Per-kind naming summary via the canonical detector."""
    try:
        from roam.commands.conventions_helper import compute_conventions
    except Exception as exc:
        # Lazy import: defers loading the conventions helper until a
        # generator caller actually needs it. ImportError here means a
        # partial install — surface the lineage rather than masking it.
        log_swallowed("agents_md.generator:section_conventions:import", exc)
        return {}
    try:
        result = compute_conventions(conn)
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # detector EXECUTION failure produces the same empty section as a
        # repo with no symbols. Surface the lineage so a broken detector
        # has a discoverable cause.
        log_swallowed("agents_md.generator:section_conventions:compute", exc)
        return {}

    by_kind = result.get("by_kind", {}) or {}
    rows: list[dict[str, Any]] = []
    # Stable order matching summarize_by_kind_text.
    ordering = (
        "function",
        "class",
        "method",
        "variable",
        "constant",
        "property",
        "field",
        "interface",
        "struct",
        "enum",
        "trait",
        "type_alias",
    )
    seen = set()
    for kind in ordering:
        if kind in by_kind:
            info = by_kind[kind]
            rows.append(
                {
                    "kind": kind,
                    "style": info.get("style", ""),
                    "pct": info.get("pct", 0),
                    "total": info.get("total", 0),
                    "has_majority": info.get("has_majority", False),
                }
            )
            seen.add(kind)
    for kind, info in sorted(by_kind.items()):
        if kind in seen:
            continue
        rows.append(
            {
                "kind": kind,
                "style": info.get("style", ""),
                "pct": info.get("pct", 0),
                "total": info.get("total", 0),
                "has_majority": info.get("has_majority", False),
            }
        )
    return {
        "by_kind": rows,
        "total_analyzed": result.get("total_analyzed", 0),
        "detector": "roam.commands.conventions_helper.compute_conventions",
    }


def section_danger_zones(conn, *, limit: int) -> list[dict[str, Any]]:
    """Top-N files by ``churn x complexity x max_fan_in``.

    Same single SQL query as
    :func:`roam.commands.cmd_dashboard._top_danger_files` so AGENTS.md
    and ``roam dashboard`` ranked the same files. We rename the
    ``danger_score`` field but include a ``danger_score_definition``
    so consumers know exactly what the number means -- this is the
    pattern from CLAUDE.md "Pattern 3".

    Public API (W15.2).
    """
    try:
        rows = conn.execute(
            """
            SELECT f.path,
                   COALESCE(fs.total_churn, 0) AS churn,
                   COALESCE(fs.complexity, 0)  AS complexity,
                   (SELECT COALESCE(MAX(gm.in_degree), 0)
                      FROM symbols s
                      JOIN graph_metrics gm ON gm.symbol_id = s.id
                     WHERE s.file_id = f.id) AS max_fan_in
              FROM files f
              LEFT JOIN file_stats fs ON fs.file_id = f.id
             WHERE COALESCE(f.file_role, 'source') = 'source'
               AND COALESCE(fs.total_churn, 0)  > 0
               AND COALESCE(fs.complexity, 0)   > 0
            """
        ).fetchall()
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a SQL
        # failure produces the same empty danger-zone table as a repo
        # with no churn history. Surface the lineage so a broken
        # multi-table join has a discoverable cause.
        log_swallowed("agents_md.generator:section_danger_zones:sql", exc)
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        churn = int(r["churn"] or 0)
        complexity = float(r["complexity"] or 0.0)
        fan_in = int(r["max_fan_in"] or 0)
        if fan_in <= 0:
            continue
        score = churn * complexity * fan_in
        out.append(
            {
                "path": r["path"],
                "danger_score": round(score, 1),
                "churn": churn,
                "complexity": round(complexity, 1),
                "max_fan_in": fan_in,
            }
        )
    out.sort(key=lambda d: d["danger_score"], reverse=True)
    return out[:limit]


def _section_gates(repo_root: Path) -> tuple[list[str], list[str], list[str], Optional[str]]:
    """Pre-edit / after-edit / pre-PR check templates from the constitution.

    Falls back to the loader's default-gate set so a repo that has not
    yet run ``roam constitution init`` still gets sensible defaults
    in its AGENTS.md.
    """
    try:
        from roam.constitution.loader import (
            _default_required_checks,
            constitution_path,
            load_constitution,
        )
    except Exception as exc:
        # Lazy import: defers the constitution loader until a caller needs
        # it. ImportError signals a partial install — surface the lineage.
        log_swallowed("agents_md.generator:section_gates:import", exc)
        return [], [], [], None

    path: Optional[str] = None
    required: dict[str, list[str]] = {}

    try:
        loaded = load_constitution(Path(repo_root))
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # load FAILURE falls through to default gates below, the same as
        # a repo with no constitution.yml. Surface the lineage so a
        # corrupt constitution file isn't read as "no constitution".
        log_swallowed("agents_md.generator:section_gates:load", exc)
        loaded = None

    if loaded is not None and loaded.required_checks:
        required = loaded.required_checks
        try:
            cp = constitution_path(Path(repo_root))
            if cp.exists():
                path = str(cp)
        except Exception as exc:
            # Loud-fallback: a path-resolution failure drops the
            # constitution_path attribution. Surface the lineage.
            log_swallowed("agents_md.generator:section_gates:path", exc)
            path = None
    else:
        try:
            required = _default_required_checks()
        except Exception as exc:
            # Loud-fallback: the loader's own default-gate set failed —
            # this should never happen, so surface it loudly.
            log_swallowed("agents_md.generator:section_gates:defaults", exc)
            required = {}

    pre_edit = list(required.get("before_edit", []) or [])
    after_edit = list(required.get("after_edit", []) or [])
    before_pr = list(required.get("before_pr", []) or [])
    return pre_edit, after_edit, before_pr, path


def _section_current_mode(repo_root: Path) -> dict[str, Any]:
    """Summarise the active agent-mode for the AGENTS.md "Current mode" section.

    Returns a dict with:
      - ``name``: active mode name (e.g. ``safe_edit``)
      - ``allowed_count``: number of commands the active mode allows
      - ``allowed_highlights``: up to ~12 representative allowed commands
      - ``blocked_examples``: up to ~6 commands blocked at this mode but
        unlocked at the next-higher mode (lets readers see what the
        upgrade buys them)
      - ``upgrade_to``: name of the next-higher mode (or ``None`` if
        already at ``autonomous_pr``)
      - ``valid_modes``: full ordered list of valid modes (for the
        switch-with hint line)

    Best-effort: if the policy module can't be imported (partial
    install) or anything raises, returns an empty dict.
    """
    try:
        from roam.modes.policy import (
            VALID_MODES,
            list_modes,
            resolve_mode,
        )
    except Exception as exc:
        # Lazy import: defers the modes policy module until a caller needs
        # it. ImportError signals a partial install — surface the lineage.
        log_swallowed("agents_md.generator:section_current_mode:import", exc)
        return {}
    try:
        active = resolve_mode(Path(repo_root))
        policies = list_modes(Path(repo_root))
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # mode-resolution FAILURE produces the same empty Current-mode
        # section as a partial install. Surface the lineage.
        log_swallowed("agents_md.generator:section_current_mode:resolve", exc)
        return {}

    allowed = sorted(active.allowed_commands)
    # Highlights: a stable, hand-curated front of the allow-list so the
    # output is deterministic. The full count surfaces immediately
    # after; we don't need to enumerate every command.
    preferred_order = (
        "search",
        "retrieve",
        "context",
        "preflight",
        "diff",
        "critique",
        "pr-bundle",
        "impact",
        "understand",
        "describe",
        "health",
        "doctor",
        "tour",
        "next",
        "intent-check",
        "mode",
    )
    highlights: list[str] = []
    for cmd in preferred_order:
        if cmd in active.allowed_commands and cmd not in highlights:
            highlights.append(cmd)
        if len(highlights) >= 12:
            break
    # Pad with whatever else is allowed if we ran short.
    if len(highlights) < 12:
        for cmd in allowed:
            if cmd in highlights:
                continue
            highlights.append(cmd)
            if len(highlights) >= 12:
                break

    # Find the next-higher mode + which commands IT unlocks.
    upgrade_to: Optional[str] = None
    blocked_examples: list[str] = []
    try:
        idx = VALID_MODES.index(active.name)
    except ValueError:
        idx = -1
    if 0 <= idx < len(VALID_MODES) - 1:
        upgrade_to = VALID_MODES[idx + 1]
        try:
            upgrade_allowed = policies[upgrade_to].allowed_commands
            # Commands NEW at the upgrade tier (not allowed now).
            new_at_upgrade = sorted(upgrade_allowed - active.allowed_commands)
            blocked_examples = new_at_upgrade[:6]
        except Exception as exc:
            # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
            # missing upgrade-tier policy drops the "blocked commands"
            # sub-section. Surface the lineage so a policy-table gap is
            # discoverable rather than read as "nothing is blocked".
            log_swallowed("agents_md.generator:section_current_mode:blocked", exc)
            blocked_examples = []

    return {
        "name": active.name,
        "source": active.source,
        "allowed_count": len(active.allowed_commands),
        "allowed_highlights": highlights,
        "blocked_examples": blocked_examples,
        "upgrade_to": upgrade_to,
        "valid_modes": list(VALID_MODES),
    }


def _section_test_conventions(conn) -> dict[str, Any]:
    """Best-effort summary of test directories and frameworks.

    Uses ``files.file_role = 'test'`` (populated by
    :mod:`roam.index.file_roles`) so the answer matches what other
    commands consider a test file.
    """
    try:
        rows = conn.execute(
            """
            SELECT path, COALESCE(language, '') AS language
              FROM files
             WHERE COALESCE(file_role, '') = 'test'
            """
        ).fetchall()
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a SQL
        # failure returns {} (no Test-conventions section), distinct from
        # the empty-but-present {"test_file_count": 0, ...} below which is
        # the honest "queried, found no test files" answer. Surface the
        # lineage so a broken query isn't read as "no tests".
        log_swallowed("agents_md.generator:section_test_conventions:sql", exc)
        return {}
    if not rows:
        return {"test_file_count": 0, "test_dirs": [], "languages": {}}

    test_dirs: Counter = Counter()
    lang_counts: Counter = Counter()
    for r in rows:
        p = (r["path"] or "").replace("\\", "/")
        lang = (r["language"] or "").strip()
        if lang:
            lang_counts[lang] += 1
        # Take the first directory component as the test root.
        if "/" in p:
            head = p.split("/", 1)[0]
            test_dirs[head] += 1
        else:
            test_dirs["."] += 1
    return {
        "test_file_count": len(rows),
        "test_dirs": [{"dir": d, "files": n} for d, n in test_dirs.most_common(6)],
        "languages": {lang: n for lang, n in lang_counts.most_common(6)},
    }


def section_laws(conn, *, top_n: int) -> list[dict[str, Any]]:
    """High-confidence mined laws (sorted by confidence then sample size).

    Public API (W15.2).
    """
    try:
        from roam.laws.miner import mine_laws
    except Exception as exc:
        # Lazy import: defers the laws miner until a caller needs it.
        # ImportError signals a partial install — surface the lineage.
        log_swallowed("agents_md.generator:section_laws:import", exc)
        return []
    try:
        laws = mine_laws(conn, top=top_n)
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # miner EXECUTION failure produces the same empty invariants
        # section as a repo with no mined laws. Surface the lineage.
        log_swallowed("agents_md.generator:section_laws:mine", exc)
        return []

    out: list[dict[str, Any]] = []
    for law in laws:
        try:
            d = law.to_dict()
        except Exception as exc:
            # Loud-fallback: a single law that fails to serialise is
            # skipped (the rest of the section still renders), but surface
            # the lineage so a broken Law.to_dict() is discoverable.
            log_swallowed("agents_md.generator:section_laws:to_dict", exc)
            continue
        # Only surface the small set of fields the AGENTS.md section
        # actually renders -- agent-readability over completeness.
        evidence = d.get("evidence", {}) or {}
        out.append(
            {
                "id": d.get("id", ""),
                "kind": d.get("kind", ""),
                "description": d.get("description", ""),
                "confidence": d.get("confidence", ""),
                "conformance_pct": evidence.get("conformance_pct"),
                "sample_size": evidence.get("sample_size"),
            }
        )
    return out


def _section_rules_files(repo_root: Path) -> list[str]:
    """Relative paths of ``.roam/rules/*.yml`` rule files."""
    try:
        rules_dir = Path(repo_root) / ".roam" / "rules"
        if not rules_dir.is_dir():
            return []
        out: list[str] = []
        for p in sorted(rules_dir.glob("*.yml")):
            try:
                rel = p.relative_to(Path(repo_root))
            except ValueError:
                # ``relative_to`` raises ValueError when the path is not
                # under repo_root — expected for an unusual layout; the
                # absolute path is a fine fallback. Not a swallowed bug.
                rel = p
            out.append(str(rel).replace("\\", "/"))
        return out
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # glob/is_dir failure produces the same empty section as a repo
        # with no .roam/rules/. Surface the lineage.
        log_swallowed("agents_md.generator:section_rules_files", exc)
        return []


def _section_capability_summary() -> dict[str, Any]:
    """Capability roster size + MCP preset counts.

    Imports a curated list of decorated modules to populate the
    registry, mirroring :func:`cmd_capabilities._populate_registry`.
    We swallow import errors so a partial install doesn't crash
    AGENTS.md generation.
    """
    import importlib

    try:
        from roam.capability import REGISTRY
    except Exception as exc:
        # Lazy import: defers the capability registry until a caller needs
        # it. ImportError signals a partial install — surface the lineage.
        log_swallowed("agents_md.generator:section_capability:import", exc)
        return {}

    # Mirrors cmd_capabilities._populate_registry; importing these modules
    # populates `REGISTRY` so `registered_count` / `ai_safe_count` below are
    # non-empty. The MCP `core` / `full` counts do NOT come from the
    # registry (see the `mcp_preset_counts()` block below) -- the registry
    # is a superset and has no reliable per-preset signal.
    decorated_modules = [
        "roam.commands.cmd_critique",
        "roam.commands.cmd_preflight",
        "roam.commands.cmd_understand",
        "roam.commands.cmd_permit",
        "roam.commands.cmd_postmortem",
        "roam.commands.cmd_article_12_check",
        "roam.commands.cmd_constitution",
    ]
    for mod in decorated_modules:
        try:
            importlib.import_module(mod)
        except Exception as exc:
            # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
            # module that fails to import silently drops its decorated
            # capabilities from the "57 core / N full" counts below.
            # Surface the lineage so a count drift has a discoverable
            # cause (rate-limited per-scope; visible under ROAM_VERBOSE=1).
            log_swallowed(f"agents_md.generator:section_capability:import_module:{mod}", exc)

    try:
        caps = REGISTRY.all()
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # registry-read failure produces the same empty Capability-roster
        # section as a registry that was never populated.
        log_swallowed("agents_md.generator:section_capability:registry_all", exc)
        return {}

    ai_safe = sum(1 for c in caps if c.ai_safe)

    # MCP preset counts come from the canonical AST-only surface counter --
    # the same source `roam surface --json` and `dev/build_readme_counts.py`
    # use. The capability registry is NOT a valid source here: no
    # `@roam_capability` ever sets `mcp_preset` to include `"full"`, and only
    # a curated handful of modules are import-populated above, so a
    # registry-derived count was structurally always wrong (`full: 0`,
    # `core: ~9`). `mcp_preset_counts()` parses `_PRESETS` in mcp_server.py
    # directly, so it reports the real `core: 57 / full: 227`.
    core: Optional[int] = None
    full: Optional[int] = None
    try:
        from roam.surface_counts import mcp_preset_counts

        preset_counts = mcp_preset_counts()
        core = int(preset_counts.get("core") or 0) or None
        full = int(preset_counts.get("full") or 0) or None
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a parse
        # failure differs from a genuine zero. Drop the field (None renders
        # cleanly via `_render_capability`) rather than ship a structural 0.
        log_swallowed("agents_md.generator:section_capability:mcp_preset_counts", exc)
        core = None
        full = None

    # Also try to surface the authoritative CLI command total (the
    # surface counter parses the AST directly so it's robust to lazy
    # imports). Empty result is acceptable -- the summary block stays
    # informative without it.
    cli_total: Optional[int] = None
    try:
        from roam.surface_counts import cli_surface_counts

        cli_total = int(cli_surface_counts().get("command_names") or 0) or None
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — the
        # CLI count is optional enrichment (None renders cleanly), but a
        # FAILURE differs from a genuine zero; surface the lineage.
        log_swallowed("agents_md.generator:section_capability:cli_surface_counts", exc)
        cli_total = None

    mcp_total: Optional[int] = None
    try:
        from roam.surface_counts import mcp_surface_counts

        mcp_total = int(mcp_surface_counts().get("registered_tools") or 0) or None
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — same
        # rationale as cli_surface_counts: a FAILURE differs from a zero.
        log_swallowed("agents_md.generator:section_capability:mcp_surface_counts", exc)
        mcp_total = None

    return {
        "registered_count": len(caps),
        "ai_safe_count": ai_safe,
        "mcp_core_count": core,
        "mcp_full_count": full,
        "cli_command_count": cli_total,
        "mcp_tool_count": mcp_total,
    }


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------


def _build_summary(
    repo_root: Path,
    stack: list[dict[str, Any]],
    capability_summary: dict[str, Any],
) -> str:
    """Two-sentence elevator pitch.

    Concrete-noun first (LAW 4 from CLAUDE.md): name the project, name
    the languages, name the capability count. Agents that read only
    this paragraph still get a useful anchor.
    """
    name = _project_name(repo_root)
    if stack:
        top = stack[0]
        if len(stack) > 1:
            lang_phrase = f"{top['language']} ({top['pct']}%) plus {len(stack) - 1} other language(s)"
        else:
            lang_phrase = f"{top['language']} ({top['pct']}%)"
    else:
        lang_phrase = "an empty or unindexed tree"

    cli_total = capability_summary.get("cli_command_count")
    mcp_total = capability_summary.get("mcp_tool_count")
    if cli_total and mcp_total:
        cap_phrase = (
            f" {cli_total} CLI commands and {mcp_total} MCP tools are available; run `roam --help-all` to list them."
        )
    elif cli_total:
        cap_phrase = f" {cli_total} CLI commands are available; run `roam --help-all` to list them."
    else:
        cap_phrase = " Run `roam --help-all` to list all available commands."

    return (
        f"This is **{name}**, primarily {lang_phrase}."
        f" AGENTS.md is the doc agents read first when joining this repo."
        f"{cap_phrase}"
    )


def generate_agents_md(
    repo_root: Path,
    conn,
    *,
    with_laws: bool = True,
    with_rules: bool = True,
    with_constitution: bool = True,
    top_n_danger: int = 10,
    top_n_laws: int = 8,
) -> AgentsMd:
    """Synthesize an :class:`AgentsMd` from indexed-repo state.

    Parameters
    ----------
    repo_root
        Project root containing the SQLite index.
    conn
        Open readonly SQLite connection.
    with_laws / with_rules / with_constitution
        Toggle the corresponding sections. A False toggle still allows
        the loader to populate defaults (gates fall back to the
        constitution-loader defaults if no constitution file exists).
    top_n_danger
        Cap on the danger-zone table size. Defaults to 10 (matches the
        operational rule "show enough to be useful, not enough to
        overwhelm").
    top_n_laws
        Cap on the architectural-invariants section.

    Returns
    -------
    AgentsMd
        Structured view; pass to :func:`render_agents_markdown` to get a string.
    """
    am = AgentsMd()
    am.generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    sources_consulted: dict[str, str] = {}

    am.stack = section_stack(conn)
    if am.stack:
        sources_consulted["stack"] = "db: files.language"

    am.conventions = _section_conventions(conn)
    if am.conventions:
        sources_consulted["conventions"] = "roam.commands.conventions_helper"

    am.danger_zones = section_danger_zones(conn, limit=top_n_danger)
    if am.danger_zones:
        sources_consulted["danger_zones"] = "db: files + file_stats + graph_metrics.in_degree"

    am.test_conventions = _section_test_conventions(conn)
    if am.test_conventions:
        sources_consulted["test_conventions"] = "db: files.file_role='test'"

    if with_constitution:
        pre, after, prep, path = _section_gates(repo_root)
        am.pre_edit_gates = pre
        am.after_edit_gates = after
        am.before_pr_gates = prep
        am.constitution_path = path
        if pre or after or prep:
            sources_consulted["gates"] = "roam.constitution.loader" + ("" if path else " (defaults)")

    # W14.2 Synergy 3 — current-mode section is always generated. It's
    # cheap (single resolve_mode + list_modes call) and orthogonal to
    # the constitution toggle: modes resolve from env / file / default
    # even when the constitution loader yields nothing.
    am.current_mode = _section_current_mode(repo_root)
    if am.current_mode:
        sources_consulted["current_mode"] = "roam.modes.policy.resolve_mode"

    if with_laws:
        am.laws = section_laws(conn, top_n=top_n_laws)
        if am.laws:
            sources_consulted["laws"] = "roam.laws.miner"

    if with_rules:
        am.rules_files = _section_rules_files(repo_root)
        if am.rules_files:
            sources_consulted["rules"] = ".roam/rules/*.yml"

    am.capability_summary = _section_capability_summary()
    if am.capability_summary:
        sources_consulted["capability"] = "roam.capability.REGISTRY"

    am.summary = _build_summary(repo_root, am.stack, am.capability_summary)
    am.sources = sources_consulted
    return am


# ---------------------------------------------------------------------------
# Backward-compat aliases (W15.2 followup)
# ---------------------------------------------------------------------------
# These three helpers were renamed from ``_section_*`` to ``section_*`` so
# external callers (currently only ``cmd_brief``) can import them without
# touching module-private names. The aliases keep the old names callable in
# case any code outside the repo imported them; the in-repo callsite was
# updated to use the public names. The aliases can be removed in a future
# cleanup once we're confident no external consumer relies on them.
_section_stack = section_stack
_section_danger_zones = section_danger_zones
_section_laws = section_laws


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _render_stack(stack: list[dict[str, Any]]) -> list[str]:
    if not stack:
        return []
    lines = ["## Stack", ""]
    for entry in stack:
        lines.append(f"- **{entry['language']}** ({entry['files']} files, {entry['pct']}%)")
    lines.append("")
    return lines


def _render_conventions(conventions: dict[str, Any]) -> list[str]:
    if not conventions:
        return []
    rows = conventions.get("by_kind", [])
    if not rows:
        return []
    lines = ["## Naming conventions", ""]
    lines.append("| Kind | Style | Conformance | Sample |")
    lines.append("|---|---|---|---|")
    for r in rows:
        pct = r.get("pct", 0)
        total = r.get("total", 0)
        style = r.get("style") or "-"
        kind = r.get("kind") or "-"
        lines.append(f"| {kind} | {style} | {pct}% | {total} |")
    lines.append("")
    lines.append(
        f"_Detector: `{conventions.get('detector', 'compute_conventions')}` "
        f"({conventions.get('total_analyzed', 0)} symbols analyzed)._"
    )
    lines.append("")
    return lines


def _render_danger(zones: list[dict[str, Any]]) -> list[str]:
    if not zones:
        return []
    lines = ["## Danger zones (touch carefully)", ""]
    lines.append(
        "Top files by `churn x complexity x max_fan_in`. Edit these only after running `roam preflight <symbol>`:"
    )
    lines.append("")
    lines.append("| File | Danger score | Churn | Complexity | Max fan-in |")
    lines.append("|---|---:|---:|---:|---:|")
    for z in zones:
        lines.append(f"| `{z['path']}` | {z['danger_score']} | {z['churn']} | {z['complexity']} | {z['max_fan_in']} |")
    lines.append("")
    lines.append(
        "_`danger_score_definition: churn x complexity x max_fan_in`."
        " For the full `roam metrics-push --dry-run` view, run that command._"
    )
    lines.append("")
    return lines


def _render_gates(
    pre: list[str],
    after: list[str],
    prep: list[str],
    constitution_path: Optional[str],
) -> list[str]:
    if not (pre or after or prep):
        return []
    lines = ["## Workflow gates", ""]
    if constitution_path:
        lines.append(f"From `{constitution_path}`:")
    else:
        lines.append(
            "_No `.roam/constitution.yml` found_ -- "
            "showing defaults from `roam.constitution.loader`."
            " Run `roam constitution init` to commit these to a file."
        )
    lines.append("")
    if pre:
        lines.append("**Before editing any symbol:**")
        lines.append("")
        for cmd in pre:
            lines.append(f"- `{cmd}`")
        lines.append("")
    if after:
        lines.append("**After editing:**")
        lines.append("")
        for cmd in after:
            lines.append(f"- `{cmd}`")
        lines.append("")
    if prep:
        lines.append("**Before opening a PR:**")
        lines.append("")
        for cmd in prep:
            lines.append(f"- `{cmd}`")
        lines.append("")
    return lines


def _render_current_mode(mode: dict[str, Any]) -> list[str]:
    """Render the "Current mode" markdown section.

    Lays out:
      - Active mode + allowed_count headline (one paste-able line).
      - Switch-with hint enumerating valid modes.
      - "Allowed in this mode (highlights):" bullet list.
      - "Blocked in this mode:" bullet list with upgrade target.

    Empty sub-sections are suppressed so a degenerate policy (e.g. an
    autonomous_pr mode with no upgrade target) renders cleanly.
    """
    if not mode:
        return []
    name = mode.get("name") or ""
    if not name:
        return []
    allowed_count = mode.get("allowed_count", 0)
    valid_modes = mode.get("valid_modes") or []
    highlights = mode.get("allowed_highlights") or []
    blocked = mode.get("blocked_examples") or []
    upgrade_to = mode.get("upgrade_to")

    lines = ["## Current mode", ""]
    lines.append(f"Active mode: **{name}** ({allowed_count} command(s) allowed).")
    if valid_modes:
        joined = " | ".join(valid_modes)
        lines.append(f"Switch with: `roam mode <{joined}>`")
    lines.append("")

    if highlights:
        lines.append(f"**Allowed in `{name}` mode (highlights):**")
        lines.append("")
        joined_h = ", ".join(f"`{c}`" for c in highlights)
        lines.append(joined_h)
        lines.append("")

    if blocked and upgrade_to:
        lines.append(f"**Blocked in `{name}` mode** (unlock with `roam mode {upgrade_to}`):")
        lines.append("")
        joined_b = ", ".join(f"`{c}`" for c in blocked)
        lines.append(joined_b)
        lines.append("")
    elif not upgrade_to:
        lines.append("_This is the highest mode -- every documented command is allowed._")
        lines.append("")

    return lines


def _render_test_conventions(tc: dict[str, Any]) -> list[str]:
    if not tc:
        return []
    test_count = tc.get("test_file_count", 0)
    if test_count == 0:
        return [
            "## Test conventions",
            "",
            "No test files detected (looked at `files.file_role = 'test'`).",
            "",
        ]
    lines = ["## Test conventions", ""]
    dirs = tc.get("test_dirs") or []
    if dirs:
        names = ", ".join(f"`{d['dir']}/` ({d['files']})" for d in dirs)
        lines.append(f"Test files live under: {names}")
    lang_summary = tc.get("languages") or {}
    if lang_summary:
        lang_names = ", ".join(f"{k} ({v})" for k, v in lang_summary.items())
        lines.append(f"Languages used in tests: {lang_names}")
    lines.append(f"Total test files indexed: **{test_count}**.")
    lines.append("")
    return lines


def _render_laws(laws: list[dict[str, Any]]) -> list[str]:
    if not laws:
        return []
    lines = ["## Architectural invariants", "", "Mined by `roam laws mine`:", ""]
    for i, law in enumerate(laws, 1):
        desc = law.get("description", law.get("id", ""))
        conf = law.get("confidence", "")
        conformance = law.get("conformance_pct")
        sample = law.get("sample_size")
        meta = []
        if conf:
            meta.append(f"confidence: {conf}")
        if conformance is not None:
            meta.append(f"conformance: {conformance}%")
        if sample is not None:
            meta.append(f"sample: {sample}")
        meta_str = f" _({'; '.join(meta)})_" if meta else ""
        lines.append(f"{i}. **{desc}**{meta_str}")
    lines.append("")
    return lines


def _render_rules(rules: list[str]) -> list[str]:
    if not rules:
        return []
    lines = ["## Graph-aware policy rules", "", "Files in `.roam/rules/`:"]
    lines.append("")
    for r in rules:
        lines.append(f"- `{r}`")
    lines.append("")
    lines.append("Run `roam rules --ci` to evaluate all of them.")
    lines.append("")
    return lines


def _render_capability(cap: dict[str, Any]) -> list[str]:
    if not cap:
        return []
    lines = ["## Capability roster", ""]
    cli_total = cap.get("cli_command_count")
    mcp_total = cap.get("mcp_tool_count")
    core = cap.get("mcp_core_count")
    full = cap.get("mcp_full_count")
    if cli_total:
        lines.append(f"- **{cli_total}** CLI commands (`roam --help-all` lists them all)")
    if mcp_total:
        lines.append(f"- **{mcp_total}** MCP tools registered")
    if core:
        lines.append(f"- **{core}** capabilities in the MCP `core` preset (default)")
    if full:
        lines.append(f"- **{full}** capabilities in the MCP `full` preset (`ROAM_MCP_PRESET=full`)")
    ai_safe = cap.get("ai_safe_count")
    if ai_safe:
        lines.append(f"- **{ai_safe}** capabilities marked `ai_safe=True`")
    lines.append("")
    lines.append(
        "Common workflow: `roam preflight <sym>` -> edit -> "
        "`git diff | roam critique` -> `roam pr-bundle validate --strict`."
    )
    lines.append("")
    return lines


def _render_where_next(am: AgentsMd) -> list[str]:
    lines = ["## Where to look next", ""]
    if am.constitution_path:
        lines.append(f"- `{am.constitution_path}` -- full constitution / policy")
    else:
        lines.append("- `.roam/constitution.yml` (run `roam constitution init` to create it)")
    lines.append("- `roam-laws.yml` -- mined architectural invariants")
    lines.append("- `.roam/rules/*.yml` -- graph-aware policy rules")
    lines.append("- `roam --help-all` -- every command available")
    lines.append("- `roam dashboard` -- live overview (health, danger, conventions, drift)")
    lines.append("- `roam tour` -- guided codebase walkthrough")
    lines.append("")
    return lines


def render_agents_markdown(am: AgentsMd) -> str:
    """Render an :class:`AgentsMd` to GitHub-flavored Markdown."""
    lines: list[str] = []
    lines.append(f"# {am.title}")
    lines.append("")
    lines.append(
        f"> Auto-generated by `roam agents-md` at {am.generated_at}. Run `roam agents-md --refresh` to update."
    )
    lines.append("")
    lines.append("## Quick read")
    lines.append("")
    lines.append(am.summary)
    lines.append("")
    lines.extend(_render_stack(am.stack))
    lines.extend(_render_conventions(am.conventions))
    lines.extend(_render_danger(am.danger_zones))
    lines.extend(
        _render_gates(
            am.pre_edit_gates,
            am.after_edit_gates,
            am.before_pr_gates,
            am.constitution_path,
        )
    )
    # W14.2 Synergy 3 — Current mode sits between workflow gates and
    # test conventions; modes are gate-related context.
    lines.extend(_render_current_mode(am.current_mode))
    lines.extend(_render_test_conventions(am.test_conventions))
    lines.extend(_render_laws(am.laws))
    lines.extend(_render_rules(am.rules_files))
    lines.extend(_render_capability(am.capability_summary))
    lines.extend(_render_where_next(am))
    return "\n".join(lines).rstrip() + "\n"


# Compatibility alias for callers that imported the original generic name
# before this renderer got an AGENTS.md-specific public name.
render_markdown = render_agents_markdown
