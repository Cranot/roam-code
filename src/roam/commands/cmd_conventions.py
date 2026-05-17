"""Auto-detect implicit codebase conventions and patterns.

This module hosts the case-classification primitives
(``classify_case``, ``_group_for_kind``, ``_LANGUAGE_KIND_DEFAULTS``,
etc.) used everywhere in roam. The **canonical aggregator** that
applies them and produces per-kind percentages lives in
``roam.commands.conventions_helper`` — the standalone ``conventions``
command, ``roam describe``, ``roam understand``, ``roam minimap``, and
``roam preflight`` all delegate there so they agree on the same
codebase.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted because conventions outputs are invocation-scoped
convention-classification percentages — not per-location violations.
See action.yml _SUPPORTED_SARIF allowlist + W1175-RESEARCH Bucket B
propagation plan + W1148 audit memo.
"""

from __future__ import annotations

import hashlib
import json as _json
import re
import sqlite3
from collections import Counter

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.languages import JS_FAMILY_LANGUAGES
from roam.output.confidence import (
    confidence_distribution,
    verdict_with_high_count,
    wrap_findings,
)
from roam.output.formatter import (
    abbrev_kind,
    format_table,
    json_envelope,
    loc,
    to_json,
)

# W133 (W93 follow-up): conventions is the next detector migrating onto
# the central findings registry (after ``clones`` in W95, ``dead`` in
# W99, ``complexity`` in W102, ``smells`` in W109, and the subsequent
# W110-W111 emitters). The shape mirrors those — a stable detector
# version stamp and a deterministic ``finding_id_str`` so re-runs upsert
# instead of duplicating rows.
#
# **Anti-pattern Pattern 4 note.** Per ``CLAUDE.md`` Pattern 4 and the
# 212-eval dogfood synthesis, five surfaces (``describe``,
# ``understand``, ``minimap``, ``preflight``, and the standalone
# ``conventions`` command) historically each computed conventions
# differently. Fix G consolidated them onto
# ``roam.commands.conventions_helper.compute_conventions`` — that helper
# is *the* canonical detector. W133 deliberately wires ``--persist``
# onto the STANDALONE ``conventions`` command only, because:
#
# * the helper computes the data but doesn't aggregate-then-persist
#   anywhere (only ``conventions`` builds the violation envelope today),
# * the other four surfaces emit summaries, not violation lists, so
#   their ``--persist`` would either redundantly mirror the same rows or
#   re-derive violations under different filters (re-entrenching
#   Pattern 4 at the persistence layer).
#
# Bump CONVENTIONS_DETECTOR_VERSION when the violation predicate (the
# (language_family, kind_group) majority calculation in
# ``conventions_helper._find_outliers``) or the registry-row shape
# changes meaningfully.
CONVENTIONS_DETECTOR_VERSION: str = "1.0.0"


# Per-kind confidence tier mapping for conventions findings.
#
# Conventions themselves are *inferred from majority patterns* — they
# are heuristics by construction (the prompt's instruction:
# "conventions are themselves heuristics inferred from majority
# patterns"). The default tier is therefore ``heuristic``. Where a
# specific subkind has a deterministic basis (file-extension family
# defaults from ``_LANGUAGE_KIND_DEFAULTS`` — e.g. "python functions
# should be snake_case") we upgrade to ``structural``: those rules
# come from the language community default, not from the empirical
# distribution in this repo, so they're a documented expectation
# rather than a freshly-inferred guess.
_CONVENTIONS_VIOLATION_KIND: str = "naming-outlier"
_CONVENTIONS_DEFAULT_CONFIDENCE: str = "heuristic"


def _conventions_violation_confidence(expected_source: str | None) -> str:
    """Map an outlier's ``expected_source`` to a confidence tier.

    ``compute_conventions`` records ``expected_source`` as either
    ``"community_default"`` (the documented language convention from
    ``_LANGUAGE_KIND_DEFAULTS``) or ``"empirical"`` (the codebase's own
    majority style). Community-default violations are upgraded to
    ``structural`` because the expected style is documented language
    convention rather than a freshly-inferred majority. Empirical
    violations stay at ``heuristic``.
    """
    if expected_source == "community_default":
        return "structural"
    return _CONVENTIONS_DEFAULT_CONFIDENCE


def _conventions_finding_id(
    family: str,
    group: str,
    name: str,
    file_path: str,
    line_start: int | None,
) -> str:
    """Stable, deterministic finding id for one convention violation.

    The (language_family, kind_group, name, file_path, line_start)
    tuple re-identifies the same outlier across runs. We fold the
    (family, group) into the digest because the same symbol name in
    two different family/group contexts is a different finding (e.g.
    a Python ``foo`` flagged as a function-style outlier vs the same
    symbol re-flagged as a property-style outlier).
    """
    raw = f"{family}|{group}|{name}|{file_path}|{int(line_start or 0)}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"conventions:{_CONVENTIONS_VIOLATION_KIND}:{digest}"


def _resolve_convention_subject_id(
    conn: sqlite3.Connection,
    file_path: str,
    symbol_name: str,
    line_start: int | None,
) -> int | None:
    """Best-effort lookup of ``symbols.id`` for one outlier triple.

    Mirrors ``cmd_smells._resolve_smell_subject_id`` — exact match on
    (path, name, line_start) first, then nearest-line by name.
    Returns ``None`` when nothing matches; the findings registry
    permits NULL subject_id by design.
    """
    try:
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.name = ? AND s.line_start = ? "
            "LIMIT 1",
            (file_path, symbol_name, line_start),
        ).fetchone()
        if row is not None:
            return int(row[0])
        row = conn.execute(
            "SELECT s.id FROM symbols s "
            "JOIN files f ON s.file_id = f.id "
            "WHERE f.path = ? AND s.name = ? "
            "ORDER BY ABS(COALESCE(s.line_start, 0) - ?) "
            "LIMIT 1",
            (file_path, symbol_name, line_start or 0),
        ).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.OperationalError:
        return None


def _emit_conventions_findings(
    conn: sqlite3.Connection,
    outliers: list[dict],
    source_version: str,
) -> int:
    """Mirror each convention-violation outlier into the findings registry.

    Returns the count of rows written. The caller is responsible for
    opening ``conn`` writable; ``emit_finding`` does not commit (the
    caller commits once at the end of the persist branch).

    Wrapped by the caller in a defensive try/except so a pre-W89 DB
    (without the ``findings`` table) silently no-ops rather than
    crashing the standard conventions command path.

    Only convention **violations** (outliers) are emitted — the
    detected conventions themselves are *state* (codebase inventory)
    not findings, and stay in the existing per-kind / per-(family,
    group) summary surfaces.
    """
    from roam.db.findings import FindingRecord, emit_finding

    written = 0
    for o in outliers:
        name = o.get("name") or ""
        kind = o.get("kind") or ""
        file_path = o.get("file") or ""
        line_start = o.get("line")
        try:
            line_start_int: int | None = int(line_start) if line_start is not None else None
        except (TypeError, ValueError):
            line_start_int = None
        family = o.get("language_family") or "unknown"
        group = _group_for_kind(kind)
        actual_style = o.get("actual_style") or "?"
        expected_style = o.get("expected_style") or "?"
        expected_source = o.get("expected_source")

        subject_id = _resolve_convention_subject_id(conn, file_path, name, line_start_int)
        finding_id = _conventions_finding_id(family, group, name, file_path, line_start_int)
        evidence = {
            "name": name,
            "kind": kind,
            "language_family": family,
            "kind_group": group,
            "actual_style": actual_style,
            "expected_style": expected_style,
            "expected_source": expected_source,
            "file_path": file_path,
            "line_start": line_start_int,
        }
        location = f"{file_path}:{line_start_int}" if line_start_int is not None else file_path
        claim = (
            f"naming-outlier: {name} ({kind}) is {actual_style}, "
            f"expected {expected_style} for {family}/{group} at {location}"
        )
        confidence = _conventions_violation_confidence(expected_source)
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="symbol" if subject_id is not None else "file",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(evidence, sort_keys=True),
                confidence=confidence,
                source_detector="conventions",
                source_version=source_version,
            ),
        )
        written += 1
    return written


# R22 — confidence classifier for convention-violation findings.
#
# Each outlier carries the (family, group) it violated. The confidence
# reflects how dominant the convention is in that group:
#   high   — the convention is dominant in ≥90% of the group; this
#            symbol is genuinely an outlier.
#   medium — 70–89% dominance; convention is real but with notable
#            minority styles, so the call is softer.
#   low    — 50–69% dominance; the project doesn't have a clear
#            convention, so flagging the symbol is mostly noise.
#
# We capture the group percent at envelope-build time and stash it on
# each outlier so the classifier can read it without needing the
# naming_summary dict.
_CONVENTION_HIGH_PCT = 90.0
_CONVENTION_MEDIUM_PCT = 70.0


def _convention_classify(violation: dict) -> tuple[str, str]:
    """Map a convention-violation finding to a (confidence, reason) tuple."""
    pct = violation.get("group_dominant_pct")
    expected = violation.get("expected_style") or "?"
    actual = violation.get("actual_style") or "?"
    try:
        pct_f = float(pct) if pct is not None else 0.0
    except (TypeError, ValueError):
        pct_f = 0.0
    if pct_f >= _CONVENTION_HIGH_PCT:
        return "high", (f"{expected} is dominant in {pct_f:.0f}% of its group; this {actual} symbol is a clear outlier")
    if pct_f >= _CONVENTION_MEDIUM_PCT:
        return "medium", (f"{expected} dominant in {pct_f:.0f}% of its group; convention real but not unanimous")
    return "low", (f"{expected} only {pct_f:.0f}% dominant; convention is weak in this group")


# ---------------------------------------------------------------------------
# Case-style detection
# ---------------------------------------------------------------------------

_CASE_PATTERNS = {
    "snake_case": re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$"),
    "camelCase": re.compile(r"^[a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*$"),
    "PascalCase": re.compile(r"^[A-Z][a-zA-Z0-9]*[a-z][a-zA-Z0-9]*$"),
    "UPPER_SNAKE": re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$"),
    "kebab-case": re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)+$"),
}

# Single-word names match multiple conventions; classify them separately.
_SINGLE_LOWER = re.compile(r"^[a-z][a-z0-9]*$")
_SINGLE_UPPER = re.compile(r"^[A-Z][A-Z0-9]*$")
_SINGLE_PASCAL = re.compile(r"^[A-Z][a-z0-9]+$")

# Names that are too short or generic to classify meaningfully.
_MIN_NAME_LEN = 2

# Dunder / framework names to skip when detecting naming conventions.
_SKIP_NAMES = frozenset(
    {
        "__init__",
        "__str__",
        "__repr__",
        "__new__",
        "__del__",
        "__enter__",
        "__exit__",
        "__getattr__",
        "__setattr__",
        "__getitem__",
        "__setitem__",
        "__len__",
        "__iter__",
        "__next__",
        "__call__",
        "__hash__",
        "__eq__",
        "__lt__",
        "__gt__",
        "__le__",
        "__ge__",
        "__ne__",
        "__bool__",
        "__contains__",
        "__add__",
        "__sub__",
        "__mul__",
        "__truediv__",
        "__floordiv__",
        "__mod__",
        "__pow__",
        "__and__",
        "__or__",
        "__xor__",
        "__invert__",
        "constructor",
        "toString",
        "valueOf",
    }
)


def classify_case(name: str) -> str | None:
    """Return the case style of *name*, or None if unclassifiable."""
    if len(name) < _MIN_NAME_LEN or name in _SKIP_NAMES:
        return None
    if name.startswith("__") and name.endswith("__"):
        return None  # dunder

    for style, pattern in _CASE_PATTERNS.items():
        if pattern.match(name):
            return style

    # Single-word heuristics
    if _SINGLE_PASCAL.match(name):
        return "PascalCase"
    if _SINGLE_LOWER.match(name):
        return "snake_case"  # single lowercase word is compatible with snake
    if _SINGLE_UPPER.match(name):
        return "UPPER_SNAKE"

    return None


# ---------------------------------------------------------------------------
# W162 — false-positive carve-outs
# ---------------------------------------------------------------------------
#
# The W149 dogfood audit found 39 conventions findings on roam-code, ALL of
# which were false positives, splitting into three root causes:
#
#   * 27 came from ``tests/fixtures/languages/kotlin/*.kt`` — deliberately-
#     malformed fixture files used to train extractors. The detector was
#     reading its own training data and complaining about it. The fix is
#     a path-prefix exclusion handled in ``conventions_helper`` (a new
#     entry in ``DEFAULT_EXCLUDE_PREFIXES``); no carve-out needed here.
#
#   * 6 Python PascalCase "variables" that were actually PEP 484
#     ``TypeAlias`` / ``NewType`` declarations (``PathLike =
#     Union[str, os.PathLike]``, ``LockMode = Literal["read", "write"]``,
#     ``CommandTarget = tuple[str, str]``, etc.). The python extractor
#     stores these as ``kind="variable"`` because they look like
#     assignments. PEP 484 says PascalCase IS the convention for type
#     aliases — flagging them is wrong. ``is_python_type_alias_signature``
#     below detects them from the captured assignment ``signature``.
#
#   * 2 ``VERSION`` "UPPER_SNAKE properties" on bridge / language base
#     classes — they are class-level constants per PEP 8, not variables.
#     ``is_upper_snake_constant_name`` below treats names like
#     ``VERSION`` / ``MAX_RETRIES`` as constants regardless of the kind
#     the extractor reported (``variable`` / ``property`` /
#     ``constant``), which routes them into the ``constants`` group
#     where ``UPPER_SNAKE`` is the documented expectation.
#
#   * 3 YAML keys (``pr``, ``pool``, ``checkout``) misclassified as
#     Python functions because YAML files appear in the
#     ``language_family="unknown"`` bucket alongside everything else.
#     ``NON_CODE_CONVENTION_LANGUAGES`` below excludes them at the
#     query-iteration level.

# Languages that don't carry code-style conventions in any
# meaningful sense. We skip their identifiers entirely so a YAML
# pipeline file's keys don't drag everything else into the
# ``language_family="unknown"`` bucket.
#
# 17 entries (10 markup/data + 4 framework templates + 3 roam-internal
# pseudo-languages). Sourced from the W126 conventions-helper dogfood
# pass where YAML pipeline keys (``pr``, ``pool``, ``checkout``) showed
# up in the ``unknown`` bucket as PascalCase / snake_case "violations".
# Excluding them at the query-iteration level drops 3 false positives on
# the roam-code self-index and keeps the unknown-bucket signal clean.
NON_CODE_CONVENTION_LANGUAGES: frozenset[str] = frozenset(
    {
        "yaml",
        "yml",
        "json",
        "toml",
        "ini",
        "xml",
        "html",
        "css",
        "scss",
        "less",
        # markup / data — neither has a meaningful "variable naming
        # convention" we can enforce.
        "markdown",
        "md",
        "rst",
        # roam-internal hand-rolled "languages" that aren't real source
        "sfxml",  # Salesforce metadata XML
        "visualforce",  # template language, conventions are project-defined
        "aura",  # framework templates
        "hcl",  # Terraform/HCL is config; keys aren't code identifiers
    }
)


# Patterns whose RHS we treat as a Python type-alias expression. We try
# to be permissive (any RHS that "looks like a type" matches) because a
# false negative here merely keeps the historical behaviour (flag as a
# PascalCase variable). The match is anchored on the assignment's RHS,
# which is the ``signature`` value the extractor captures
# (truncated at 80 chars, see ``python_lang._extract_module_assignment``).
#
# Cases we want to match (all observed on roam-code):
#   ``PathLike = Union[str, os.PathLike]``
#   ``TaintOrigin = tuple[str, Union[int, str]]``
#   ``LockMode = Literal["read", "write", "exclusive"]``
#   ``CommandTarget = tuple[str, str]``
#   ``Finding = dict[str, Any]``
#   ``DetectorSpec = tuple[str, str, Callable[[Any], list[Finding]]]``
#   ``PluginAPI = RoamPluginContext``  (alias to another typedef)
#   ``X: TypeAlias = list[int]``  (PEP 613)
#   ``LockMode = NewType("LockMode", str)``  (PEP 484 NewType)
#   ``type Alias = list[int]``  (PEP 695)
_TYPE_ALIAS_CONTAINERS = (
    "Union",
    "Optional",
    "Literal",
    "Callable",
    "Tuple",
    "List",
    "Dict",
    "Set",
    "FrozenSet",
    "Type",
    "Annotated",
    "Sequence",
    "Iterable",
    "Iterator",
    "Mapping",
    "MutableMapping",
    "Awaitable",
    "Coroutine",
    "AsyncIterator",
    "Generator",
    "TypedDict",
    "Protocol",
)


def is_python_type_alias_signature(signature: str | None, name: str) -> bool:
    """Return True if *signature* (an assignment text) looks like a type alias.

    The extractor stores Python module-level assignments as ``"<name> =
    <rhs[:80]>"``. We accept the assignment as a type alias if any of:

    * the LHS has a ``: TypeAlias`` annotation (PEP 613)
    * it's a PEP 695 ``type X = ...`` statement
    * the RHS calls ``NewType(...)`` (PEP 484)
    * the RHS opens with a generic-subscript on a typing container
      (``Union[...]``, ``Optional[...]``, ``Literal[...]``,
      ``Callable[...]``, etc.) or a builtin generic (``tuple[...]``,
      ``list[...]``, ``dict[...]``, ``set[...]``, ``frozenset[...]``,
      ``type[...]``)
    * the RHS is a single PascalCase identifier (an alias to another
      type, e.g. ``PluginAPI = RoamPluginContext``)

    *name* is the LHS symbol name; the test patterns mostly care about
    the RHS but a leading ``<name>:`` annotation is recognised too.
    """
    if not signature:
        return False
    sig = signature.strip()
    # PEP 695: ``type Name = ...``
    if sig.startswith("type ") and "=" in sig:
        return True
    # PEP 613 explicit annotation: ``Name: TypeAlias = ...`` (the
    # extractor's signature for an annotated assignment may or may not
    # include the annotation depending on the tree-sitter node walked;
    # we accept either form).
    if "TypeAlias" in sig:
        return True
    # Find RHS by splitting on the first ``=`` not inside a generic.
    # The signature shape is always ``<lhs> = <rhs>`` for module
    # assignments per ``python_lang._extract_module_assignment``.
    if "=" not in sig:
        return False
    rhs = sig.split("=", 1)[1].strip()
    if not rhs:
        return False
    # NewType call
    if rhs.startswith("NewType(") or rhs.startswith("typing.NewType("):
        return True
    # Typing-container generic at the head of the RHS
    for container in _TYPE_ALIAS_CONTAINERS:
        if rhs.startswith(container + "[") or rhs.startswith("typing." + container + "["):
            return True
    # Builtin generics at the head of the RHS (PEP 585)
    for builtin_generic in ("tuple[", "list[", "dict[", "set[", "frozenset[", "type["):
        if rhs.startswith(builtin_generic):
            return True
    # Pipe-style union (PEP 604): ``str | None`` or ``int | str | bytes``
    # We only accept this when there's a ``|`` outside any brackets in
    # the first ~40 chars — a heuristic that avoids matching bitwise OR.
    head = rhs.split("(", 1)[0]
    if "|" in head and all(
        part.strip()
        and part.strip()[0].isupper()
        or part.strip() in ("None", "int", "str", "float", "bytes", "bool", "list", "dict", "set", "tuple")
        for part in head.split("|")
    ):
        return True
    # Single-token RHS that is itself a PascalCase identifier —
    # treated as an alias to another typedef (the
    # ``PluginAPI = RoamPluginContext`` case). We restrict to clean
    # identifiers (no parentheses, no dots beyond a single dotted
    # module path) to avoid matching ordinary class-instantiation
    # patterns like ``foo = SomeClass()``.
    if "(" not in rhs and "[" not in rhs and re.match(r"^[A-Za-z_][\w\.]*$", rhs):
        # Pure PascalCase head segment → alias-to-typedef.
        head_ident = rhs.split(".")[-1]
        if _SINGLE_PASCAL.match(head_ident) or _CASE_PATTERNS["PascalCase"].match(head_ident):
            return True
    return False


def is_upper_snake_constant_name(name: str) -> bool:
    """Return True if *name* should be classified as a constant per PEP 8.

    Matches both single-word upper (``VERSION``) and multi-word
    UPPER_SNAKE (``MAX_RETRIES``, ``DEFAULT_TIMEOUT_S``). Used by the
    canonical conventions detector to re-route ``UPPER_SNAKE``-named
    symbols into the ``constants`` group regardless of the declared
    extractor kind (``variable`` / ``property`` / ``constant``). Aligns
    with PEP 8 §"Naming Conventions" — "constants are usually defined
    on a module level and written in all capital letters with
    underscores separating words".
    """
    if not name or len(name) < 1:
        return False
    return bool(re.match(r"^[A-Z][A-Z0-9_]*$", name))


# Per-language convention bias — each language has community defaults that
# the detector should respect rather than imposing the codebase-wide
# dominant style. Round 3 #2 noted SQL identifiers (snake_case) being
# flagged as outliers because the surrounding TypeScript codebase had
# camelCase as its dominant style.
_LANGUAGE_FAMILIES = {
    "python": "python",
    "ruby": "ruby",
    "rust": "rust",
    "go": "go",
    "javascript": "js",
    "typescript": "js",
    "tsx": "js",
    "jsx": "js",
    "vue": "js",
    "svelte": "js",
    "java": "jvm",
    "kotlin": "jvm",
    "scala": "jvm",
    "csharp": "dotnet",
    "fsharp": "dotnet",
    "vbnet": "dotnet",
    "sql": "sql",
    "ddl": "sql",
    "postgresql": "sql",
    "mysql": "sql",
    "plsql": "sql",
}

# Expected dominant style per (language_family, kind_group). The detector
# uses this to flag outliers against the LANGUAGE convention — not the
# codebase-wide one — so SQL tables in a TS-dominant project don't trip
# the camelCase expectation.
_LANGUAGE_KIND_DEFAULTS: dict[tuple[str, str], str] = {
    ("python", "functions"): "snake_case",
    ("python", "classes"): "PascalCase",
    ("python", "constants"): "UPPER_SNAKE",
    ("python", "variables"): "snake_case",
    ("python", "properties"): "snake_case",
    ("ruby", "functions"): "snake_case",
    ("ruby", "classes"): "PascalCase",
    ("ruby", "constants"): "UPPER_SNAKE",
    ("rust", "functions"): "snake_case",
    ("rust", "classes"): "PascalCase",
    ("rust", "constants"): "UPPER_SNAKE",
    ("go", "functions"): "camelCase",  # exported PascalCase, but most are camel
    ("go", "classes"): "PascalCase",
    ("js", "functions"): "camelCase",
    ("js", "classes"): "PascalCase",
    ("js", "constants"): "UPPER_SNAKE",
    ("js", "variables"): "camelCase",
    ("js", "properties"): "camelCase",
    ("jvm", "functions"): "camelCase",
    ("jvm", "classes"): "PascalCase",
    ("jvm", "constants"): "UPPER_SNAKE",
    ("dotnet", "functions"): "PascalCase",
    ("dotnet", "classes"): "PascalCase",
    ("dotnet", "constants"): "PascalCase",
    ("sql", "functions"): "snake_case",
    ("sql", "classes"): "snake_case",  # tables/views — SQL has no real PascalCase tradition
    ("sql", "constants"): "UPPER_SNAKE",
    ("sql", "variables"): "snake_case",
    ("sql", "properties"): "snake_case",
}


def _language_family(language: str | None) -> str:
    """Map a file's language to a convention family.

    Returns ``"unknown"`` for languages we don't track — those fall back
    to the codebase-wide dominant style (the historic behaviour).
    """
    if not language:
        return "unknown"
    return _LANGUAGE_FAMILIES.get(language.lower(), "unknown")


# Kind groupings for naming analysis
_KIND_GROUPS = {
    "function": "functions",
    "method": "functions",
    "class": "classes",
    "interface": "classes",
    "struct": "classes",
    "trait": "classes",
    "enum": "classes",
    "variable": "variables",
    "constant": "constants",
    "property": "variables",
    "field": "variables",
}


def _group_for_kind(kind: str) -> str:
    return _KIND_GROUPS.get(kind, "other")


# ---------------------------------------------------------------------------
# Prefix / suffix detection
# ---------------------------------------------------------------------------


def _detect_affixes(names: list[str], min_count: int = 5, min_ratio: float = 0.03) -> dict:
    """Detect common prefixes and suffixes from a list of names."""
    prefix_counter: Counter = Counter()
    suffix_counter: Counter = Counter()

    for name in names:
        # Prefixes: split on _ or case boundary
        parts = re.split(r"[_]", name)
        if len(parts) >= 2 and len(parts[0]) >= 2:
            prefix_counter[parts[0] + "_"] += 1
        # Check camelCase prefix (lowercase start up to first uppercase)
        m = re.match(r"^([a-z]+)[A-Z]", name)
        if m and len(m.group(1)) >= 2:
            prefix_counter[m.group(1)] += 1

        # Suffixes
        if len(parts) >= 2 and len(parts[-1]) >= 2:
            suffix_counter["_" + parts[-1]] += 1
        # PascalCase suffix: last uppercase word
        m = re.search(r"[a-z]([A-Z][a-z]+)$", name)
        if m and len(m.group(1)) >= 3:
            suffix_counter[m.group(1)] += 1

    total = max(len(names), 1)
    threshold = max(min_count, int(total * min_ratio))

    prefixes = [
        {"affix": p, "count": c, "percent": round(100 * c / total, 1)}
        for p, c in prefix_counter.most_common(10)
        if c >= threshold
    ]
    suffixes = [
        {"affix": s, "count": c, "percent": round(100 * c / total, 1)}
        for s, c in suffix_counter.most_common(10)
        if c >= threshold
    ]
    return {"prefixes": prefixes, "suffixes": suffixes}


# ---------------------------------------------------------------------------
# File organization detection
# ---------------------------------------------------------------------------

_TEST_PATTERNS = [
    ("test_*.py", re.compile(r"(^|/)test_[^/]+\.py$")),
    ("*_test.py", re.compile(r"(^|/)[^/]+_test\.py$")),
    ("*.test.ts", re.compile(r"(^|/)[^/]+\.test\.ts$")),
    ("*.test.tsx", re.compile(r"(^|/)[^/]+\.test\.tsx$")),
    ("*.test.js", re.compile(r"(^|/)[^/]+\.test\.js$")),
    ("*.test.jsx", re.compile(r"(^|/)[^/]+\.test\.jsx$")),
    ("*.spec.ts", re.compile(r"(^|/)[^/]+\.spec\.ts$")),
    ("*.spec.tsx", re.compile(r"(^|/)[^/]+\.spec\.tsx$")),
    ("*.spec.js", re.compile(r"(^|/)[^/]+\.spec\.js$")),
    ("*.spec.jsx", re.compile(r"(^|/)[^/]+\.spec\.jsx$")),
    ("*_test.go", re.compile(r"(^|/)[^/]+_test\.go$")),
    ("*_test.rs", re.compile(r"(^|/)[^/]+_test\.rs$")),
    ("Test*.java", re.compile(r"(^|/)Test[^/]+\.java$")),
    ("*Test.java", re.compile(r"(^|/)[^/]+Test\.java$")),
]

_BARREL_NAMES = frozenset(
    {
        "index.ts",
        "index.js",
        "index.tsx",
        "index.jsx",
        "index.mjs",
        "index.cjs",
        "__init__.py",
    }
)


def _analyze_files(paths: list[str]) -> dict:
    """Analyze file paths for directory structure and test conventions."""
    normalized = [p.replace("\\", "/") for p in paths]

    # Top-level directory counts
    dir_counts: Counter = Counter()
    for p in normalized:
        parts = p.split("/")
        if len(parts) > 1:
            dir_counts[parts[0] + "/"] += 1

    top_dirs = [{"dir": d, "count": c} for d, c in dir_counts.most_common(15) if c >= 2]

    # Test file patterns
    test_pattern_counts: Counter = Counter()
    test_dir_counts: Counter = Counter()
    total_test_files = 0

    for p in normalized:
        for pattern_name, regex in _TEST_PATTERNS:
            if regex.search(p):
                test_pattern_counts[pattern_name] += 1
                total_test_files += 1
                # Track which directories contain tests
                parts = p.split("/")
                if len(parts) > 1:
                    test_dir_counts[parts[0] + "/"] += 1
                break

    test_patterns = [{"pattern": pat, "count": c} for pat, c in test_pattern_counts.most_common(5) if c >= 1]

    test_dirs = [{"dir": d, "count": c} for d, c in test_dir_counts.most_common(5) if c >= 1]

    # Barrel files
    barrel_count = 0
    for p in normalized:
        basename = p.rsplit("/", 1)[-1] if "/" in p else p
        if basename in _BARREL_NAMES:
            barrel_count += 1

    return {
        "total_files": len(paths),
        "top_dirs": top_dirs,
        "test_patterns": test_patterns,
        "test_dirs": test_dirs,
        "test_file_count": total_test_files,
        "barrel_files": barrel_count,
        "has_barrels": barrel_count > 0,
    }


# ---------------------------------------------------------------------------
# Import pattern detection
# ---------------------------------------------------------------------------


def _analyze_imports(conn) -> dict:
    """Analyze import edges for absolute vs relative and grouping patterns."""
    # Get edges with kind='imports' joining file paths
    rows = conn.execute("""
        SELECT fe.source_file_id, fe.target_file_id, fe.symbol_count,
               sf.path as source_path, tf.path as target_path
        FROM file_edges fe
        JOIN files sf ON fe.source_file_id = sf.id
        JOIN files tf ON fe.target_file_id = tf.id
        WHERE fe.kind = 'imports'
    """).fetchall()

    if not rows:
        return {
            "total_import_edges": 0,
            "absolute_imports": 0,
            "relative_imports": 0,
            "absolute_pct": 0,
            "style": "unknown",
        }

    total = len(rows)
    relative = 0
    absolute = 0

    for r in rows:
        src = r["source_path"].replace("\\", "/")
        tgt = r["target_path"].replace("\\", "/")

        # Heuristic: if source and target share a common prefix directory,
        # and the target is within 2 levels, it's likely a relative import.
        src_parts = src.rsplit("/", 1)
        tgt_parts = tgt.rsplit("/", 1)

        src_dir = src_parts[0] if len(src_parts) > 1 else ""
        tgt_dir = tgt_parts[0] if len(tgt_parts) > 1 else ""

        if (
            src_dir
            and tgt_dir
            and (src_dir == tgt_dir or src_dir.startswith(tgt_dir + "/") or tgt_dir.startswith(src_dir + "/"))
        ):
            relative += 1
        else:
            absolute += 1

    abs_pct = round(100 * absolute / total, 1) if total else 0
    style = "absolute" if abs_pct >= 60 else "relative" if abs_pct <= 40 else "mixed"

    return {
        "total_import_edges": total,
        "absolute_imports": absolute,
        "relative_imports": relative,
        "absolute_pct": abs_pct,
        "relative_pct": round(100 * relative / total, 1) if total else 0,
        "style": style,
    }


# ---------------------------------------------------------------------------
# Export pattern detection
# ---------------------------------------------------------------------------


def _analyze_exports(conn) -> dict:
    """Analyze is_exported flag distribution across symbols."""
    row = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_exported = 1 THEN 1 ELSE 0 END) as exported,
            SUM(CASE WHEN is_exported = 0 THEN 1 ELSE 0 END) as private
        FROM symbols
        WHERE kind IN ('function', 'class', 'method', 'variable', 'constant',
                        'interface', 'struct', 'enum', 'type_alias')
    """).fetchone()

    total = row["total"] or 0
    exported = row["exported"] or 0
    private = row["private"] or 0
    exported_pct = round(100 * exported / total, 1) if total else 0

    # Per-kind breakdown
    kind_rows = conn.execute("""
        SELECT kind,
               COUNT(*) as total,
               SUM(CASE WHEN is_exported = 1 THEN 1 ELSE 0 END) as exported
        FROM symbols
        WHERE kind IN ('function', 'class', 'method', 'variable', 'constant',
                        'interface', 'struct', 'enum', 'type_alias')
        GROUP BY kind
        ORDER BY total DESC
    """).fetchall()

    by_kind = []
    for kr in kind_rows:
        kt = kr["total"] or 0
        ke = kr["exported"] or 0
        by_kind.append(
            {
                "kind": kr["kind"],
                "total": kt,
                "exported": ke,
                "exported_pct": round(100 * ke / kt, 1) if kt else 0,
            }
        )

    # Detect default-export vs named-export preference for JS/TS
    # Check if files have exactly one exported symbol (likely default export).
    # Vue / Svelte SFCs participate in the same import graph as .ts files
    # (their ``<script>`` blocks compile down to ESM modules) so they're
    # counted here — see ``roam.languages.JS_FAMILY_LANGUAGES``.
    js_ph = ",".join("?" * len(JS_FAMILY_LANGUAGES))
    default_style_rows = conn.execute(
        f"""
        SELECT f.id, f.path, COUNT(*) as exported_count
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.is_exported = 1
          AND f.language IN ({js_ph})
        GROUP BY f.id
    """,
        JS_FAMILY_LANGUAGES,
    ).fetchall()

    single_export_files = sum(1 for r in default_style_rows if r["exported_count"] == 1)
    multi_export_files = sum(1 for r in default_style_rows if r["exported_count"] > 1)
    js_ts_total = single_export_files + multi_export_files

    export_style = "unknown"
    if js_ts_total > 0:
        if single_export_files > multi_export_files:
            export_style = "default-export preferred"
        else:
            export_style = "named-exports preferred"

    return {
        "total_symbols": total,
        "exported": exported,
        "private": private,
        "exported_pct": exported_pct,
        "by_kind": by_kind,
        "js_ts_export_style": export_style,
        "js_ts_single_export_files": single_export_files,
        "js_ts_multi_export_files": multi_export_files,
    }


# ---------------------------------------------------------------------------
# Error handling detection
# ---------------------------------------------------------------------------

_ERROR_NAME_RE = re.compile(r"(Error|Exception|Err|Fault|Failure|Panic)$", re.IGNORECASE)


def _analyze_error_handling(conn) -> dict:
    """Detect error/exception patterns from symbols and file complexity."""
    # Count error-related symbols.
    # Use a broad query then filter in Python to avoid LIKE false positives
    # (e.g., DEFAULT matching %Fault%).
    error_candidates = conn.execute("""
        SELECT s.name, s.kind, f.path as file_path, s.line_start
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.name LIKE '%Error%'
           OR s.name LIKE '%Exception%'
           OR s.name LIKE '%Failure%'
    """).fetchall()
    error_symbols = [
        r
        for r in error_candidates
        if _ERROR_NAME_RE.search(r["name"])
        or "Error" in r["name"]
        or "Exception" in r["name"]
        or "Failure" in r["name"]
    ]

    error_classes = [r for r in error_symbols if r["kind"] in ("class", "struct", "interface")]
    error_functions = [r for r in error_symbols if r["kind"] in ("function", "method")]

    # Complexity as proxy for error handling density
    complexity_rows = conn.execute("""
        SELECT AVG(complexity) as avg_complexity,
               MAX(complexity) as max_complexity,
               COUNT(*) as file_count
        FROM file_stats
        WHERE complexity > 0
    """).fetchone()

    return {
        "error_symbol_count": len(error_symbols),
        "error_classes": len(error_classes),
        "error_functions": len(error_functions),
        "error_symbols": [
            {"name": r["name"], "kind": r["kind"], "file": r["file_path"], "line": r["line_start"]}
            for r in error_symbols[:20]
        ],
        "avg_complexity": round(complexity_rows["avg_complexity"] or 0, 1),
        "max_complexity": round(complexity_rows["max_complexity"] or 0, 1),
        "files_with_complexity": complexity_rows["file_count"] or 0,
    }


def _analyze_naming(conn, exclude_paths=None) -> tuple[list, dict, list, dict]:
    """Discover dominant naming style per (language_family, kind_group) and
    surface symbols that violate it.

    Delegates to ``roam.commands.conventions_helper.compute_conventions``
    so the standalone ``conventions`` command agrees with every other
    roam command that mentions conventions. Excludes non-source-code
    paths (``.github/``, ``.claude/``, ``docs/``, ``dist/``, …) by
    default — Pattern 4 of the dogfood corpus showed standalone
    conventions emitting 9014 outliers (51% of identifiers!) including
    ``.github/workflows/setup-node``-style false positives.

    Returns ``(all_symbols, naming_summary, outliers, affixes)`` to
    preserve the historic call shape for backwards compatibility with
    the JSON envelope.
    """
    # Inline import to avoid circular dependency at module load.
    from roam.commands.conventions_helper import compute_conventions

    result = compute_conventions(conn, exclude_paths=exclude_paths)

    # all_symbols is returned for callers that needed the raw row list.
    # The helper doesn't expose that anymore (since exclude filtering
    # happens inside it), so we synthesise a compatible row count from
    # the totals. Existing callers (this module's own ``conventions``
    # command) only use ``naming_summary``, ``outliers``, and
    # ``affixes`` — the raw list is kept as an empty placeholder.
    all_symbols: list = []
    naming_summary = result["by_family_group"]
    outliers = result["outliers"]
    affixes = result["affixes"]
    return all_symbols, naming_summary, outliers, affixes


def _build_naming_summary(group_cases: dict[tuple[str, str], Counter]) -> dict[str, dict]:
    """Pick the dominant style per (family, group). Documented community
    defaults beat the empirical mode so a SQL-heavy project's bad habits
    don't get treated as "the convention"."""
    summary: dict[str, dict] = {}
    for (family, group), counter in sorted(group_cases.items()):
        total = sum(counter.values())
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


def _find_naming_outliers(symbol_details: list[dict], naming_summary: dict[str, dict]) -> list[dict]:
    """Symbols whose case style doesn't match the dominant style for their
    (family, group)."""
    outliers: list[dict] = []
    for det in symbol_details:
        family = det["language_family"]
        group = det["group"]
        key = f"{family}/{group}" if family != "unknown" else group
        grp_info = naming_summary.get(key)
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
# Main command
# ---------------------------------------------------------------------------


@roam_capability(
    name="conventions",
    category="refactoring",
    summary="Auto-detect codebase naming, file, import, and export conventions",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command()
@click.option("-n", "max_outliers", default=10, help="Maximum outliers to display per category")
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Persist convention-violation findings (naming outliers) to "
        ".roam/index.db findings registry (cross-detector queryable via "
        "`roam findings list --detector conventions`). The detector-"
        "specific output is unchanged; the registry rows are the "
        "denormalised cross-detector surface. Detected conventions "
        "themselves stay as inventory in the standard envelope — only "
        "violations are emitted as findings."
    ),
)
@click.pass_context
def conventions(ctx, max_outliers, persist):
    """Auto-detect codebase naming, file, import, and export conventions.

    Unlike ``verify`` (which enforces conventions on changed files) and
    ``check-rules`` (which evaluates governance rules), this command
    discovers what conventions the codebase actually follows.

    Naming detection delegates to the canonical helper at
    ``roam.commands.conventions_helper`` so this command's verdicts
    agree with ``roam describe``, ``roam understand``, ``roam minimap``,
    and ``roam preflight``.

    By default the helper skips identifiers under ``.github/``,
    ``.claude/``, ``docs/``, ``dist/``, ``build/``, ``node_modules/``,
    ``vendor/``, and ``__pycache__/``. The global ``--include-excluded``
    flag restores legacy scan-everything behaviour for users who need it.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    include_excluded = ctx.obj.get("include_excluded") if ctx.obj else False
    ensure_index()

    with open_db(readonly=not persist) as conn:
        project = find_project_root().name

        # ---- 1. Naming conventions ----
        # ``exclude_paths=()`` disables the default exclusion list so
        # the legacy scan-everything behaviour is still available via
        # the global ``--include-excluded`` flag.
        exclude_paths = () if include_excluded else None
        all_symbols, naming_summary, outliers, affixes = _analyze_naming(conn, exclude_paths=exclude_paths)

        # --- W133: mirror outliers into the central findings registry ---
        # Runs ONLY with --persist. We emit one row per convention
        # violation; the conventions themselves stay as inventory in
        # the standard envelope (the per-(family, group) summary).
        # Wrapped defensively so a pre-W89 DB (no ``findings`` table)
        # degrades cleanly without breaking the standard output path.
        if persist:
            try:
                _emit_conventions_findings(conn, outliers, CONVENTIONS_DETECTOR_VERSION)
                conn.commit()
            except sqlite3.OperationalError:
                pass

        # ---- 2. File organization ----
        file_rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
        file_paths = [r["path"] for r in file_rows]
        file_info = _analyze_files(file_paths)

        # ---- 3. Import patterns ----
        import_info = _analyze_imports(conn)

        # ---- 4. Error handling ----
        error_info = _analyze_error_handling(conn)

        # ---- 5. Export patterns ----
        export_info = _analyze_exports(conn)

        # ---- Build verdict ----
        # Dominant naming style across all groups
        dominant_desc = ""
        if naming_summary:
            # Pick the group with the most symbols to represent overall style
            biggest_group = max(naming_summary.values(), key=lambda g: g["total"])
            dominant_desc = f"{biggest_group['dominant_style']} ({biggest_group['percent']}%)"
        test_desc = f"{file_info['test_file_count']} test files" if file_info["test_file_count"] else "no test files"
        outlier_desc = f"{len(outliers)} naming outliers" if outliers else "consistent naming"
        verdict = f"{outlier_desc}, {dominant_desc}, {test_desc}"

        # ---- JSON output ----
        if json_mode:
            # Annotate each outlier with the dominant-style percent of
            # its (family, group) so the R22 classifier can derive a
            # confidence label without needing naming_summary at hand.
            violation_list = []
            for o in outliers:
                family = o.get("language_family", "unknown")
                # Recover group by looking at the kind (we don't store
                # group on the outlier directly).
                kind = o.get("kind", "")
                group = _group_for_kind(kind)
                key = f"{family}/{group}" if family != "unknown" else group
                grp_info = naming_summary.get(key) or {}
                violation_list.append(
                    {
                        "name": o["name"],
                        "kind": o["kind"],
                        "actual_style": o["actual_style"],
                        "expected_style": o["expected_style"],
                        "file": o["file"],
                        "line": o["line"],
                        "group_dominant_pct": grp_info.get("percent"),
                        "group_dominant_style": grp_info.get("dominant_style"),
                        "naming_group": key,
                    }
                )
            # R22: wrap each violation in {value, confidence, reason}.
            # Consumers that previously read violations[i]["name"] must
            # now read violations[i]["value"]["name"] plus
            # violations[i]["confidence"] / violations[i]["reason"].
            violation_triples = wrap_findings(violation_list, classifier=_convention_classify)
            distribution = confidence_distribution(violation_triples)
            wrapped_verdict = verdict_with_high_count(verdict, distribution)
            # W805-followup-E: empty-state disclosure (Pattern 2 silent-
            # fallback fix). When naming_summary is empty the per-group
            # symbol analysis ran against zero symbols; "consistent
            # naming, no test files" is indistinguishable from "we
            # analyzed nothing." Surface the degraded state explicitly.
            empty_naming = not naming_summary
            summary = {
                "verdict": (
                    "no symbols analyzed (corpus empty — run `roam index --force` to populate)"
                    if empty_naming
                    else wrapped_verdict
                ),
                "total_symbols_analyzed": sum(g["total"] for g in naming_summary.values()),
                "naming_groups": len(naming_summary),
                "outlier_count": len(outliers),
                "total_files": file_info["total_files"],
                "test_files": file_info["test_file_count"],
                "barrel_files": file_info["barrel_files"],
                "import_style": import_info["style"],
                "exported_pct": export_info["exported_pct"],
                "findings_confidence_distribution": distribution,
            }
            if empty_naming:
                summary["partial_success"] = True
                summary["state"] = "no_symbols_analyzed"
            click.echo(
                to_json(
                    json_envelope(
                        "conventions",
                        summary=summary,
                        budget=token_budget,
                        naming=naming_summary,
                        affixes=affixes,
                        files=file_info,
                        imports=import_info,
                        exports=export_info,
                        errors=error_info,
                        violations=violation_triples,
                    )
                )
            )
            return

        # ---- Text output ----
        click.echo(f"VERDICT: {verdict}\n")
        click.echo(f"Conventions detected in {project}:\n")

        # -- Naming --
        click.echo("=== Naming ===")
        if naming_summary:
            for group, info in sorted(naming_summary.items()):
                click.echo(
                    f"  {group.capitalize()}: {info['dominant_style']} ({info['percent']}% of {info['total']} {group})"
                )
                # Show minority styles if present
                for style, count in info["breakdown"].items():
                    if style != info["dominant_style"] and count >= 2:
                        pct = round(100 * count / info["total"], 1)
                        click.echo(f"    also: {style} ({pct}%, {count})")
        else:
            click.echo("  (no classifiable symbols found)")

        if outliers:
            click.echo(f"\n  Outliers ({len(outliers)} total):")
            for o in outliers[:max_outliers]:
                click.echo(
                    f"    {o['name']} ({o['actual_style']}, "
                    f"expected {o['expected_style']}) "
                    f"at {loc(o['file'], o['line'])}"
                )
            if len(outliers) > max_outliers:
                click.echo(f"    (+{len(outliers) - max_outliers} more)")

        if affixes["prefixes"] or affixes["suffixes"]:
            click.echo("\n  Common affixes:")
            for p in affixes["prefixes"][:5]:
                click.echo(f"    prefix {p['affix']}  ({p['count']} symbols, {p['percent']}%)")
            for s in affixes["suffixes"][:5]:
                click.echo(f"    suffix {s['affix']}  ({s['count']} symbols, {s['percent']}%)")

        # -- File organization --
        click.echo(f"\n=== File Organization ({file_info['total_files']} files) ===")
        if file_info["top_dirs"]:
            dir_rows = [[d["dir"], str(d["count"])] for d in file_info["top_dirs"]]
            click.echo(format_table(["Directory", "Files"], dir_rows))
        if file_info["test_patterns"]:
            click.echo(f"\n  Test files: {file_info['test_file_count']} detected")
            for tp in file_info["test_patterns"]:
                click.echo(f"    {tp['pattern']} ({tp['count']} files)")
            if file_info["test_dirs"]:
                dirs = ", ".join(d["dir"] for d in file_info["test_dirs"])
                click.echo(f"    in: {dirs}")
        else:
            click.echo("  Tests: (no standard test file patterns detected)")
        if file_info["has_barrels"]:
            click.echo(f"  Barrel/index files: {file_info['barrel_files']}")

        # -- Import style --
        click.echo(f"\n=== Import Style ({import_info['total_import_edges']} import edges) ===")
        if import_info["total_import_edges"] > 0:
            click.echo(
                f"  {import_info['style'].capitalize()} imports preferred "
                f"({import_info['absolute_pct']}% cross-directory, "
                f"{import_info['relative_pct']}% same-directory)"
            )
        else:
            click.echo("  (no import edges found)")

        # -- Error handling --
        click.echo("\n=== Error Handling ===")
        if error_info["error_symbol_count"] > 0:
            click.echo(
                f"  {error_info['error_symbol_count']} error-related symbols "
                f"({error_info['error_classes']} classes, "
                f"{error_info['error_functions']} functions)"
            )
            for es in error_info["error_symbols"][:5]:
                click.echo(f"    {es['name']} ({abbrev_kind(es['kind'])}) at {loc(es['file'], es['line'])}")
            if len(error_info["error_symbols"]) > 5:
                click.echo(f"    (+{len(error_info['error_symbols']) - 5} more)")
        else:
            click.echo("  (no error/exception symbols detected)")
        if error_info["files_with_complexity"] > 0:
            click.echo(f"  Avg file complexity: {error_info['avg_complexity']} (max {error_info['max_complexity']})")

        # -- Export pattern --
        click.echo(f"\n=== Export Pattern ({export_info['total_symbols']} symbols) ===")
        if export_info["total_symbols"] > 0:
            click.echo(f"  Exported: {export_info['exported']} ({export_info['exported_pct']}%)")
            click.echo(
                f"  Private:  {export_info['private']} "
                f"({round(100 * export_info['private'] / export_info['total_symbols'], 1)}%)"
            )
            if export_info["by_kind"]:
                ek_rows = [
                    [
                        abbrev_kind(k["kind"]),
                        str(k["total"]),
                        str(k["exported"]),
                        f"{k['exported_pct']}%",
                    ]
                    for k in export_info["by_kind"]
                ]
                click.echo(format_table(["Kind", "Total", "Exported", "Rate"], ek_rows))
            if export_info["js_ts_export_style"] != "unknown":
                click.echo(f"  JS/TS: {export_info['js_ts_export_style']}")
        else:
            click.echo("  (no symbols found)")
