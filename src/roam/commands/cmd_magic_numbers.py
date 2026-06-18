"""``roam magic-numbers`` — scan source for hardcoded numeric constants

SARIF is deliberately NOT emitted: this is an advisory heuristic scan; findings are surfaced in the JSON envelope rather than as a blocking SARIF stream.
that should be named.

**Python path (legacy / authoritative).** Walks ``ast.Constant`` nodes
filtering ``isinstance(node.value, (int, float))`` (excluding bool, which
Python's type hierarchy says is an ``int``). Skips test files (any path
component is ``tests``), trivial numbers (0, 1, -1, 2) unless
``--include-trivial`` is set, and ``__version__`` literals.

**Cross-language path (tree-sitter).** For files in JavaScript /
TypeScript / Go / Rust / Java / Ruby / C / C# the scanner detects the
language via ``roam.languages.registry.get_language_for_file`` and walks
the tree-sitter parse tree looking for numeric-literal node kinds
(``number_literal``, ``integer_literal``, ``float_literal``, ``int_lit``,
``float_lit``, etc.). When a tree-sitter grammar isn't available for a
file the scanner falls back to a regex sweep for numeric tokens; this
keeps results coming for environments without ``tree-sitter-language-pack``
installed and is documented inline.

The Python AST path remains the default for ``.py`` files (no behavior
change without ``--cluster``).

Output follows the canonical ``json_envelope`` shape with LAW-4-anchored
``agent_contract.facts`` (terminal tokens: ``files``, ``findings``,
``files_scanned``, ``clusters``).

Usage::

    roam magic-numbers
    roam magic-numbers src/
    roam magic-numbers --threshold 3
    roam magic-numbers --include-trivial
    roam magic-numbers --cluster
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_TRIVIAL_VALUES: frozenset[int] = frozenset({0, 1, -1, 2})

# Extensions the scanner walks. Python keeps the dedicated AST path; the
# rest go through the tree-sitter / regex-fallback path.
_PY_EXTS: frozenset[str] = frozenset({".py"})
_TS_LANG_EXTS: frozenset[str] = frozenset(
    {
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".rb",
        ".c",
        ".h",
        ".cs",
    }
)

# Tree-sitter numeric-literal node kinds, by language. Each value is a
# set of node ``type`` strings that represent a numeric literal in that
# language's grammar (verified against tree-sitter-language-pack 0.6).
# Unknown / missing kinds collapse to the regex fallback.
_TS_NUMERIC_NODE_KINDS: dict[str, frozenset[str]] = {
    "javascript": frozenset({"number"}),
    "typescript": frozenset({"number"}),
    "tsx": frozenset({"number"}),
    "go": frozenset({"int_literal", "float_literal", "imaginary_literal"}),
    "rust": frozenset({"integer_literal", "float_literal"}),
    "java": frozenset(
        {
            "decimal_integer_literal",
            "hex_integer_literal",
            "octal_integer_literal",
            "binary_integer_literal",
            "decimal_floating_point_literal",
            "hex_floating_point_literal",
        }
    ),
    "ruby": frozenset({"integer", "float", "rational", "complex"}),
    "c": frozenset({"number_literal"}),
    "c_sharp": frozenset({"integer_literal", "real_literal"}),
}

# Regex fallback for languages where tree-sitter is unavailable or where
# the grammar node kinds drift. Catches decimal, hex, binary, octal,
# floating point, scientific notation. Anchored with non-word lookbehind
# / lookahead to avoid matching the tail of an identifier (``foo42``).
_NUMBER_RE = re.compile(
    r"(?<![\w.$])("
    r"0[xX][0-9a-fA-F_]+"
    r"|0[bB][01_]+"
    r"|0[oO]?[0-7_]+"
    r"|\d[\d_]*\.\d[\d_]*(?:[eE][+-]?\d+)?"
    r"|\d[\d_]*(?:[eE][+-]?\d+)?"
    r"|\.\d[\d_]*(?:[eE][+-]?\d+)?"
    r")(?![\w.])"
)


def _is_test_path(p: Path) -> bool:
    """True if any path component is a test directory or the file name
    starts with ``test_``."""
    parts = {part.lower() for part in p.parts}
    if "tests" in parts or "test" in parts:
        return True
    return p.name.startswith("test_")


def _line_snippet(source_lines: list[str], lineno: int) -> str:
    """Return a single-line snippet for the given 1-indexed lineno.

    Truncates long lines to keep envelope budgets sane."""
    idx = lineno - 1
    if 0 <= idx < len(source_lines):
        snippet = source_lines[idx].strip()
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        return snippet
    return ""


def _extract_numeric_literals(
    tree: ast.AST,
    source_lines: list[str],
    file_str: str,
) -> list[tuple[int | float, int, str]]:
    """Walk ``tree`` and return ``(value, lineno, snippet)`` triples for
    each non-bool numeric ``ast.Constant``. Skips:

    - numbers used as the value of ``__version__ = ...`` assignments
    - numbers inside ``__future__`` imports (no numeric literals here in
      practice, but the guard is cheap)
    """
    # Collect linenos that are part of a __version__ = ... assignment so we
    # can suppress them in the walk below.
    version_linenos: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__version__":
                    # Record every lineno spanned by the RHS literal.
                    rhs = node.value
                    if hasattr(rhs, "lineno") and rhs.lineno is not None:
                        version_linenos.add(rhs.lineno)
        elif isinstance(node, ast.AnnAssign):
            tgt = node.target
            if isinstance(tgt, ast.Name) and tgt.id == "__version__":
                rhs = node.value
                if rhs is not None and getattr(rhs, "lineno", None) is not None:
                    version_linenos.add(rhs.lineno)

    out: list[tuple[int | float, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        if isinstance(value, bool):
            continue
        if not isinstance(value, (int, float)):
            continue
        lineno = getattr(node, "lineno", None)
        if lineno is None:
            continue
        if lineno in version_linenos:
            continue
        snippet = _line_snippet(source_lines, lineno)
        out.append((value, lineno, snippet))
    return out


def _scan_python_file(
    path: Path,
    include_trivial: bool,
) -> list[tuple[int | float, int, str]]:
    """Return the raw numeric-literal occurrences for a Python ``path``.

    Caller aggregates across files before applying the threshold filter.
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []
    source_lines = src.splitlines()
    raw = _extract_numeric_literals(tree, source_lines, str(path))
    if include_trivial:
        return raw
    return [(v, ln, sn) for (v, ln, sn) in raw if not (isinstance(v, int) and v in _TRIVIAL_VALUES)]


# Back-compat alias: existing tests import ``_scan_file`` and expect the
# Python-AST behaviour with a (path, threshold, include_trivial)
# signature. ``threshold`` is unused at the per-file scan stage (the
# caller aggregates and applies the floor) — preserved for API stability.
def _scan_file(
    path: Path,
    threshold: int,
    include_trivial: bool,
) -> list[tuple[int | float, int, str]]:
    return _scan_python_file(path, include_trivial)


def _parse_number_text(text: str) -> int | float | None:
    """Parse a numeric literal as written in source code into a Python
    number. Strips underscores and language-specific type suffixes
    (Rust ``_i32``, Java ``L``/``f``/``d``, JS ``n`` bigint, Go ``i``).

    Returns ``None`` if the text isn't a parseable number."""
    s = text.strip()
    if not s:
        return None
    # Strip trailing single-letter type suffix common in J/C/Rust/JS:
    # 100L, 1.0f, 1.0d, 100n, 100u, 100ll, 100ull, 1.0_f32, 1i64.
    # Skip suffix stripping for radix-prefixed literals (0x1F, 0b101, 0o17) —
    # in hex literals "F" / "d" / "f" are valid digits, not type suffixes.
    is_radix_prefixed = s.lower().startswith(("0x", "0b", "0o"))
    if not is_radix_prefixed:
        s = re.sub(r"(?:_?(?:i|u|f)\d+|n|[LlFfDdUu]+)$", "", s)
    s = s.replace("_", "")
    if not s:
        return None
    try:
        if s.startswith(("0x", "0X")):
            return int(s, 16)
        if s.startswith(("0b", "0B")):
            return int(s, 2)
        if s.startswith(("0o", "0O")):
            return int(s, 8)
        # Bare leading 0 like 0755 — treat as decimal here (cross-language
        # ambiguity; we err on the side of NOT crashing).
        if "." in s or "e" in s or "E" in s:
            return float(s)
        return int(s)
    except ValueError:
        return None


def _walk_ts_numbers(
    root_node,
    kinds: frozenset[str],
    source_bytes: bytes,
) -> list[tuple[int | float, int, str]]:
    """BFS-walk a tree-sitter node tree collecting ``(value, lineno,
    text)`` triples for nodes whose ``type`` is in ``kinds``."""
    out: list[tuple[int | float, int, str]] = []
    stack = [root_node]
    while stack:
        node = stack.pop()
        if node.type in kinds:
            literal_bytes = source_bytes[node.start_byte : node.end_byte]
            text = literal_bytes.decode("utf-8", errors="replace")
            value = _parse_number_text(text)
            if value is not None and not isinstance(value, bool):
                # tree-sitter start_point is (row, col), 0-indexed row.
                lineno = node.start_point[0] + 1
                out.append((value, lineno, text))
        # Iterate children via the cursor API would be faster, but the
        # children list is simpler and sufficient.
        stack.extend(node.children)
    return out


def _scan_ts_file(
    path: Path,
    language: str,
    include_trivial: bool,
) -> list[tuple[int | float, int, str]]:
    """Scan a non-Python file via tree-sitter. Falls back to the regex
    sweep when the grammar isn't installed or the node kinds are unknown
    for the language."""
    try:
        src_bytes = path.read_bytes()
    except OSError:
        return []
    source_lines = src_bytes.decode("utf-8", errors="replace").splitlines()

    kinds = _TS_NUMERIC_NODE_KINDS.get(language)
    raw: list[tuple[int | float, int, str]] = []
    used_treesitter = False
    if kinds is not None:
        try:
            # NOTE: tree-sitter-language-pack is already a hard dep of
            # roam (see pyproject.toml), but we still guard the import
            # so that a missing grammar (e.g. older language-pack) falls
            # back to the regex sweep instead of crashing the command.
            from tree_sitter_language_pack import get_parser

            from roam.index.parser import GRAMMAR_ALIASES
            from roam.languages.registry import get_ts_language  # noqa: F401

            grammar = GRAMMAR_ALIASES.get(language, language)
            parser = get_parser(grammar)
            tree = parser.parse(src_bytes)
            walked = _walk_ts_numbers(tree.root_node, kinds, src_bytes)
            for value, lineno, _text in walked:
                snippet = _line_snippet(source_lines, lineno)
                raw.append((value, lineno, snippet))
            used_treesitter = True
        except Exception:
            # Fall through to the regex sweep — documented fallback.
            used_treesitter = False

    if not used_treesitter:
        # Regex fallback: scan each line for numeric tokens. This is
        # intentionally permissive — it will over-count compared to a
        # proper AST walk (e.g. a number inside a string literal will
        # match), but it keeps the command useful when the grammar
        # isn't available.
        for lineno, line in enumerate(source_lines, start=1):
            for m in _NUMBER_RE.finditer(line):
                value = _parse_number_text(m.group(1))
                if value is None:
                    continue
                snippet = _line_snippet(source_lines, lineno)
                raw.append((value, lineno, snippet))

    if include_trivial:
        return raw
    return [(v, ln, sn) for (v, ln, sn) in raw if not (isinstance(v, int) and v in _TRIVIAL_VALUES)]


def _discover_files(root: Path) -> list[Path]:
    """Discover source files under ``root`` across the supported
    extensions, skipping test paths. If ``root`` is a file, scan only
    that file (regardless of extension)."""
    if root.is_file():
        if root.suffix in _PY_EXTS or root.suffix in _TS_LANG_EXTS:
            return [root]
        return []
    out: list[Path] = []
    exts = _PY_EXTS | _TS_LANG_EXTS
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in exts:
            continue
        if _is_test_path(p):
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# --cluster semantic grouping
# ---------------------------------------------------------------------------


_CLUSTER_TIMEOUT_SECS: frozenset[int] = frozenset(
    {
        30,
        60,
        120,
        300,
        600,
        1800,
        3600,
        7200,
        86400,
    }
)
_CLUSTER_POW2: frozenset[int] = frozenset(
    {
        8,
        16,
        32,
        64,
        128,
        256,
        512,
        1024,
        2048,
        4096,
        8192,
        16384,
        32768,
        65536,
        131072,
    }
)
_CLUSTER_PORTS: frozenset[int] = frozenset(
    {
        80,
        443,
        3000,
        3306,
        5000,
        5432,
        6379,
        8000,
        8080,
        8443,
        9200,
    }
)


def _cluster_for_value(value: int | float, snippet: str) -> str:
    """Return the semantic-cluster name for ``value``.

    Cluster precedence (first match wins):
    1. timeout_seconds — exact membership in the canonical seconds set
    2. network_port    — exact membership in the well-known port set
    3. size_power_of_two — exact membership in the power-of-two set
    4. percentage      — floats in [0, 1] OR ints in [0, 100] when
                         snippet hints at percentage (``percent``,
                         ``pct``, ``%``, ``ratio``, ``rate``)
    5. http_status     — ints in [100, 599]
    6. uncategorized   — everything else
    """
    # timeout takes precedence over generic size: 60 / 300 / 3600 are
    # canonical timeouts even though they're not powers of two.
    if isinstance(value, int) and value in _CLUSTER_TIMEOUT_SECS:
        return "timeout_seconds"
    if isinstance(value, int) and value in _CLUSTER_PORTS:
        return "network_port"
    if isinstance(value, int) and value in _CLUSTER_POW2:
        return "size_power_of_two"
    if isinstance(value, float) and 0.0 <= value <= 1.0:
        return "percentage"
    if isinstance(value, int) and 0 <= value <= 100:
        lo = snippet.lower()
        if any(k in lo for k in ("percent", "pct", "%", "ratio", "rate")):
            return "percentage"
    if isinstance(value, int) and 100 <= value <= 599:
        return "http_status"
    return "uncategorized"


# Context keyword sets for _cluster_for_value_with_context. Tracked as
# module-level frozensets so the dogfood-FP fix is easy to audit / extend.
_CTX_PORT_KEYWORDS: tuple[str, ...] = (":80", "port", ":443", ":8080")
_CTX_HTTP_KEYWORDS: tuple[str, ...] = (
    "status_code",
    "response.status",
    "http",
    "raise_for_status",
)
_CTX_SIZE_KEYWORDS: tuple[str, ...] = (
    "len(",
    "[:",
    "max_",
    "min_",
    "limit",
    "cap",
    "size",
    "lines",
    "chars",
    "bytes",
)


def _cluster_for_value_with_context(value: int | float, line: str) -> str:
    """Context-aware sibling of ``_cluster_for_value``.

    Peeks at the source-line text to break the systemic FPs observed in
    the dogfood of ``src/roam/plan/compiler.py``:

    - ``sqlite3.connect(path, timeout=1.0)`` mis-classified as
      ``percentage``
    - ``len(task) < 200``, ``_TASK_PREFIX_LEN_CAP = 200`` mis-classified
      as ``http_status``
    - ``_FILE_EXCERPT_LINES = 80`` mis-classified as ``network_port``

    Order matters: the explicit, high-precision cues (timeout / port /
    http) fire before the broader ``size_or_limit`` net so that an
    HTTP-status site sitting next to a ``len(`` token is still tagged as
    ``http_status``. Falls through to the value-only classifier when no
    context cue matches.
    """
    line_lower = (line or "").lower()
    # Highest-precision overrides — explicit semantic cues that flip the
    # value-only verdict (timeout=1.0 -> timeout_seconds, not percentage;
    # `port` keyword -> network_port; HTTP cues -> http_status).
    if "timeout" in line_lower and isinstance(value, float):
        return "timeout_seconds"
    if any(kw in line_lower for kw in _CTX_PORT_KEYWORDS):
        return "network_port"
    if any(kw in line_lower for kw in _CTX_HTTP_KEYWORDS):
        return "http_status"
    # The size-or-limit net is the lowest-precision override: it should
    # only fire when the value-only classifier would otherwise land on
    # one of the FP-prone categories (``http_status`` for limits like
    # ``len(x) < 200`` and ``network_port`` for line-counts like
    # ``_FILE_EXCERPT_LINES = 80``). Letting it pre-empt strong value
    # signals (``timeout_seconds`` / ``size_power_of_two``) would
    # mis-categorize honest buffer sizes (`SIZE = 1024`).
    value_only = _cluster_for_value(value, line)
    if value_only in ("http_status", "network_port"):
        if any(kw in line_lower for kw in _CTX_SIZE_KEYWORDS):
            return "size_or_limit"
    return value_only


def _suggest_constant_name(cluster: str, value: int | float) -> str:
    """Return a suggested constant name for a (cluster, value) pair.

    The shape mirrors typical Python / Go uppercase-snake conventions:
    ``DEFAULT_TIMEOUT_S=30`` for timeouts, ``BUFFER_SIZE=1024`` for
    powers of two, ``HTTP_200`` for HTTP statuses, ``PORT_8080`` for
    network ports. Falls back to ``CONST_<value>`` for uncategorized."""
    if cluster == "timeout_seconds":
        return f"DEFAULT_TIMEOUT_S={int(value)}"
    if cluster == "size_power_of_two":
        return f"BUFFER_SIZE={int(value)}"
    if cluster == "http_status":
        return f"HTTP_{int(value)}"
    if cluster == "network_port":
        return f"PORT_{int(value)}"
    if cluster == "percentage":
        return f"DEFAULT_RATIO={value}"
    if cluster == "size_or_limit":
        # value may be int (most common) or float; preserve as written.
        return f"MAX_LIMIT_{str(value).replace('.', '_').replace('-', 'neg_')}"
    return f"CONST_{str(value).replace('.', '_').replace('-', 'neg_')}"


def _build_clusters(findings: list[dict]) -> dict[str, dict]:
    """Group ``findings`` by semantic cluster.

    Each cluster value:
        {
          "count":  total occurrences across all values in the cluster,
          "values": [v1, v2, ...] sorted by descending occurrence,
          "sites":  first 3 sites across the whole cluster,
          "suggested_constant": str — the suggestion for the
                                most-frequent value in the cluster,
        }
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        value = f["value"]
        sites = f["sites"]
        # Use the first site's snippet as the cluster-context hint. The
        # context-aware classifier kills the systemic FPs (timeout=1.0
        # mis-tagged as percentage, len(x) < 200 mis-tagged as
        # http_status, etc.) and falls back to the value-only heuristic
        # when no context cue matches.
        hint_snippet = sites[0]["context_snippet"] if sites else ""
        cluster = _cluster_for_value_with_context(value, hint_snippet)
        buckets[cluster].append(f)

    out: dict[str, dict] = {}
    for cluster, items in buckets.items():
        # Sort items in the cluster by occurrence count DESC so the
        # suggestion + top sites come from the most-frequent value.
        items_sorted = sorted(items, key=lambda f: (-f["occurrences"], str(f["value"])))
        all_sites = [s for f in items_sorted for s in f["sites"]]
        top_value = items_sorted[0]["value"]
        out[cluster] = {
            "count": sum(f["occurrences"] for f in items_sorted),
            "values": [f["value"] for f in items_sorted],
            "sites": all_sites[:3],
            "suggested_constant": _suggest_constant_name(cluster, top_value),
        }
    return out


@roam_capability(
    name="magic-numbers",
    category="health",
    summary="Scan source for hardcoded numeric constants that should be named (Python AST + tree-sitter)",
    inputs=("path", "--threshold", "--include-trivial", "--cluster"),
    outputs=("findings_envelope",),
)
@click.command(name="magic-numbers")
@click.argument("path", required=False, default="src/", type=click.Path(file_okay=True, dir_okay=True))
@click.option("--threshold", type=int, default=2, help="Flag numbers appearing >= N times (default 2).")
@click.option("--include-trivial", is_flag=True, help="Also flag 0, 1, -1, 2 (skipped by default).")
@click.option(
    "--cluster",
    is_flag=True,
    help="Group findings into semantic clusters (timeout, size, port, http_status, percentage, size_or_limit, uncategorized).",
)
@click.pass_context
def magic_numbers(ctx, path, threshold, include_trivial, cluster):
    """Scan source for hardcoded numeric constants that should be
    named constants. Python via ``ast``; JS/TS/Go/Rust/Java/Ruby/C/C#
    via tree-sitter (with a regex fallback when the grammar isn't
    available)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = Path(path)

    if not root.exists():
        verdict = f"path not found: {path}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "magic-numbers",
                        summary={"verdict": verdict, "partial_success": False},
                        path=str(path),
                        error="path_not_found",
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)
        return

    files = _discover_files(root)

    # Aggregate occurrences across all files keyed by numeric value.
    by_value: dict[int | float, list[dict]] = defaultdict(list)
    for f in files:
        if f.suffix in _PY_EXTS:
            occs = _scan_python_file(f, include_trivial)
        else:
            from roam.languages.registry import get_language_for_file

            language = get_language_for_file(str(f)) or ""
            occs = _scan_ts_file(f, language, include_trivial)
        for value, lineno, snippet in occs:
            by_value[value].append(
                {
                    "file": str(f),
                    "line": lineno,
                    "context_snippet": snippet,
                }
            )

    # Filter by threshold.
    findings: list[dict] = []
    for value, sites in by_value.items():
        if len(sites) < threshold:
            continue
        findings.append(
            {
                "value": value,
                "occurrences": len(sites),
                "sites": sorted(sites, key=lambda s: (s["file"], s["line"])),
            }
        )
    findings.sort(key=lambda f: (-f["occurrences"], str(f["value"])))

    files_scanned = len(files)
    files_with_findings = len({s["file"] for f in findings for s in f["sites"]})

    clusters: dict[str, dict] | None = None
    if cluster:
        clusters = _build_clusters(findings)

    if findings:
        top = findings[0]
        verdict = (
            f"{len(findings)} magic numbers across {files_with_findings} files "
            f"(top: `{top['value']}` in {top['occurrences']} sites)"
        )
    else:
        verdict = f"0 magic numbers across {files_scanned} files scanned"

    if clusters is not None:
        # Re-shape the verdict to call out cluster counts. Ordered by
        # cluster size DESC for actionability.
        cluster_summary = ", ".join(
            f"{name} ({info['count']})" for name, info in sorted(clusters.items(), key=lambda kv: -kv[1]["count"])
        )
        verdict = (
            f"{len(findings)} magic numbers across {files_with_findings} files; "
            f"{len(clusters)} clusters: {cluster_summary}"
        )

    facts = [
        f"{len(findings)} magic numbers across {files_with_findings} files",
        f"{files_scanned} files scanned",
        f"threshold {threshold} occurrences",
    ]
    if findings:
        top = findings[0]
        facts.append(f"top value `{top['value']}` at {top['occurrences']} sites")
    if clusters is not None:
        facts.append(f"{len(clusters)} clusters")

    summary = {
        "verdict": verdict,
        "findings_count": len(findings),
        "files_scanned": files_scanned,
        "threshold_used": threshold,
        "include_trivial": include_trivial,
    }
    if clusters is not None:
        summary["cluster_count"] = len(clusters)

    if json_mode:
        envelope_kwargs: dict = dict(
            summary=summary,
            path=str(path),
            files_scanned=files_scanned,
            threshold_used=threshold,
            include_trivial=include_trivial,
            findings=findings,
            agent_contract={"facts": facts},
        )
        if clusters is not None:
            envelope_kwargs["clusters"] = clusters
        click.echo(to_json(json_envelope("magic-numbers", **envelope_kwargs)))
        return

    # Text output
    click.echo(f"VERDICT: {verdict}")
    click.echo(f"scanned: {files_scanned} files (path={path})")
    click.echo(f"threshold: >={threshold} occurrences (include_trivial={include_trivial})")
    if clusters is not None and clusters:
        click.echo("")
        click.echo("clusters:")
        for name, info in sorted(clusters.items(), key=lambda kv: -kv[1]["count"]):
            click.echo(
                f"  {name}: {info['count']} occurrences across "
                f"{len(info['values'])} values  "
                f"-> suggest {info['suggested_constant']}"
            )
            for site in info["sites"]:
                click.echo(f"    {site['file']}:{site['line']}  {site['context_snippet']}")
    if not findings:
        return
    click.echo("")
    for f in findings[:25]:
        click.echo(f"  {f['value']!r}: {f['occurrences']} occurrences")
        for site in f["sites"][:3]:
            click.echo(f"    {site['file']}:{site['line']}  {site['context_snippet']}")
        if len(f["sites"]) > 3:
            click.echo(f"    (+{len(f['sites']) - 3} more sites)")
    if len(findings) > 25:
        click.echo(f"  (+{len(findings) - 25} more findings)")
