"""Causal-graph detector — per-symbol input→sink data dependencies.

R28 sub-feature 3 of 4 (shipped in W15.3).

Distinct from the call graph: causal edges record **which inputs / state
sources cause which side-effects** within a single function body.  Where
``world_model.side_effects`` answers *what* a function does, this module
answers *why* — i.e. which parameter, global, or env read flowed into
that side-effect call (or into the return value / raise / mutation).

Useful for:

- ``pr-bundle`` risks: an edit that adds ``param:user_id → io_write:db``
  to ``handleSave`` is a load-bearing change reviewers should see.
- Debugging: trace from a side-effect back to which input caused it.
- Agent safety: warn when an edit changes which input → which effect.

Heuristic detector — false negatives expected (we miss flow through
intermediate locals), false positives should be rare (we only emit an
edge when the source token appears in the same line as the sink call /
return / raise / mutation).

Detection strategy (heuristics, cheapest first)
==============================================

A.  **param_to_effect** — parameter name appears in the argument list of
    a known side-effecting call on the same line.  Confidence ``high``
    if the param token is inside the call's parenthesised args, ``medium``
    if it's elsewhere on the same line.

B.  **param_to_return** — parameter name appears in the expression of a
    ``return`` statement.

C.  **global_to_effect** — name read by the function (no preceding ``=``
    on that line) appears in a side-effecting call.  We only consider
    identifiers also seen on a top-level assignment in the file
    (``LOG = ...``) to keep the noise floor low.

D.  **env_to_effect** — ``os.environ.get('NAME')`` / ``os.getenv('NAME')``
    on a line, paired with any side-effecting call on a later line in
    the same body produces ``env:NAME → effect:<kind>`` edges.

E.  **param_to_raise** — parameter name appears in a ``raise ...`` line
    (typically validation).

F.  **global_to_mutation** — a top-level identifier is written to
    (``GLOBAL = ...``, ``GLOBAL.something = ...``) inside the body.

The detector is intended to run < 8s on the ~12K-symbol roam-code DB.
We reuse :func:`classify_side_effects`'s evidence to know which calls
on which lines are side-effecting.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from roam.db.connection import find_project_root
from roam.observability import log_swallowed
from roam.output.confidence import confidence_level_rank
from roam.world_model.side_effects import (
    KNOWN_SIDE_EFFECTING_PREFIXES,
    SideEffectClassification,
)

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

CAUSAL_KINDS = (
    "param_to_effect",
    "param_to_return",
    "global_to_effect",
    "global_to_mutation",
    "env_to_effect",
    "param_to_raise",
)

# Maximum edges retained per symbol — caps noise.  When exceeded we set
# ``truncated = True``.  Empirically 50 covers > 99% of real functions in
# the roam-code dogfood corpus.
MAX_EDGES_PER_SYMBOL = 50


@dataclass
class CausalEdge:
    """One directional data-dependency edge inside a function body."""

    source: str  # "param:path", "global:CONFIG", "env:HOME"
    sink: str  # "io_write:open", "return", "raise:ValueError"
    kind: str  # one of CAUSAL_KINDS
    confidence: str = "medium"  # "high" | "medium" | "low"
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "sink": self.sink,
            "kind": self.kind,
            "confidence": self.confidence,
            "evidence": dict(self.evidence),
        }


@dataclass
class CausalGraph:
    """Per-symbol causal graph: inputs → sinks."""

    symbol: str
    file: str
    edges: list[CausalEdge] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)  # param names + global reads + env reads
    sinks: list[str] = field(default_factory=list)  # side-effect labels + "return" + "raise"
    truncated: bool = False
    confidence: str = "medium"
    symbol_id: int = 0
    line_start: int = 0
    line_end: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "file": self.file,
            "edges": [e.to_dict() for e in self.edges],
            "inputs": list(self.inputs),
            "sinks": list(self.sinks),
            "truncated": self.truncated,
            "confidence": self.confidence,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


# ---------------------------------------------------------------------------
# Parameter extraction — cheap, regex-driven; AST avoided for speed.
# ---------------------------------------------------------------------------

# Match the ``def name(args)`` / ``async def name(args)`` head, even when
# the args span multiple lines.  We use a non-greedy capture and rely on
# the post-processing to discard self/cls and default-value RHS.
_DEF_HEAD_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+\w+\s*\((?P<args>.*?)\)\s*(?:->[^:]+)?\s*:",
    re.DOTALL | re.MULTILINE,
)

# JS / TS-ish arrow / function: ``function name(args)`` and
# ``name(args) =>`` / ``const name = (args) => ...``.  We only use this
# when the head line did not match the Python form.
_JS_FUNC_HEAD_RE = re.compile(
    r"(?:function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*)\s*\((?P<args>.*?)\)\s*(?:=>|\{)",
    re.DOTALL,
)


def _extract_params_from_signature(signature: str | None) -> list[str]:
    """Pull parameter names out of a stored signature string.

    Signatures in the DB look like e.g.
    ``(self, path: str, mode='w') -> None`` or ``(name, email)`` —
    we drop ``self``/``cls``, defaults, and type annotations.

    ``signature`` may be ``None`` (the underlying ``symbols.signature``
    column is NULL for symbols indexed before the signature extractor
    landed, and for languages whose extractor never populates it); the
    None-guard short-circuits to an empty list so callers don't need
    a cargo-cult ``or ""`` wrapper at every call-site (W1034).
    """
    if not signature:
        return []
    # Trim leading/trailing parens if present.
    s = signature.strip()
    # Find outermost parens.
    lp = s.find("(")
    rp = s.rfind(")")
    if lp >= 0 and rp > lp:
        s = s[lp + 1 : rp]
    return _split_param_list(s)


def _extract_params_from_body(body_text: str) -> list[str]:
    """Fallback: parse the ``def NAME(args)`` head from body text."""
    m = _DEF_HEAD_RE.search(body_text)
    if m:
        return _split_param_list(m.group("args"))
    m2 = _JS_FUNC_HEAD_RE.search(body_text)
    if m2:
        return _split_param_list(m2.group("args"))
    return []


_TYPE_ANN_TRIM_RE = re.compile(r":[^=,]+")  # strip ": int", ": Optional[str]"
_DEFAULT_TRIM_RE = re.compile(r"=.*$")  # strip default value


def _split_param_list(args_blob: str) -> list[str]:
    """Split a comma-separated param list, taking care of nested brackets."""
    out: list[str] = []
    if not args_blob.strip():
        return out
    depth = 0
    buf: list[str] = []
    for ch in args_blob:
        if ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    cleaned: list[str] = []
    for raw in out:
        # Strip *, **, type annotation, default value, whitespace.
        s = raw.strip()
        s = s.lstrip("*").strip()
        # Strip default value first (it may contain a colon, e.g.
        # ``x: Dict[str, int] = {}``).
        s = _DEFAULT_TRIM_RE.sub("", s).strip()
        # Now strip annotation.
        s = _TYPE_ANN_TRIM_RE.sub("", s).strip()
        # Final: bare identifier or self/cls — drop the latter.
        if not s:
            continue
        if s in ("self", "cls"):
            continue
        # Must be a valid identifier — skip junk like ``/`` or ``*``.
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", s):
            continue
        cleaned.append(s)
    return cleaned


# ---------------------------------------------------------------------------
# Side-effect anchors — derived from the side_effects detector's
# KNOWN_SIDE_EFFECTING_PREFIXES list, plus an `open(` catch-all.  We map
# each anchor to (coarse kind, short label) for sink identification.
# ---------------------------------------------------------------------------

_SIDE_EFFECT_ANCHORS: tuple[tuple[re.Pattern, str, str], ...] = tuple(
    [
        (re.compile(r"\b" + re.escape(prefix.lstrip(".")) + r"\s*\("), kind, prefix)
        for prefix, kind in KNOWN_SIDE_EFFECTING_PREFIXES
    ]
    + [
        (re.compile(r"\bopen\s*\("), "io_read", "open"),
    ]
)

# Cheap body-level pre-filter — if the body contains none of these
# substrings we can skip the per-line anchor scan entirely.  This is the
# same trick side_effects.py uses; without it the classifier walks
# ~12K * (lines * 30 anchors) regex calls.
_BODY_PRE_FILTER_RE = re.compile(
    r"\b("
    r"open|requests|httpx|aiohttp|urlopen|subprocess|threading|"
    r"multiprocessing|asyncio|os\.|tempfile|json\.dump|pickle|shutil|"
    r"psycopg2|sqlite3|boto3|fetchone|fetchall|fetchmany|"
    r"write_text|write_bytes|writelines|read_text|read_bytes|"
    r"\.commit|\.execute|\.insert|\.save|\.send|\.recv|"
    r"Path\.|return|raise"
    r")"
)

# Per-line cheap pre-filter — only trigger the full anchor loop when at
# least one of the substring fragments appears on a line.
_LINE_PRE_FILTER_RE = re.compile(
    r"(open|requests|httpx|aiohttp|urlopen|subprocess|threading|"
    r"multiprocessing|asyncio|os\.|tempfile|json\.dump|pickle|shutil|"
    r"psycopg2|sqlite3|boto3|fetchone|fetchall|fetchmany|"
    r"write_text|write_bytes|writelines|read_text|read_bytes|"
    r"\.commit|\.execute|\.insert|\.save|\.send|\.recv|Path\.)"
)

# Top-level assignment detector (file-wide) — `NAME = ...` at column 0.
_TOPLEVEL_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)

# Mutation patterns (in body): GLOBAL = ..., GLOBAL.x = ..., GLOBAL[...] = ...
_MUTATION_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\b(?:\s*[\.\[]|\s*=)")

# Return / raise detectors.
_RETURN_RE = re.compile(r"^\s*return\b(.*)$")
_RAISE_RE = re.compile(r"^\s*raise\s+([A-Za-z_][A-Za-z0-9_]*)?(.*)$")

# os.environ / os.getenv reads — capture key.
_ENV_READ_RE = re.compile(
    r"""os\.environ(?:\.get)?\s*\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""
    r"""|os\.getenv\s*\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""
    r"""|os\.environ\s*\[\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""
)


def _global_names(all_text: str) -> set[str]:
    """File-wide top-level assignments — candidate global identifiers."""
    names: set[str] = set()
    for m in _TOPLEVEL_ASSIGN_RE.finditer(all_text):
        n = m.group(1)
        # Filter common false positives (imports / class names / def heads).
        if n.isupper() or "_" in n or n.islower():
            names.add(n)
    return names


# ---------------------------------------------------------------------------
# Per-symbol scan
# ---------------------------------------------------------------------------


def _scan_one(
    sym_name: str,
    file_path: str,
    body_text: str,
    params: list[str],
    file_globals: set[str],
    sink_se: Optional[SideEffectClassification],
    line_start: int,
    symbol_id: int,
    line_end: int,
) -> CausalGraph:
    """Build a causal graph for one symbol body."""
    edges: list[CausalEdge] = []
    inputs: set[str] = set()
    sinks: set[str] = set()
    truncated = False

    if not body_text:
        return CausalGraph(
            symbol=sym_name,
            file=file_path,
            edges=edges,
            inputs=[],
            sinks=[],
            truncated=False,
            confidence="low",
            symbol_id=symbol_id,
            line_start=line_start,
            line_end=line_end,
        )

    # Cheap body-level pre-filter: skip the full per-line walk when no
    # anchor / return / raise token appears anywhere.  Cuts classifier
    # runtime from ~17s → ~5s on roam-code's 12K-symbol DB because the
    # vast majority of function bodies are short helpers with neither a
    # side-effect call nor a return-with-arg.  Trade-off: a small number
    # of global_to_mutation edges in functions that only assign to a
    # global without any other anchor are lost (≤ 2% of total edges on
    # the dogfood corpus).  Acceptable per the v1 heuristic spec.
    has_any_anchor = bool(_BODY_PRE_FILTER_RE.search(body_text))
    if not has_any_anchor:
        return CausalGraph(
            symbol=sym_name,
            file=file_path,
            edges=edges,
            inputs=[],
            sinks=[],
            truncated=False,
            confidence="low",
            symbol_id=symbol_id,
            line_start=line_start,
            line_end=line_end,
        )

    # Use line-by-line scan so we can attribute every edge to a source line.
    lines = body_text.splitlines()

    # Pre-pass: find env reads in this body and remember their keys (we
    # use the *file*-relative line number so evidence can be inspected).
    env_keys_in_body: list[tuple[int, str]] = []
    for li, line in enumerate(lines, start=line_start):
        for m in _ENV_READ_RE.finditer(line):
            key = m.group(1) or m.group(2) or m.group(3)
            if key:
                env_keys_in_body.append((li, key))
                inputs.add(f"env:{key}")

    # Build a quick set of param names for membership tests.
    param_set = {p for p in params if p}

    # Pre-compile per-param regexes for cheap "token appears on this line"
    # tests.  We anchor with \b to avoid false positives on substrings.
    param_pats: dict[str, re.Pattern] = {p: re.compile(r"\b" + re.escape(p) + r"\b") for p in param_set}
    global_pats: dict[str, re.Pattern] = {g: re.compile(r"\b" + re.escape(g) + r"\b") for g in file_globals}

    def _emit(edge: CausalEdge) -> bool:
        """Append edge if under cap; return False once truncated."""
        nonlocal truncated
        if len(edges) >= MAX_EDGES_PER_SYMBOL:
            truncated = True
            return False
        edges.append(edge)
        return True

    def _label_for_sink_match(pattern_label: str, kind: str) -> str:
        # Strip leading dot for cleaner output.
        clean = pattern_label.lstrip(".")
        return f"{kind}:{clean}"

    for li, line in enumerate(lines, start=line_start):
        stripped = line.strip()

        # ── E. param_to_raise ────────────────────────────────────────────
        raise_m = _RAISE_RE.match(line)
        if raise_m:
            exc_name = (raise_m.group(1) or "").strip()
            tail = raise_m.group(2) or ""
            sink_label = f"raise:{exc_name}" if exc_name else "raise"
            sinks.add(sink_label)
            for p in param_set:
                if param_pats[p].search(tail):
                    inputs.add(f"param:{p}")
                    if not _emit(
                        CausalEdge(
                            source=f"param:{p}",
                            sink=sink_label,
                            kind="param_to_raise",
                            confidence="high",
                            evidence={"line_number": li, "matched_token": p},
                        )
                    ):
                        break
            if truncated:
                break
            # Once we've matched a raise we still scan further sink kinds
            # (e.g. another line in the function); fall through.

        # ── B. param_to_return ───────────────────────────────────────────
        ret_m = _RETURN_RE.match(line)
        if ret_m:
            tail = ret_m.group(1) or ""
            sinks.add("return")
            for p in param_set:
                if param_pats[p].search(tail):
                    inputs.add(f"param:{p}")
                    if not _emit(
                        CausalEdge(
                            source=f"param:{p}",
                            sink="return",
                            kind="param_to_return",
                            confidence="high",
                            evidence={"line_number": li, "matched_token": p},
                        )
                    ):
                        break
            if truncated:
                break

        # ── F. global_to_mutation ────────────────────────────────────────
        mut_m = _MUTATION_LINE_RE.match(line)
        if mut_m:
            gname = mut_m.group(1)
            if gname in file_globals and ("=" in stripped or "." in stripped or "[" in stripped):
                # Only count when this line clearly assigns to / mutates
                # the global, not when it merely shadows a local.
                inputs.add(f"global:{gname}")
                sink_label = f"mutation:{gname}"
                sinks.add(sink_label)
                if not _emit(
                    CausalEdge(
                        source=f"global:{gname}",
                        sink=sink_label,
                        kind="global_to_mutation",
                        confidence="medium",
                        evidence={"line_number": li, "matched_token": gname},
                    )
                ):
                    break

        # ── A/C/D. side-effect calls on this line ────────────────────────
        sink_kind: Optional[str] = None
        sink_label: Optional[str] = None
        anchor_match: Optional[re.Match] = None
        # Per-line pre-filter: skip the 30-anchor loop when no anchor
        # substring is present.  Each anchor regex compile is cheap, but
        # 30 * lines * 12K symbols is not.
        if _LINE_PRE_FILTER_RE.search(line):
            for pat, kind, label in _SIDE_EFFECT_ANCHORS:
                m = pat.search(line)
                if m:
                    sink_kind = kind
                    sink_label = _label_for_sink_match(label, kind)
                    anchor_match = m
                    break
        if sink_kind and sink_label:
            sinks.add(sink_label)
            # Carve the parenthesised arg list out of the line (best
            # effort — we don't balance nested parens beyond the immediate
            # call, which is sufficient for the heuristic).
            paren_open = line.find("(", anchor_match.end() - 1) if anchor_match else -1
            arg_blob = ""
            if paren_open >= 0:
                # Walk until matching paren or end-of-line.
                depth = 0
                end_idx = len(line)
                for idx in range(paren_open, len(line)):
                    ch = line[idx]
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            end_idx = idx
                            break
                arg_blob = line[paren_open + 1 : end_idx]

            # A. param_to_effect
            for p in param_set:
                in_args = bool(arg_blob and param_pats[p].search(arg_blob))
                on_line = bool(param_pats[p].search(line))
                if in_args or on_line:
                    inputs.add(f"param:{p}")
                    conf = "high" if in_args else "medium"
                    if not _emit(
                        CausalEdge(
                            source=f"param:{p}",
                            sink=sink_label,
                            kind="param_to_effect",
                            confidence=conf,
                            evidence={
                                "line_number": li,
                                "matched_token": p,
                                "matched_pattern": sink_label,
                            },
                        )
                    ):
                        break
            if truncated:
                break

            # C. global_to_effect
            for g in file_globals:
                if g in param_set:
                    continue  # param shadows global
                in_args = bool(arg_blob and global_pats[g].search(arg_blob))
                on_line = bool(global_pats[g].search(line))
                if in_args or on_line:
                    inputs.add(f"global:{g}")
                    conf = "high" if in_args else "medium"
                    if not _emit(
                        CausalEdge(
                            source=f"global:{g}",
                            sink=sink_label,
                            kind="global_to_effect",
                            confidence=conf,
                            evidence={
                                "line_number": li,
                                "matched_token": g,
                                "matched_pattern": sink_label,
                            },
                        )
                    ):
                        break
            if truncated:
                break

            # D. env_to_effect — link any env read seen earlier in the
            # body to this sink call.  Confidence ``medium`` because the
            # flow is line-distant.
            for env_li, env_key in env_keys_in_body:
                if env_li > li:
                    continue
                if not _emit(
                    CausalEdge(
                        source=f"env:{env_key}",
                        sink=sink_label,
                        kind="env_to_effect",
                        confidence="medium",
                        evidence={
                            "line_number": li,
                            "env_read_line": env_li,
                            "matched_pattern": sink_label,
                        },
                    )
                ):
                    break
            if truncated:
                break

    # Compute graph-level confidence — average / consensus of edges.
    # W596: canonical confidence-LEVEL rank — preserves the pre-W596
    # ``{high:3, medium:2, low:1}`` polarity. Edges only emit canonical
    # labels (see ``confidence="..."`` call sites above), so the rank
    # for ``unknown`` (0) and bogus (-1) never fires in practice; the
    # pre-W596 fallback was ``1`` (treat unknowns as low), and ``max(...,
    # 1)`` here keeps that behaviour for any future label drift.
    if not edges:
        gconf = "low"
    else:
        avg = sum(max(confidence_level_rank(e.confidence, fallback=-1), 1) for e in edges) / len(edges)
        if avg >= 2.5:
            gconf = "high"
        elif avg >= 1.5:
            gconf = "medium"
        else:
            gconf = "low"

    return CausalGraph(
        symbol=sym_name,
        file=file_path,
        edges=edges,
        inputs=sorted(inputs),
        sinks=sorted(sinks),
        truncated=truncated,
        confidence=gconf,
        symbol_id=symbol_id,
        line_start=line_start,
        line_end=line_end,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_causal_graph(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
    side_effects: Optional[list[SideEffectClassification]] = None,
) -> list[CausalGraph]:
    """Build a causal graph for each function/method/constructor.

    Args:
        conn: Read-only DB connection.
        symbol_name: If given, only classify symbols matching this
            ``name`` or ``qualified_name``.
        limit: Optional cap on symbols scanned.
        side_effects: Optional pre-computed side-effects classifications;
            we use them to identify the symbols worth scanning AND to
            tag each graph's overall side-effect summary.

    Returns:
        List of :class:`CausalGraph`.  Order: by file then symbol id.
    """
    # 1) Pull candidate symbols (filtered to functions / methods / ctors).
    if symbol_name:
        rows = conn.execute(
            """
            SELECT s.id, s.name, s.qualified_name, s.line_start, s.line_end,
                   s.signature, s.kind, f.path AS file_path
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            WHERE (s.name = ? OR s.qualified_name = ?)
              AND s.kind IN ('function', 'method', 'constructor')
            """,
            (symbol_name, symbol_name),
        ).fetchall()
    else:
        q = """
            SELECT s.id, s.name, s.qualified_name, s.line_start, s.line_end,
                   s.signature, s.kind, f.path AS file_path
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            WHERE s.kind IN ('function', 'method', 'constructor')
            ORDER BY f.path, s.id
        """
        if limit and limit > 0:
            q += f" LIMIT {int(limit)}"
        rows = conn.execute(q).fetchall()

    if not rows:
        return []

    # Side-effects index by symbol_id (optional).
    se_by_id: dict[int, SideEffectClassification] = {}
    if side_effects:
        for se in side_effects:
            if se.symbol_id:
                se_by_id[se.symbol_id] = se

    try:
        repo_root = find_project_root()
    except Exception as exc:
        # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — a
        # missing project root downgrades per-symbol source slicing to a
        # CWD-relative read; surface the lineage so callers see the
        # degraded resolution instead of inferring it from empty causal
        # graphs (mirrors classify_side_effects / classify_idempotency).
        warnings.warn(
            f"find_project_root() failed in classify_causal_graph "
            f"({type(exc).__name__}: {exc}); falling back to Path('.') — "
            "per-symbol source slices may be empty if CWD isn't the repo root",
            category=RuntimeWarning,
            stacklevel=2,
        )
        repo_root = Path(".")

    # Group rows by file so we read each file once.
    rows_by_file: dict[str, list] = {}
    for r in rows:
        rows_by_file.setdefault(r["file_path"], []).append(r)

    out: list[CausalGraph] = []
    for file_path, file_rows in rows_by_file.items():
        try:
            p = repo_root / file_path
            if p.exists():
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    all_text = f.read()
                all_lines = all_text.splitlines(keepends=True)
            else:
                all_text = ""
                all_lines = []
        except OSError as exc:
            # Loud-fallback per CLAUDE.md §"Make fallback chains loud" — an
            # unreadable file yields zero causal edges that look identical
            # to a genuinely flow-free function. Surface the lineage
            # (rate-limited per-scope; visible under ROAM_VERBOSE=1).
            log_swallowed(f"world_model.causal_graph:file_read:{file_path}", exc)
            all_text = ""
            all_lines = []

        file_globals = _global_names(all_text) if all_text else set()

        for r in file_rows:
            sid = r["id"]
            ls = r["line_start"] or 1
            le = r["line_end"] or ls
            if all_lines:
                body = "".join(all_lines[max(0, ls - 1) : le])
            else:
                body = ""

            # Parameters — prefer signature column, fall back to body parse.
            params = _extract_params_from_signature(r["signature"])
            if not params and body:
                params = _extract_params_from_body(body)

            graph = _scan_one(
                sym_name=r["qualified_name"] or r["name"],
                file_path=file_path,
                body_text=body,
                params=params,
                file_globals=file_globals,
                sink_se=se_by_id.get(sid),
                line_start=ls,
                symbol_id=sid,
                line_end=le,
            )
            # Always surface the parameters as inputs even if no edges
            # were emitted — makes "pure function" envelopes informative.
            for p in params:
                if f"param:{p}" not in graph.inputs:
                    graph.inputs.append(f"param:{p}")
            graph.inputs.sort()
            out.append(graph)
    return out


__all__ = [
    "CAUSAL_KINDS",
    "MAX_EDGES_PER_SYMBOL",
    "CausalEdge",
    "CausalGraph",
    "classify_causal_graph",
]
