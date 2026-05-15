"""Canonical naming-conventions detector used across roam-code commands.

This module is the **single source of truth** for naming-convention detection.
Before Fix G, 5 commands (`describe`, `understand`, `minimap`, `preflight`,
`conventions`) each computed naming conventions differently and reported
conflicting verdicts on the same codebase. They now all delegate here.

Why one detector?
-----------------
The 212-eval dogfood corpus (``internal/dogfood/SYNTHESIS-2026-05-12.md`` Pattern 4)
documented exactly this divergence:

* ``describe`` / ``understand`` correctly reported per-kind percentage breakdowns
* ``minimap`` collapsed everything to one misleading label
* ``preflight`` produced 45 violations, many false positives
* ``conventions`` (standalone) reported 9014 outliers (51% of identifiers!),
  many in ``.github/workflows/`` and ``docs/``

Fix G consolidates all five onto ``compute_conventions()`` below, with a
default exclude list for non-code identifier sources.

Public API
----------
* ``compute_conventions(conn, exclude_paths=None) -> ConventionsResult`` —
  the canonical detector. Returns per-kind percentage breakdowns plus
  per-(language_family, kind_group) breakdowns and outliers.
* ``DEFAULT_EXCLUDE_PREFIXES`` — paths excluded by default
  (``.github/``, ``.claude/``, ``docs/``, ``dist/``, ``build/``,
  ``node_modules/``, ``vendor/``, ``__pycache__/``).
* ``is_excluded_path(path, prefixes)`` — boolean helper.

The result dict is intentionally a superset of what each caller needs so
the same call serves describe's compact summary, conventions' outlier
enumeration, and preflight's >70% majority gate.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

# Re-export the case-style primitives so callers only need to import from
# this module. ``classify_case``, ``_group_for_kind``, ``_MIN_NAME_LEN``,
# ``_SKIP_NAMES``, and the language-family / language-kind-default tables
# all live in ``cmd_conventions`` for historic reasons; this module is the
# only place that should aggregate them.
from roam.commands.cmd_conventions import (
    _LANGUAGE_KIND_DEFAULTS,
    _MIN_NAME_LEN,
    _SKIP_NAMES,
    NON_CODE_CONVENTION_LANGUAGES,
    _detect_affixes,
    _group_for_kind,
    _language_family,
    classify_case,
    is_python_type_alias_signature,
    is_upper_snake_constant_name,
)

# ---------------------------------------------------------------------------
# Path exclusion
# ---------------------------------------------------------------------------

# Default prefixes that should NOT contribute identifiers to convention
# detection. These are typically non-source-code paths (CI workflows,
# docs, vendored deps, build artifacts) whose identifiers don't reflect
# the codebase's coding conventions.
DEFAULT_EXCLUDE_PREFIXES: tuple[str, ...] = (
    ".github/",
    ".claude/",
    ".roam/",
    "docs/",
    "doc/",
    "dist/",
    "build/",
    "node_modules/",
    "vendor/",
    "__pycache__/",
    ".venv/",
    "venv/",
    "site-packages/",
    "target/",  # rust/java build
    ".next/",  # next.js build
    ".cache/",
    # W162: detector-training corpora and fixture files are deliberately
    # malformed (camelCase methods in a Kotlin fixture, mismatched
    # property casing, etc.) — they exist to exercise the EXTRACTORS,
    # not to model the project's own conventions. Reading them as
    # conventions data was the largest false-positive source on
    # roam-code (27/41 findings, 100% noise) per the W149 audit. The
    # ``fixtures/`` prefix catches both ``tests/fixtures/`` and any
    # nested ``<lang>/tests/fixtures/`` layout because
    # ``is_excluded_path`` matches the prefix as a path component, not
    # just at the string head.
    "tests/fixtures/",
    "fixtures/",
    "testdata/",
    "test_data/",
    "tests/data/",
    # Code-generation templates (``src/roam/templates/`` in this repo)
    # produce YAML / Jinja files whose identifiers are framework
    # vocabulary (``pr``, ``pool``, ``checkout``, ``setup-python``),
    # not project conventions. Excluding ``templates/`` keeps them out
    # of the empirical pool.
    "templates/",
)


def is_excluded_path(path: str, exclude_prefixes: tuple[str, ...] | None = None) -> bool:
    """Return True if *path* starts with (or contains) any excluded prefix.

    Matches both top-level and nested locations — e.g. a deeply nested
    ``packages/foo/node_modules/bar.js`` is excluded.
    """
    if not path:
        return False
    norm = path.replace("\\", "/")
    prefixes = exclude_prefixes if exclude_prefixes is not None else DEFAULT_EXCLUDE_PREFIXES
    for prefix in prefixes:
        if norm.startswith(prefix) or f"/{prefix}" in f"/{norm}":
            return True
    return False


# ---------------------------------------------------------------------------
# Canonical detector
# ---------------------------------------------------------------------------

# Symbol kinds we sample for convention detection. Matches what
# cmd_conventions analysed historically, so the consolidated detector
# can replace it without changing the surface for the conventions
# standalone command.
_ANALYZED_KINDS = (
    "function",
    "method",
    "class",
    "interface",
    "struct",
    "trait",
    "enum",
    "variable",
    "constant",
    "property",
    "field",
    "type_alias",
)


def _kind_label(kind: str) -> str:
    """Human-readable plural for a kind, used in describe/understand text."""
    if kind == "class":
        return "classes"
    return f"{kind}s"


def compute_conventions(
    conn,
    exclude_paths: tuple[str, ...] | None = None,
    *,
    min_majority_pct: float = 70.0,
) -> dict[str, Any]:
    """Compute naming conventions for the indexed codebase.

    Parameters
    ----------
    conn
        Open SQLite connection (readonly is fine).
    exclude_paths
        Tuple of path prefixes to exclude. ``None`` means
        ``DEFAULT_EXCLUDE_PREFIXES``. Pass an empty tuple ``()`` to
        disable exclusion entirely.
    min_majority_pct
        Threshold at which a kind has a "dominant" convention.
        Defaults to 70 — matches the rule from LAW 5 of agi-in-md
        (Pattern 4) that preflight should flag only deviations from a
        kind whose majority convention is >70%.

    Returns
    -------
    dict
        A canonical conventions result with the following keys::

            {
              "by_kind": {
                "function": {
                    "style": "snake_case",
                    "pct": 93,            # int percent of dominant
                    "total": 1450,
                    "breakdown": {"snake_case": 1348, "camelCase": 102},
                    "has_majority": True,  # pct >= min_majority_pct
                },
                ...
              },
              "by_family_group": {           # used by `roam conventions`
                "python/functions": {...},
                "js/classes": {...},
              },
              "outliers": [                  # symbols not matching the
                {                            # dominant style for their
                  "name": "myFunc",          # (family, group)
                  "kind": "function",
                  "actual_style": "camelCase",
                  "expected_style": "snake_case",
                  "expected_source": "community_default",
                  "language_family": "python",
                  "file": "src/foo.py",
                  "line": 42,
                },
                ...
              ],
              "affixes": {"prefixes": [...], "suffixes": [...]},
              "total_analyzed": 1820,         # symbols with classifiable style
              "total_excluded": 234,          # symbols filtered by exclude_paths
              "exclude_prefixes": (...),     # the prefixes actually applied
              "min_majority_pct": 70.0,
            }

    The ``by_kind`` view is what ``describe``, ``understand``, and
    ``minimap`` consume — a flat per-kind dict with percentages.

    The ``by_family_group`` view is what the standalone ``conventions``
    command consumes — it splits by language family so SQL snake_case
    doesn't show up as a violation against a TypeScript-dominant codebase.

    ``outliers`` are computed against ``by_family_group`` (the most
    precise grouping) so a follow-up renderer can present them with the
    correct expected style.
    """
    exclude = exclude_paths if exclude_paths is not None else DEFAULT_EXCLUDE_PREFIXES

    kinds_csv = ",".join(f"'{k}'" for k in _ANALYZED_KINDS)
    all_symbols = conn.execute(
        f"""
        SELECT s.name, s.kind, s.signature, s.line_start, f.path as file_path,
               COALESCE(f.language, '') as language
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.kind IN ({kinds_csv})
        """
    ).fetchall()

    # Per-kind counters (codebase-wide, what describe/understand surface)
    by_kind_counts: dict[str, Counter] = defaultdict(Counter)
    # Per-(family, group) counters (what conventions standalone surfaces)
    by_family_group_counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    symbol_details: list[dict] = []
    all_names: list[str] = []
    excluded_count = 0

    for sym in all_symbols:
        path = sym["file_path"] or ""
        if is_excluded_path(path, exclude):
            excluded_count += 1
            continue
        language = (sym["language"] or "").lower()
        # W162: data / markup / template languages don't have meaningful
        # code-style conventions. Their identifiers (YAML keys, JSON
        # property names, markdown headings) shouldn't drag everything
        # else into the ``language_family="unknown"`` empirical bucket.
        if language in NON_CODE_CONVENTION_LANGUAGES:
            excluded_count += 1
            continue
        name = sym["name"]
        kind = sym["kind"]
        style = classify_case(name)
        if not style:
            continue
        # W162: re-route names that are PEP-8 constants into the
        # ``constants`` group regardless of the extractor's declared
        # kind. The python extractor emits ``kind="property"`` for
        # class-level ``VERSION = "1.0.0"`` declarations on
        # ``LanguageExtractor`` and ``LanguageBridge`` base classes —
        # these are constants per PEP 8, not variables. Without this
        # re-route they land in the ``python/variables`` group and get
        # flagged as ``snake_case`` outliers (false positive).
        if is_upper_snake_constant_name(name) and kind in (
            "variable",
            "property",
            "field",
            "constant",
        ):
            group = "constants"
        else:
            group = _group_for_kind(kind)
        family = _language_family(sym["language"])

        # W162: PEP 484 / PEP 585 / PEP 613 / PEP 695 type aliases and
        # ``NewType`` constructions are stored by the python extractor
        # as ``kind="variable"`` with a PascalCase name (e.g.
        # ``PathLike = Union[str, os.PathLike]``,
        # ``LockMode = NewType("LockMode", str)``,
        # ``CommandTarget = tuple[str, str]``). PEP 8 / PEP 484 say
        # PascalCase IS the correct case for type aliases, so flagging
        # them is wrong. We skip them entirely from convention
        # detection — they're a third class (alongside "variable" and
        # "constant") that just happens to share the ``variable``
        # extractor kind.
        if (
            family == "python"
            and kind in ("variable", "constant")
            and style == "PascalCase"
            and is_python_type_alias_signature(sym["signature"], name)
        ):
            excluded_count += 1
            continue

        by_kind_counts[kind][style] += 1
        by_family_group_counts[(family, group)][style] += 1
        symbol_details.append(
            {
                "name": name,
                "kind": kind,
                "group": group,
                "language_family": family,
                "style": style,
                "file": path,
                "line": sym["line_start"],
            }
        )
        if len(name) >= _MIN_NAME_LEN and name not in _SKIP_NAMES:
            all_names.append(name)

    by_kind = _build_by_kind(by_kind_counts, min_majority_pct=min_majority_pct)
    by_family_group = _build_by_family_group(by_family_group_counts)
    outliers = _find_outliers(symbol_details, by_family_group)
    affixes = _detect_affixes(all_names)

    return {
        "by_kind": by_kind,
        "by_family_group": by_family_group,
        "outliers": outliers,
        "affixes": affixes,
        "total_analyzed": len(symbol_details),
        "total_excluded": excluded_count,
        "exclude_prefixes": tuple(exclude),
        "min_majority_pct": min_majority_pct,
    }


def _build_by_kind(
    by_kind_counts: dict[str, Counter],
    *,
    min_majority_pct: float,
) -> dict[str, dict]:
    """Build the flat per-kind summary that describe / understand /
    minimap consume."""
    result: dict[str, dict] = {}
    for kind, counter in by_kind_counts.items():
        total = sum(counter.values())
        if total == 0:
            continue
        dominant_style, dominant_count = counter.most_common(1)[0]
        pct = round(dominant_count * 100 / total) if total else 0
        result[kind] = {
            "style": dominant_style,
            "pct": pct,
            "total": total,
            "breakdown": dict(counter.most_common()),
            "has_majority": pct >= min_majority_pct,
            "label": _kind_label(kind),
        }
    return result


def _build_by_family_group(
    group_cases: dict[tuple[str, str], Counter],
) -> dict[str, dict]:
    """Build the (language_family, kind_group) view that the standalone
    conventions command surfaces. Documented community defaults beat the
    empirical mode so a SQL-heavy project's bad habits don't get treated
    as "the convention".

    Mirrors the historic ``_build_naming_summary`` in ``cmd_conventions``
    so the standalone command's JSON envelope shape doesn't change.
    """
    summary: dict[str, dict] = {}
    for (family, group), counter in sorted(group_cases.items()):
        total = sum(counter.values())
        if total == 0:
            continue
        empirical_style, empirical_count = counter.most_common(1)[0]
        documented = _LANGUAGE_KIND_DEFAULTS.get((family, group))
        dominant_style = documented or empirical_style
        dominant_count = counter.get(dominant_style, empirical_count)
        pct = round(100 * dominant_count / total, 1) if total else 0
        key = f"{family}/{group}" if family != "unknown" else group
        summary[key] = {
            "dominant_style": dominant_style,
            "expected_source": "community_default" if documented else "empirical",
            "dominant_count": dominant_count,
            "total": total,
            "percent": pct,
            "language_family": family,
            "kind_group": group,
            "breakdown": dict(counter.most_common()),
        }
    return summary


def _find_outliers(
    symbol_details: list[dict],
    by_family_group: dict[str, dict],
) -> list[dict]:
    """Symbols whose case style doesn't match the dominant style for
    their (language_family, kind_group)."""
    outliers: list[dict] = []
    for det in symbol_details:
        family = det["language_family"]
        group = det["group"]
        key = f"{family}/{group}" if family != "unknown" else group
        grp_info = by_family_group.get(key)
        if grp_info and det["style"] != grp_info["dominant_style"]:
            outliers.append(
                {
                    "name": det["name"],
                    "kind": det["kind"],
                    "language_family": family,
                    "actual_style": det["style"],
                    "expected_style": grp_info["dominant_style"],
                    "expected_source": grp_info["expected_source"],
                    "file": det["file"],
                    "line": det["line"],
                }
            )
    return outliers


# ---------------------------------------------------------------------------
# Convenience renderers — used by describe / understand / minimap
# ---------------------------------------------------------------------------


def summarize_by_kind_text(by_kind: dict[str, dict]) -> list[str]:
    """Render the per-kind summary as the compact lines describe /
    understand emit. Returns a list of strings like
    ``"function: 93% snake_case"``.
    """
    out: list[str] = []
    # Preserve a stable order so output is deterministic across calls.
    ordering = ("function", "class", "method", "variable", "constant", "property", "field", "interface", "struct")
    seen = set()
    for kind in ordering:
        if kind in by_kind:
            info = by_kind[kind]
            out.append(f"{kind}: {info['pct']}% {info['style']}")
            seen.add(kind)
    for kind, info in sorted(by_kind.items()):
        if kind in seen:
            continue
        out.append(f"{kind}: {info['pct']}% {info['style']}")
    return out


def short_conventions_string(by_kind: dict[str, dict], *, min_pct: int = 70) -> str:
    """Render the compact one-liner used by minimap and the agent-prompt
    section of describe. Returns ``"functions=snake_case, classes=PascalCase"``
    style strings, but only when a kind hits ``min_pct``.
    """
    parts: list[str] = []
    for kind, info in by_kind.items():
        if info["pct"] >= min_pct:
            parts.append(f"{_kind_label(kind)}={info['style']} ({info['pct']}%)")
    return ", ".join(parts) if parts else "mixed"
