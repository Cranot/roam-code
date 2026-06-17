"""Leaf-level case-classification primitives for naming-convention detection.

This module holds the pure, dependency-free building blocks that the
canonical ``roam.commands.conventions_helper.compute_conventions`` aggregator
and the standalone ``roam.commands.cmd_conventions`` command both consume:
case classifiers, the skip / non-code language sets, the Python type-alias and
constant detectors, and the per-language convention-default tables.

These primitives previously lived in ``cmd_conventions`` for historic reasons,
which forced ``conventions_helper`` to import from the command module — a
top-level import cycle (``conventions_helper -> cmd_conventions ->
conventions_helper``). Extracting them into this leaf module breaks the cycle:
``conventions_helper`` and ``cmd_conventions`` both import from here, and
neither imports the other at module load. ``cmd_conventions`` re-exports every
name below for backward compatibility (existing references such as
``cmd_conventions.classify_case`` / ``cmd_conventions._SKIP_NAMES`` keep
resolving).

This module must stay a LEAF: it imports only the stdlib (``re``, ``Counter``).
Do NOT import ``cmd_conventions`` or ``conventions_helper`` from here.
"""

from __future__ import annotations

import re
from collections import Counter

# ---------------------------------------------------------------------------
# Case classification
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
    # Framework lifecycle / override points: renaming them breaks the
    # framework contract, so they are never naming-convention signal and
    # never naming-convention violations (dogfood: `setUp` in a
    # PHPUnit TestCase was flagged for rename to snake_case).
    | {
        # PHPUnit / xUnit / Python unittest
        "setUp",
        "tearDown",
        "setUpBeforeClass",
        "tearDownAfterClass",
        "setUpClass",
        "tearDownClass",
        "setUpModule",
        "tearDownModule",
        "assertPreConditions",
        # JS test runners (Jest/Vitest/Mocha)
        "beforeEach",
        "afterEach",
        "beforeAll",
        "afterAll",
        # Vue lifecycle + composition entry point
        "beforeCreate",
        "beforeMount",
        "beforeUpdate",
        "beforeUnmount",
        "beforeDestroy",
        "errorCaptured",
        "renderTracked",
        "renderTriggered",
        # React class components
        "componentDidMount",
        "componentDidUpdate",
        "componentWillUnmount",
        "shouldComponentUpdate",
        "getDerivedStateFromProps",
        "getSnapshotBeforeUpdate",
        "componentDidCatch",
        # Angular
        "ngOnInit",
        "ngOnChanges",
        "ngOnDestroy",
        "ngAfterViewInit",
        "ngAfterContentInit",
        "ngDoCheck",
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
        # A single lowercase word (`props`, `run`, `delay`) is written
        # identically under snake_case and camelCase — it carries no case
        # signal. Counting it as snake_case both inflated the snake bucket
        # in the majority sample AND flagged it against camelCase repos
        # (dogfood: every Vue SFC destructure re-flagged).
        # Case-neutral: never a sample vote, never a violation.
        return None
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


def _sig_is_pep695_or_typealias(sig: str) -> bool:
    """PEP 695 ``type X = ...`` statement, or an explicit ``TypeAlias`` annotation.

    The PEP 613 ``Name: TypeAlias = ...`` annotation may or may not appear in the
    extractor's signature depending on the tree-sitter node walked; we accept
    either form by substring.
    """
    if sig.startswith("type ") and "=" in sig:
        return True
    return "TypeAlias" in sig


def _rhs_is_newtype_or_generic(rhs: str) -> bool:
    """RHS is a ``NewType(...)`` call (PEP 484) or a typing/builtin generic subscript."""
    if rhs.startswith("NewType(") or rhs.startswith("typing.NewType("):
        return True
    # Typing-container generic at the head of the RHS (``Union[...]`` etc.)
    for container in _TYPE_ALIAS_CONTAINERS:
        if rhs.startswith(container + "[") or rhs.startswith("typing." + container + "["):
            return True
    # Builtin generics at the head of the RHS (PEP 585)
    return any(
        rhs.startswith(builtin_generic)
        for builtin_generic in ("tuple[", "list[", "dict[", "set[", "frozenset[", "type[")
    )


def _rhs_is_pipe_union(rhs: str) -> bool:
    """Pipe-style union (PEP 604): ``str | None`` or ``int | str | bytes``.

    Only accepted when there's a ``|`` outside any call parens and every segment
    is type-ish — a heuristic that avoids matching bitwise OR.
    """
    head = rhs.split("(", 1)[0]
    if "|" not in head:
        return False
    return all(
        part.strip()
        and part.strip()[0].isupper()
        or part.strip() in ("None", "int", "str", "float", "bytes", "bool", "list", "dict", "set", "tuple")
        for part in head.split("|")
    )


def _rhs_is_pascal_alias(rhs: str) -> bool:
    """Single-token PascalCase RHS → alias to another typedef.

    The ``PluginAPI = RoamPluginContext`` case. Restricted to clean identifiers
    (no parens, no subscript) so ordinary instantiation like ``foo = SomeClass()``
    is not matched.
    """
    if "(" in rhs or "[" in rhs or not re.match(r"^[A-Za-z_][\w\.]*$", rhs):
        return False
    head_ident = rhs.split(".")[-1]
    return bool(_SINGLE_PASCAL.match(head_ident) or _CASE_PATTERNS["PascalCase"].match(head_ident))


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
    if _sig_is_pep695_or_typealias(sig):
        return True
    # Signature shape is always ``<lhs> = <rhs>`` for module assignments per
    # ``python_lang._extract_module_assignment``.
    if "=" not in sig:
        return False
    rhs = sig.split("=", 1)[1].strip()
    if not rhs:
        return False
    return _rhs_is_newtype_or_generic(rhs) or _rhs_is_pipe_union(rhs) or _rhs_is_pascal_alias(rhs)


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
