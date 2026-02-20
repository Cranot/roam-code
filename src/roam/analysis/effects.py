"""Effect classification and propagation for roam-code.

Classifies what functions DO (read DB, write DB, network I/O, filesystem
access, etc.) and propagates effects through the call graph so callers
inherit the transitive effects of their callees.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Effect taxonomy
# ---------------------------------------------------------------------------

PURE = "pure"
READS_DB = "reads_db"
WRITES_DB = "writes_db"
NETWORK = "network"
FILESYSTEM = "filesystem"
TIME = "time"
RANDOM = "random"
MUTATES_GLOBAL = "mutates_global"
CACHE = "cache"
QUEUE = "queue"
LOGGING = "logging"

ALL_EFFECTS = frozenset({
    PURE, READS_DB, WRITES_DB, NETWORK, FILESYSTEM,
    TIME, RANDOM, MUTATES_GLOBAL, CACHE, QUEUE, LOGGING,
})

# ---------------------------------------------------------------------------
# Framework-aware pattern dictionaries
#
# Each entry: (compiled_regex, effect_type)
# Patterns are matched against function body text (between line_start and
# line_end). We compile once at import time for performance.
# ---------------------------------------------------------------------------

def _compile(patterns: list[tuple[str, str]]) -> list[tuple[re.Pattern, str]]:
    """Compile (regex_str, effect) pairs."""
    return [(re.compile(p, re.IGNORECASE), e) for p, e in patterns]


_PYTHON_PATTERNS = _compile([
    # Database writes
    (r"\.save\s*\(", WRITES_DB),
    (r"\.create\s*\(", WRITES_DB),
    (r"\.delete\s*\(", WRITES_DB),
    (r"\.update\s*\(", WRITES_DB),
    (r"\.bulk_create\s*\(", WRITES_DB),
    (r"\.bulk_update\s*\(", WRITES_DB),
    (r"\.execute\s*\(", WRITES_DB),
    (r"\.executemany\s*\(", WRITES_DB),
    (r"cursor\.", WRITES_DB),
    (r"\.commit\s*\(", WRITES_DB),
    (r"\.add\s*\(", WRITES_DB),
    (r"session\.flush", WRITES_DB),
    (r"\.insert\s*\(", WRITES_DB),
    # Database reads
    (r"\.objects\.", READS_DB),
    (r"\.filter\s*\(", READS_DB),
    (r"\.get\s*\(", READS_DB),
    (r"\.all\s*\(", READS_DB),
    (r"\.select\s*\(", READS_DB),
    (r"\.fetchone\s*\(", READS_DB),
    (r"\.fetchall\s*\(", READS_DB),
    (r"\.fetchmany\s*\(", READS_DB),
    (r"\.query\s*\(", READS_DB),
    # Network
    (r"requests\.\w+\s*\(", NETWORK),
    (r"httpx\.\w+\s*\(", NETWORK),
    (r"urllib\.", NETWORK),
    (r"aiohttp\.", NETWORK),
    (r"urlopen\s*\(", NETWORK),
    (r"socket\.", NETWORK),
    (r"grpc\.", NETWORK),
    # Filesystem
    (r"\bopen\s*\(", FILESYSTEM),
    (r"Path\s*\(", FILESYSTEM),
    (r"\bos\.(?:path|remove|rename|mkdir|rmdir|listdir|walk|unlink|stat)", FILESYSTEM),
    (r"shutil\.", FILESYSTEM),
    (r"pathlib\.", FILESYSTEM),
    (r"\.read_text\s*\(", FILESYSTEM),
    (r"\.write_text\s*\(", FILESYSTEM),
    (r"\.read_bytes\s*\(", FILESYSTEM),
    (r"\.write_bytes\s*\(", FILESYSTEM),
    # Time
    (r"time\.\w+\s*\(", TIME),
    (r"datetime\.", TIME),
    (r"sleep\s*\(", TIME),
    # Random
    (r"random\.\w+\s*\(", RANDOM),
    (r"secrets\.", RANDOM),
    (r"uuid\.", RANDOM),
    # Global mutation
    (r"\bglobal\s+\w+", MUTATES_GLOBAL),
    (r"os\.environ\[", MUTATES_GLOBAL),
    # Cache
    (r"@cache\b", CACHE),
    (r"@lru_cache", CACHE),
    (r"@cached", CACHE),
    (r"\.cache\.", CACHE),
    (r"redis\.", CACHE),
    (r"memcache", CACHE),
    # Queue
    (r"\.send_message\s*\(", QUEUE),
    (r"\.publish\s*\(", QUEUE),
    (r"\.put\s*\(", QUEUE),
    (r"celery\.", QUEUE),
    (r"\.delay\s*\(", QUEUE),
    (r"\.apply_async\s*\(", QUEUE),
    # Logging
    (r"logger\.\w+\s*\(", LOGGING),
    (r"logging\.\w+\s*\(", LOGGING),
    (r"\blog\.\w+\s*\(", LOGGING),
    (r"print\s*\(", LOGGING),
])

_JAVASCRIPT_PATTERNS = _compile([
    # Network
    (r"\bfetch\s*\(", NETWORK),
    (r"axios\.\w+\s*\(", NETWORK),
    (r"XMLHttpRequest", NETWORK),
    (r"\.ajax\s*\(", NETWORK),
    (r"http\.\w+\s*\(", NETWORK),
    (r"ws\.send\s*\(", NETWORK),
    (r"WebSocket\s*\(", NETWORK),
    # Database writes
    (r"\.save\s*\(", WRITES_DB),
    (r"\.create\s*\(", WRITES_DB),
    (r"\.insertOne\s*\(", WRITES_DB),
    (r"\.insertMany\s*\(", WRITES_DB),
    (r"\.updateOne\s*\(", WRITES_DB),
    (r"\.updateMany\s*\(", WRITES_DB),
    (r"\.deleteOne\s*\(", WRITES_DB),
    (r"\.deleteMany\s*\(", WRITES_DB),
    (r"\.destroy\s*\(", WRITES_DB),
    (r"\.execute\s*\(", WRITES_DB),
    (r"\.query\s*\(", WRITES_DB),
    (r"\.run\s*\(", WRITES_DB),
    # Database reads
    (r"\.find\s*\(", READS_DB),
    (r"\.findOne\s*\(", READS_DB),
    (r"\.findById\s*\(", READS_DB),
    (r"\.findAll\s*\(", READS_DB),
    (r"\.select\s*\(", READS_DB),
    (r"\.where\s*\(", READS_DB),
    # Filesystem
    (r"fs\.\w+\s*\(", FILESYSTEM),
    (r"readFile\w*\s*\(", FILESYSTEM),
    (r"writeFile\w*\s*\(", FILESYSTEM),
    (r"\.createReadStream\s*\(", FILESYSTEM),
    (r"\.createWriteStream\s*\(", FILESYSTEM),
    # Time
    (r"setTimeout\s*\(", TIME),
    (r"setInterval\s*\(", TIME),
    (r"Date\.\w+\s*\(", TIME),
    (r"new Date\s*\(", TIME),
    # Random
    (r"Math\.random\s*\(", RANDOM),
    (r"crypto\.random", RANDOM),
    # Global mutation
    (r"globalThis\.", MUTATES_GLOBAL),
    (r"window\.\w+\s*=", MUTATES_GLOBAL),
    (r"process\.env\.", MUTATES_GLOBAL),
    # Cache
    (r"localStorage\.", CACHE),
    (r"sessionStorage\.", CACHE),
    (r"\.setItem\s*\(", CACHE),
    (r"\.getItem\s*\(", CACHE),
    # Queue
    (r"\.emit\s*\(", QUEUE),
    (r"\.publish\s*\(", QUEUE),
    (r"\.postMessage\s*\(", QUEUE),
    # Logging
    (r"console\.\w+\s*\(", LOGGING),
])

_PHP_PATTERNS = _compile([
    # Database writes
    (r"->save\s*\(", WRITES_DB),
    (r"::create\s*\(", WRITES_DB),
    (r"->insert\s*\(", WRITES_DB),
    (r"->update\s*\(", WRITES_DB),
    (r"->delete\s*\(", WRITES_DB),
    (r"DB::insert\b", WRITES_DB),
    (r"DB::update\b", WRITES_DB),
    (r"DB::delete\b", WRITES_DB),
    (r"DB::statement\b", WRITES_DB),
    (r"->execute\s*\(", WRITES_DB),
    (r"->exec\s*\(", WRITES_DB),
    # Database reads
    (r"DB::select\b", READS_DB),
    (r"DB::table\b", READS_DB),
    (r"->get\s*\(", READS_DB),
    (r"->find\s*\(", READS_DB),
    (r"->first\s*\(", READS_DB),
    (r"->where\s*\(", READS_DB),
    (r"->select\s*\(", READS_DB),
    (r"->fetchAll\s*\(", READS_DB),
    (r"->fetch\s*\(", READS_DB),
    # Network
    (r"curl_\w+\s*\(", NETWORK),
    (r"file_get_contents\s*\(", NETWORK),
    (r"Http::", NETWORK),
    (r"Guzzle", NETWORK),
    (r"->request\s*\(", NETWORK),
    # Filesystem
    (r"fopen\s*\(", FILESYSTEM),
    (r"fwrite\s*\(", FILESYSTEM),
    (r"fread\s*\(", FILESYSTEM),
    (r"file_put_contents\s*\(", FILESYSTEM),
    (r"unlink\s*\(", FILESYSTEM),
    (r"mkdir\s*\(", FILESYSTEM),
    (r"rmdir\s*\(", FILESYSTEM),
    (r"is_file\s*\(", FILESYSTEM),
    # Time
    (r"time\s*\(", TIME),
    (r"strtotime\s*\(", TIME),
    (r"Carbon::", TIME),
    (r"new DateTime\b", TIME),
    (r"sleep\s*\(", TIME),
    # Random
    (r"rand\s*\(", RANDOM),
    (r"mt_rand\s*\(", RANDOM),
    (r"random_\w+\s*\(", RANDOM),
    (r"Str::random\s*\(", RANDOM),
    # Global mutation
    (r"\$GLOBALS\[", MUTATES_GLOBAL),
    (r"\$_SESSION\[", MUTATES_GLOBAL),
    (r"putenv\s*\(", MUTATES_GLOBAL),
    # Cache
    (r"Cache::", CACHE),
    (r"->remember\s*\(", CACHE),
    (r"->forever\s*\(", CACHE),
    (r"Redis::", CACHE),
    # Queue
    (r"dispatch\s*\(", QUEUE),
    (r"Queue::", QUEUE),
    (r"->onQueue\s*\(", QUEUE),
    (r"Event::", QUEUE),
    # Logging
    (r"Log::", LOGGING),
    (r"->info\s*\(", LOGGING),
    (r"->error\s*\(", LOGGING),
    (r"->warning\s*\(", LOGGING),
    (r"error_log\s*\(", LOGGING),
])

_GO_PATTERNS = _compile([
    # Database
    (r"\.Query\w*\s*\(", READS_DB),
    (r"\.Exec\w*\s*\(", WRITES_DB),
    (r"\.Prepare\s*\(", READS_DB),
    (r"tx\.Commit\s*\(", WRITES_DB),
    # Network
    (r"http\.\w+\s*\(", NETWORK),
    (r"net\.Dial\w*\s*\(", NETWORK),
    (r"grpc\.", NETWORK),
    # Filesystem
    (r"os\.(?:Open|Create|Remove|Mkdir|ReadFile|WriteFile|Stat)", FILESYSTEM),
    (r"ioutil\.", FILESYSTEM),
    (r"io\.Read", FILESYSTEM),
    # Time
    (r"time\.Now\s*\(", TIME),
    (r"time\.Sleep\s*\(", TIME),
    # Random
    (r"rand\.\w+\s*\(", RANDOM),
    # Logging
    (r"log\.\w+\s*\(", LOGGING),
    (r"fmt\.Print", LOGGING),
])

# Language -> pattern list mapping
_LANGUAGE_PATTERNS: dict[str, list[tuple[re.Pattern, str]]] = {
    "python": _PYTHON_PATTERNS,
    "javascript": _JAVASCRIPT_PATTERNS,
    "typescript": _JAVASCRIPT_PATTERNS,
    "tsx": _JAVASCRIPT_PATTERNS,
    "jsx": _JAVASCRIPT_PATTERNS,
    "php": _PHP_PATTERNS,
    "go": _GO_PATTERNS,
}


# ---------------------------------------------------------------------------
# String/comment exclusion via tree-sitter
# ---------------------------------------------------------------------------

_STRING_COMMENT_TYPES = frozenset({
    "string", "string_literal", "template_string", "raw_string",
    "comment", "line_comment", "block_comment",
    "string_content", "interpreted_string_literal",
    "encapsed_string", "heredoc_body", "nowdoc_body",
})


def _collect_excluded_ranges(tree, source: bytes,
                             line_start: int, line_end: int) -> list[tuple[int, int]]:
    """Collect byte ranges of strings/comments within [line_start, line_end].

    Returns list of (start_byte, end_byte) that should be excluded from
    pattern matching.
    """
    ranges = []
    if tree is None:
        return ranges

    # Convert line numbers to byte offsets for the body
    lines = source.split(b"\n")
    body_start_byte = sum(len(lines[i]) + 1 for i in range(min(line_start - 1, len(lines))))
    body_end_byte = sum(len(lines[i]) + 1 for i in range(min(line_end, len(lines))))

    def _walk(node):
        # Skip nodes entirely outside the body range
        if node.end_byte <= body_start_byte or node.start_byte >= body_end_byte:
            return
        if node.type in _STRING_COMMENT_TYPES:
            ranges.append((node.start_byte - body_start_byte,
                           node.end_byte - body_start_byte))
            return
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return ranges


def _in_excluded(pos: int, excluded: list[tuple[int, int]]) -> bool:
    """Check if byte position is inside any excluded range."""
    for start, end in excluded:
        if start <= pos < end:
            return True
    return False


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_symbol_effects(
    body_text: str,
    language: str,
    tree=None,
    source: bytes | None = None,
    line_start: int = 0,
    line_end: int = 0,
) -> set[str]:
    """Classify the side effects of a function body.

    Args:
        body_text: The text of the function body (lines between line_start..line_end).
        language: Programming language identifier.
        tree: Optional tree-sitter parse tree for AST-aware string/comment filtering.
        source: Full file source bytes (needed with tree for range mapping).
        line_start: 1-based start line of the function.
        line_end: 1-based end line of the function.

    Returns:
        Set of effect type strings (e.g. {"reads_db", "network"}).
        Empty set if no effects detected (the function is considered pure).
    """
    patterns = _LANGUAGE_PATTERNS.get(language, [])
    if not patterns:
        return set()

    # Build excluded ranges from tree-sitter AST if available
    excluded: list[tuple[int, int]] = []
    if tree is not None and source is not None and line_start > 0:
        excluded = _collect_excluded_ranges(tree, source, line_start, line_end)

    effects: set[str] = set()

    for pattern, effect in patterns:
        for match in pattern.finditer(body_text):
            # If we have AST info, check if match is inside a string/comment
            if excluded and _in_excluded(match.start(), excluded):
                continue
            effects.add(effect)
            break  # One match per pattern is enough

    return effects


def classify_file_effects(
    conn,
    file_id: int,
    source: bytes,
    language: str,
    tree=None,
) -> dict[int, set[str]]:
    """Classify effects for all symbols in a file.

    Args:
        conn: SQLite connection.
        file_id: File ID in the database.
        source: Full file source bytes.
        language: Programming language.
        tree: Optional tree-sitter parse tree.

    Returns:
        {symbol_id: set[str]} mapping symbol IDs to their direct effects.
    """
    if language not in _LANGUAGE_PATTERNS:
        return {}

    rows = conn.execute(
        "SELECT id, kind, line_start, line_end FROM symbols "
        "WHERE file_id = ? AND kind IN ('function', 'method', 'constructor')",
        (file_id,),
    ).fetchall()

    if not rows:
        return {}

    lines = source.decode("utf-8", errors="replace").split("\n")
    results: dict[int, set[str]] = {}

    for row in rows:
        sym_id = row["id"]
        ls = row["line_start"] or 1
        le = row["line_end"] or len(lines)

        # Extract body text (1-based lines)
        body_lines = lines[max(0, ls - 1):le]
        body_text = "\n".join(body_lines)

        effects = classify_symbol_effects(
            body_text, language,
            tree=tree, source=source,
            line_start=ls, line_end=le,
        )
        if effects:
            results[sym_id] = effects

    return results


# ---------------------------------------------------------------------------
# Propagation
# ---------------------------------------------------------------------------


def propagate_effects(
    G,
    direct_effects: dict[int, set[str]],
) -> dict[int, set[str]]:
    """Propagate effects through the call graph (bottom-up).

    For each node, its transitive effects = direct effects UNION
    effects of all callees. Uses reverse topological sort where
    possible, with iteration for cycles.

    Args:
        G: NetworkX DiGraph (symbol graph).
        direct_effects: {symbol_id: set[effect_str]} from classification.

    Returns:
        {symbol_id: set[str]} with transitive effects for all nodes
        that have at least one effect (direct or inherited).
    """
    import networkx as nx

    # Initialize with direct effects
    all_effects: dict[int, set[str]] = {}
    for sid, effects in direct_effects.items():
        if sid in G:
            all_effects[sid] = set(effects)

    # Try topological order on condensation (handles cycles)
    try:
        condensation = nx.condensation(G)
        # node_mapping: condensation node -> set of original nodes
        members = condensation.graph.get("mapping", {})
        # Reverse: original -> condensation
        orig_to_scc: dict[int, int] = {}
        for orig, scc_id in members.items():
            orig_to_scc[orig] = scc_id

        # Process in reverse topological order (leaves first)
        for scc_id in reversed(list(nx.topological_sort(condensation))):
            scc_nodes = [n for n, s in orig_to_scc.items() if s == scc_id]

            # Within an SCC, all nodes share the same effects
            scc_effects: set[str] = set()
            for n in scc_nodes:
                scc_effects.update(all_effects.get(n, set()))

            # Collect effects from successors (callees outside this SCC)
            for succ_scc in condensation.successors(scc_id):
                succ_nodes = [n for n, s in orig_to_scc.items() if s == succ_scc]
                for sn in succ_nodes:
                    scc_effects.update(all_effects.get(sn, set()))

            # Apply to all nodes in this SCC
            if scc_effects:
                for n in scc_nodes:
                    all_effects[n] = scc_effects.copy()

    except Exception:
        # Fallback: simple iterative propagation
        changed = True
        max_iters = 20
        iteration = 0
        while changed and iteration < max_iters:
            changed = False
            iteration += 1
            for node in G.nodes():
                current = all_effects.get(node, set())
                for succ in G.successors(node):
                    succ_effects = all_effects.get(succ, set())
                    new = succ_effects - current
                    if new:
                        all_effects.setdefault(node, set()).update(new)
                        changed = True

    return all_effects


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def store_effects(
    conn,
    all_effects: dict[int, set[str]],
    direct_effects: dict[int, set[str]],
):
    """Persist classified effects to the symbol_effects table.

    Args:
        conn: SQLite connection (writable).
        all_effects: {symbol_id: set[str]} — full (transitive) effects.
        direct_effects: {symbol_id: set[str]} — only direct effects.
    """
    conn.execute("DELETE FROM symbol_effects")

    rows = []
    for sym_id, effects in all_effects.items():
        direct = direct_effects.get(sym_id, set())
        for effect in effects:
            source = "direct" if effect in direct else "transitive"
            rows.append((sym_id, effect, source))

    if rows:
        conn.executemany(
            "INSERT INTO symbol_effects (symbol_id, effect_type, source) "
            "VALUES (?, ?, ?)",
            rows,
        )


# ---------------------------------------------------------------------------
# Indexer integration entry point
# ---------------------------------------------------------------------------


def compute_and_store_effects(conn, root, G=None):
    """Full effects pipeline: classify per file, propagate, store.

    Called from the indexer after graph construction.
    """
    from roam.index.parser import parse_file

    # 1. Classify direct effects for all files
    direct_effects: dict[int, set[str]] = {}

    files = conn.execute("SELECT id, path, language FROM files").fetchall()
    for file_row in files:
        file_id = file_row["id"]
        language = file_row["language"]

        if language not in _LANGUAGE_PATTERNS:
            continue

        full_path = root / file_row["path"]
        try:
            with open(full_path, "rb") as f:
                source = f.read()
        except OSError:
            continue

        # Parse for AST-aware filtering
        tree, parsed_source, lang = parse_file(full_path, language)

        effects = classify_file_effects(
            conn, file_id, source, language, tree=tree,
        )
        direct_effects.update(effects)

    if not direct_effects:
        return

    # 2. Propagate through call graph
    if G is not None:
        all_effects = propagate_effects(G, direct_effects)
    else:
        all_effects = dict(direct_effects)

    # 3. Store
    store_effects(conn, all_effects, direct_effects)
