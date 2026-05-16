"""Codebase-law mining.

Discovers a repo's unwritten rules from the indexed DB. Returns a list
of :class:`Law` dataclasses that the checker (:mod:`roam.laws.checker`)
and the YAML serializer (:mod:`roam.laws.serializer`) consume.

Mining strategies
-----------------
A. **Naming laws** — leverages ``conventions_helper.compute_conventions``
   (the canonical detector, shipped by Fix G). For any kind where one
   convention is the dominant style at >= ``min_conformance_pct``, emit
   a law with that style. ``confidence: high`` when the dominant style
   is at >= 90 %, ``medium`` at >= 80 %, otherwise ``low``.

B. **Import-layering laws** — queries ``file_edges`` (kind = ``imports``)
   grouped by the top-level src-directory of source / target. When a
   source directory imports from a target directory in >= 95 % of its
   cross-directory imports, emit ``A imports from B (95%)``.

C. **Test-coverage laws** — for each public-symbol kind, computes the
   fraction that have a matching ``test_*`` file. If the fraction
   crosses the threshold, emit ``public <kind>s must be tested by
   test_<name>*``. This is intentionally coarse-grained: we only emit
   a law when conformance is overwhelmingly high so it doesn't fire on
   small / experimental codebases.

D. **Error-handling laws** — STUB for v1. Returns ``[]``. Documented in
   :func:`_mine_error_laws` so a follow-up can extend it.

E. **Co-change laws** — STUB for v1. Returns ``[]``. Documented in
   :func:`_mine_cochange_laws` so a follow-up can extend it.

The ``rule`` dict embedded in each :class:`Law` is intentionally
shaped so R18's policy DSL can consume it directly.  Each ``kind``
defines its own minimal-shape rule:

* ``naming``     -> ``{"kind": "naming", "symbol_kind": "function",
                       "style": "snake_case"}``
* ``import``     -> ``{"kind": "import", "from_dir": "src/handlers",
                       "to_dir": "src/db"}``
* ``testing``    -> ``{"kind": "testing", "symbol_kind": "function",
                       "test_pattern": "test_*"}``
* ``errors``     -> ``{"kind": "errors", "scope_pattern": "*_handler",
                       "must_catch": ["IntegrityError"]}``
* ``co_change``  -> ``{"kind": "co_change", "trigger": "models.py",
                       "expects": ["migrations.py"]}``
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Law:
    """A single discovered law.

    Attributes
    ----------
    id
        Stable kebab-case identifier — ``snake_case_functions``,
        ``imports_src_handlers_to_src_db``. Round-trip safe.
    kind
        One of ``naming`` / ``import`` / ``testing`` / ``errors`` /
        ``co_change``.
    description
        Human-readable sentence — used by ``roam laws list``.
    evidence
        Dict carrying ``sample_size``, ``conformance_pct``, and a small
        list of ``examples`` (verbatim names / paths). Sized to fit in a
        terminal pane.
    severity
        ``advisory`` (default) / ``warning`` / ``blocker``.
    confidence
        ``low`` / ``medium`` / ``high``. Drives how aggressively the
        checker treats a violation when ``--strict`` is set.
    rule
        Machine-readable rule spec. Re-usable by R18's policy DSL — the
        dict shape is documented in the module docstring.
    """

    id: str
    kind: str
    description: str
    evidence: dict[str, Any] = field(default_factory=dict)
    severity: str = "advisory"
    confidence: str = "medium"
    rule: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation suitable for YAML / JSON."""
        return asdict(self)


@dataclass
class Violation:
    """A single rule violation discovered by :func:`check_laws`."""

    law_id: str
    kind: str
    severity: str
    confidence: str
    message: str
    file: str = ""
    line: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


# Conformance thresholds. Pulled out as module-level constants so tests and
# future tuning don't have to dig into individual functions.
_MIN_CONFORMANCE_PCT = 70.0  # below this we emit nothing
_HIGH_CONFIDENCE_PCT = 90.0  # >= this -> confidence: high
_MED_CONFIDENCE_PCT = 80.0  # >= this -> confidence: medium
_MIN_SAMPLE_SIZE = 5  # below this the signal is too noisy


def mine_laws(
    conn,
    *,
    min_conformance_pct: float = _MIN_CONFORMANCE_PCT,
    min_sample_size: int = _MIN_SAMPLE_SIZE,
    top: Optional[int] = None,
) -> list[Law]:
    """Discover laws from the indexed codebase at *conn*.

    Parameters
    ----------
    conn
        Open SQLite connection (readonly is fine).
    min_conformance_pct
        Below this percentage a candidate law is dropped — the signal
        isn't strong enough.
    min_sample_size
        Below this sample count a candidate law is dropped — the signal
        is too noisy.
    top
        If set, keep only the *top* highest-confidence laws (after
        sorting by ``confidence`` desc + ``conformance_pct`` desc).

    Returns
    -------
    list[Law]
        Ordered by confidence (high -> low), then by sample size.
    """
    laws: list[Law] = []

    laws.extend(_mine_naming_laws(conn, min_conformance_pct, min_sample_size))
    laws.extend(_mine_import_laws(conn, min_conformance_pct, min_sample_size))
    laws.extend(_mine_testing_laws(conn, min_conformance_pct, min_sample_size))
    laws.extend(_mine_error_laws(conn, min_conformance_pct, min_sample_size))
    laws.extend(_mine_cochange_laws(conn, min_conformance_pct, min_sample_size))

    laws.sort(key=_law_sort_key, reverse=True)
    if top is not None and top > 0:
        laws = laws[:top]
    return laws


def _law_sort_key(law: Law) -> tuple[int, float, int]:
    """Stable sort key: higher confidence first, then higher conformance,
    then bigger sample.

    W1299: source confidence rank from the canonical helper at
    :func:`roam.output.confidence.confidence_level_rank` rather than an
    inline dict (W596 migration target). Polarity is preserved
    (high=3 / medium=2 / low=1, unknown=0).
    """
    from roam.output.confidence import confidence_level_rank

    conf_rank = confidence_level_rank(law.confidence, fallback=0)
    conformance = float(law.evidence.get("conformance_pct", 0))
    sample = int(law.evidence.get("sample_size", 0))
    return (conf_rank, conformance, sample)


def _confidence_from_pct(pct: float) -> str:
    if pct >= _HIGH_CONFIDENCE_PCT:
        return "high"
    if pct >= _MED_CONFIDENCE_PCT:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Strategy A — naming laws
# ---------------------------------------------------------------------------


def _mine_naming_laws(conn, min_pct: float, min_sample: int) -> list[Law]:
    """Emit a naming law per symbol kind that has a dominant style.

    Defers to the canonical conventions helper so all five consumers of
    naming detection agree on the answer.
    """
    try:
        from roam.commands.conventions_helper import compute_conventions
    except Exception:
        return []

    try:
        result = compute_conventions(conn, min_majority_pct=min_pct)
    except Exception:
        return []

    laws: list[Law] = []
    by_kind = result.get("by_kind", {}) or {}
    outliers_by_kind: dict[str, list[dict]] = {}
    for o in result.get("outliers", []) or []:
        outliers_by_kind.setdefault(o.get("kind", ""), []).append(o)

    for kind, info in sorted(by_kind.items()):
        total = info.get("total", 0)
        pct = info.get("pct", 0)
        style = info.get("style", "")
        if total < min_sample or pct < min_pct or not style:
            continue

        examples = _naming_examples(conn, kind, style, limit=3)
        outlier_count = len(outliers_by_kind.get(kind, []))

        law = Law(
            id=_safe_id(f"{style.lower()}_{_kind_plural(kind)}"),
            kind="naming",
            description=f"{_kind_plural(kind).capitalize()} must be {style}",
            evidence={
                "sample_size": total,
                "conformance_pct": pct,
                "style": style,
                "breakdown": info.get("breakdown", {}),
                "outlier_count": outlier_count,
                "examples": examples,
            },
            severity="advisory",
            confidence=_confidence_from_pct(pct),
            rule={
                "kind": "naming",
                "symbol_kind": kind,
                "style": style,
            },
        )
        laws.append(law)
    return laws


def _naming_examples(conn, kind: str, style: str, *, limit: int = 3) -> list[str]:
    """Pull a few conforming names so ``laws explain`` has something
    concrete to show. We pick the most-imported / oldest symbols (any
    deterministic ordering will do); we just need representative ones."""
    try:
        rows = conn.execute(
            "SELECT name FROM symbols WHERE kind = ? ORDER BY id LIMIT 50",
            (kind,),
        ).fetchall()
    except Exception:
        return []
    try:
        from roam.commands.cmd_conventions import classify_case
    except Exception:
        return [r["name"] for r in rows[:limit]]
    examples: list[str] = []
    for r in rows:
        n = r["name"] if hasattr(r, "keys") else r[0]
        if classify_case(n) == style:
            examples.append(n)
            if len(examples) >= limit:
                break
    return examples


# ---------------------------------------------------------------------------
# Strategy B — import-layering laws
# ---------------------------------------------------------------------------


def _mine_import_laws(conn, min_pct: float, min_sample: int) -> list[Law]:
    """Emit a law per (source_dir, target_dir) that dominates the
    source_dir's cross-directory imports.

    Algorithm
    ---------
    1. Join ``file_edges`` (kind = ``imports``) with ``files`` to get
       source path + target path.
    2. Bucket each edge by its top-level src directory (``src/A`` ->
       ``src/A``, ``src/A/sub`` -> ``src/A``).
    3. For each source bucket, sum the edges going to each distinct
       target bucket. If one target bucket accounts for >= ``min_pct``
       of that source's outbound cross-bucket imports, emit a law.
    """
    laws: list[Law] = []

    try:
        rows = conn.execute(
            """
            SELECT
                src.path AS src_path,
                tgt.path AS tgt_path
            FROM file_edges fe
            JOIN files src ON fe.source_file_id = src.id
            JOIN files tgt ON fe.target_file_id = tgt.id
            WHERE fe.kind = 'imports'
            """
        ).fetchall()
    except Exception:
        return []

    # source_bucket -> target_bucket -> count
    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        src_bucket = _import_bucket(r["src_path"])
        tgt_bucket = _import_bucket(r["tgt_path"])
        if not src_bucket or not tgt_bucket or src_bucket == tgt_bucket:
            continue
        counts.setdefault(src_bucket, {})
        counts[src_bucket][tgt_bucket] = counts[src_bucket].get(tgt_bucket, 0) + 1

    for src_bucket, tgt_counts in sorted(counts.items()):
        total = sum(tgt_counts.values())
        if total < min_sample:
            continue
        # Dominant target
        dominant_tgt, dominant_count = max(tgt_counts.items(), key=lambda x: x[1])
        pct = round(100 * dominant_count / total, 1) if total else 0
        if pct < min_pct:
            continue

        examples = _import_examples(conn, src_bucket, dominant_tgt, limit=3)
        law = Law(
            id=_safe_id(f"imports_{src_bucket}_to_{dominant_tgt}"),
            kind="import",
            description=(
                f"Files in {src_bucket}/ import from {dominant_tgt}/ ({pct:.0f}% of {total} cross-directory imports)"
            ),
            evidence={
                "sample_size": total,
                "conformance_pct": pct,
                "from_dir": src_bucket,
                "to_dir": dominant_tgt,
                "breakdown": dict(sorted(tgt_counts.items(), key=lambda x: -x[1])[:8]),
                "examples": examples,
            },
            severity="advisory",
            confidence=_confidence_from_pct(pct),
            rule={
                "kind": "import",
                "from_dir": src_bucket,
                "to_dir": dominant_tgt,
            },
        )
        laws.append(law)
    return laws


def _import_bucket(path: str | None) -> str:
    """Return the top-two-*directory*-segment bucket for a file path.

    The bucket excludes the file basename so every file under
    ``tests/`` ends up in the same ``tests`` bucket rather than its own
    per-file silo. Examples::

        src/handlers/auth.py        -> src/handlers
        src/roam/commands/foo.py    -> src/roam        (skip the deepest dir
                                                        — too granular)
        tests/test_foo.py            -> tests
        app.py                       -> ""             (no directory)
        scripts/setup.py             -> scripts
    """
    if not path:
        return ""
    norm = path.replace("\\", "/").lstrip("./")
    parts = norm.split("/")
    # Drop the basename — buckets are directories only.
    dirs = parts[:-1]
    if not dirs:
        return ""
    # Keep at most the top two directory segments. Anything deeper makes
    # the bucket so specific the law has no generalisation value.
    return "/".join(dirs[:2])


def _import_examples(conn, src_bucket: str, tgt_bucket: str, *, limit: int = 3) -> list[str]:
    """Return concrete ``src_file -> tgt_file`` pairs for the law's
    evidence panel."""
    try:
        like_src = src_bucket.replace("\\", "/") + "/%"
        like_tgt = tgt_bucket.replace("\\", "/") + "/%"
        rows = conn.execute(
            """
            SELECT src.path AS s, tgt.path AS t
            FROM file_edges fe
            JOIN files src ON fe.source_file_id = src.id
            JOIN files tgt ON fe.target_file_id = tgt.id
            WHERE fe.kind = 'imports'
              AND (src.path LIKE ? OR src.path LIKE ?)
              AND (tgt.path LIKE ? OR tgt.path LIKE ?)
            LIMIT ?
            """,
            (like_src, like_src.replace("/", "\\"), like_tgt, like_tgt.replace("/", "\\"), limit * 4),
        ).fetchall()
    except Exception:
        return []
    examples: list[str] = []
    for r in rows:
        s = (r["s"] or "").replace("\\", "/")
        t = (r["t"] or "").replace("\\", "/")
        if s.startswith(src_bucket + "/") and t.startswith(tgt_bucket + "/"):
            examples.append(f"{s} -> {t}")
            if len(examples) >= limit:
                break
    return examples


# ---------------------------------------------------------------------------
# Strategy C — test-coverage laws
# ---------------------------------------------------------------------------


def _mine_testing_laws(conn, min_pct: float, min_sample: int) -> list[Law]:
    """Emit a law when a high fraction of public symbols have a
    matching test file.

    We look at each ``function`` / ``class`` whose ``visibility`` is not
    ``private`` and check if the indexed file set contains a test file
    whose basename matches one of the heuristic patterns.

    We split by symbol kind so the codebase can pass on functions and
    fail on classes (or vice versa) — each gets its own law.
    """
    try:
        from roam.commands.changed_files import is_test_file
    except Exception:
        return []

    laws: list[Law] = []

    # Cache the set of test file basenames so the per-symbol check is O(1).
    try:
        test_files = {
            (r["path"] or "").replace("\\", "/")
            for r in conn.execute("SELECT path FROM files").fetchall()
            if is_test_file(r["path"])
        }
    except Exception:
        return []
    if not test_files:
        # No tests -> nothing to mine.
        return []
    test_basenames = {p.rsplit("/", 1)[-1].lower() for p in test_files}

    for kind in ("function", "class"):
        try:
            rows = conn.execute(
                """
                SELECT s.name AS name, f.path AS path
                FROM symbols s
                JOIN files f ON s.file_id = f.id
                WHERE s.kind = ?
                  AND COALESCE(s.visibility, 'public') = 'public'
                  AND COALESCE(s.is_exported, 1) = 1
                """,
                (kind,),
            ).fetchall()
        except Exception:
            continue

        # Only count source-side symbols (skip those that are themselves
        # in test files — they bias the coverage upward).
        public_names: list[tuple[str, str]] = []
        for r in rows:
            name = r["name"] or ""
            path = (r["path"] or "").replace("\\", "/")
            if not name or name.startswith("_"):
                continue
            if is_test_file(path):
                continue
            public_names.append((name, path))

        if len(public_names) < min_sample:
            continue

        covered = 0
        missing_examples: list[str] = []
        for name, path in public_names:
            if _has_matching_test(name, test_basenames):
                covered += 1
            elif len(missing_examples) < 5:
                missing_examples.append(f"{name} ({path})")

        total = len(public_names)
        pct = round(100 * covered / total, 1) if total else 0
        if pct < min_pct:
            continue

        law = Law(
            id=_safe_id(f"public_{_kind_plural(kind)}_must_be_tested"),
            kind="testing",
            description=(
                f"Public {_kind_plural(kind)} should have a matching test file"
                f" ({pct:.0f}% of {total} public {_kind_plural(kind)} do today)"
            ),
            evidence={
                "sample_size": total,
                "conformance_pct": pct,
                "covered": covered,
                "uncovered": total - covered,
                "missing_examples": missing_examples,
                "test_pattern": "test_<name>* / <name>_test.* / <name>.test.*",
            },
            severity="advisory",
            confidence=_confidence_from_pct(pct),
            rule={
                "kind": "testing",
                "symbol_kind": kind,
                "test_pattern": "test_*",
            },
        )
        laws.append(law)
    return laws


def _has_matching_test(name: str, test_basenames: set[str]) -> bool:
    """Heuristic match: does any indexed test file's basename mention
    *name*?"""
    if not name:
        return False
    lower = name.lower()
    # Common patterns across ecosystems.
    candidates = (
        f"test_{lower}.py",
        f"{lower}_test.py",
        f"{lower}.test.js",
        f"{lower}.test.ts",
        f"{lower}.test.tsx",
        f"{lower}.spec.js",
        f"{lower}.spec.ts",
        f"{lower}_test.go",
        f"{lower}_spec.rb",
    )
    if any(c in test_basenames for c in candidates):
        return True
    # Last-resort: substring scan. Cheap on the cached set.
    needle = lower
    for bn in test_basenames:
        if needle in bn:
            return True
    return False


# ---------------------------------------------------------------------------
# Strategy D — error-handling laws (v1 STUB)
# ---------------------------------------------------------------------------


def _mine_error_laws(conn, min_pct: float, min_sample: int) -> list[Law]:
    """Discover error-handling conventions (v1 stub).

    Planned: scan symbol bodies for ``except`` / ``catch`` clauses and
    correlate with the symbol's name prefix / suffix. For now we return
    no laws so the checker can't trip on unmined signal; the function is
    kept as the integration seam for a follow-up.
    """
    return []


# ---------------------------------------------------------------------------
# Strategy E — co-change laws (v1 STUB)
# ---------------------------------------------------------------------------


def _mine_cochange_laws(conn, min_pct: float, min_sample: int) -> list[Law]:
    """Discover git co-change invariants (v1 stub).

    Planned: walk ``git_cochange`` for file pairs that change together
    in >= 75 % of their commits over the trailing 6 months. The data is
    already indexed (see CLAUDE.md ``git_stats``) — we just haven't wired
    the policy emission yet because the false-positive rate on small
    repos is high.
    """
    return []


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _kind_plural(kind: str) -> str:
    """English-ish pluralisation for the kinds we mine.

    Kept explicit so we never emit "classs" or "propertys" in the
    public verdict / law id.
    """
    irregulars = {
        "class": "classes",
        "property": "properties",
    }
    return irregulars.get(kind, f"{kind}s")


def _safe_id(raw: str) -> str:
    """Normalise an id to a stable kebab-case slug.

    Replaces path separators and spaces with underscores, drops any
    char outside ``[a-z0-9_]``, collapses consecutive underscores.
    """
    out_chars: list[str] = []
    last_underscore = False
    for ch in raw.lower():
        if ch.isalnum():
            out_chars.append(ch)
            last_underscore = False
        else:
            if not last_underscore:
                out_chars.append("_")
                last_underscore = True
    slug = "".join(out_chars).strip("_")
    return slug or "law"
