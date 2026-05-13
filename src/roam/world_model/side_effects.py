"""Side-effects detector — coarse, agent-friendly per-symbol classification.

Heuristic detector — false negatives expected, false positives should be rare.

Classifies each symbol into zero-or-more of the following kinds:

- ``none``      — pure function (no I/O, no mutation, no spawn).
- ``io_read``   — reads from disk, network, DB, or env.
- ``io_write``  — writes to disk, network, DB, or process state.
- ``mutation``  — mutates a global / module / class-level binding.
- ``process``   — spawns a subprocess / thread / async-task / signal.
- ``unknown``   — has outgoing edges or imports we can't classify, but no
                  evidence either way.

The taxonomy is intentionally **coarser** than
:mod:`roam.analysis.effects` (11 kinds + propagation): coarse kinds are
what agents act on (`io_write` → "be careful"), fine kinds drive
attestation / taint.

Detection strategy
==================

For each symbol that is a function / method / constructor we combine
three signals (cheapest first):

1. **Outgoing call edges** — join ``edges`` to the target symbol's
   ``qualified_name`` / ``file_path`` and look for known
   side-effecting prefixes (``requests.``, ``subprocess.``, ``open``,
   etc.).  This is high-precision because the indexer resolved the
   names.

2. **Source-text patterns** — read the symbol body (``line_start`` →
   ``line_end``) from the file on disk and grep for the same
   well-known anchors as a fallback (cheap when the indexer didn't
   resolve a call, e.g. ``with open(...)``).

3. **File-level imports** — if step 1+2 turned up nothing but the
   file imports e.g. ``boto3`` / ``psycopg2``, mark the symbol
   ``unknown`` (signal exists, evidence per-symbol does not).

Pure functions (no outgoing edges, no patterns, no side-effecting
imports) collapse to ``["none"]``.

The detector is intended to run sub-second on the 18K-symbol roam-code
DB.  All disk reads are batched per-file (one read per file, not per
symbol).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from roam.db.connection import find_project_root

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

SIDE_EFFECT_KINDS = ("none", "io_read", "io_write", "mutation", "process", "unknown")


@dataclass
class SideEffectClassification:
    """Per-symbol coarse side-effects classification."""

    symbol: str
    file: str
    kinds: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    # "high"  — call edge OR multiple source patterns matched
    # "medium" — single source pattern matched
    # "low"   — only file-level imports matched
    confidence: str = "low"
    symbol_id: int = 0
    line_start: int = 0
    line_end: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "file": self.file,
            "kinds": list(self.kinds),
            "evidence": dict(self.evidence),
            "confidence": self.confidence,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


# ---------------------------------------------------------------------------
# Known side-effecting prefixes (seed list — users can extend).
#
# Each entry maps a name *prefix* (matched against
# ``qualified_name`` or against substring of the source body) to a
# coarse kind.  Order doesn't matter; we collect ALL matches.
#
# Heuristic: false negatives expected (we don't cover every library);
# false positives should be rare because we use precise prefixes.
# ---------------------------------------------------------------------------

KNOWN_SIDE_EFFECTING_PREFIXES: tuple[tuple[str, str], ...] = (
    # ── io_read ────────────────────────────────────────────────────────
    ("requests.get", "io_read"),
    ("requests.head", "io_read"),
    ("httpx.get", "io_read"),
    ("aiohttp.ClientSession.get", "io_read"),
    ("urllib.request.urlopen", "io_read"),
    ("urlopen", "io_read"),
    ("socket.recv", "io_read"),
    ("socket.recvfrom", "io_read"),
    (".fetchone", "io_read"),
    (".fetchall", "io_read"),
    (".fetchmany", "io_read"),
    (".read_text", "io_read"),
    (".read_bytes", "io_read"),
    ("os.environ.get", "io_read"),
    ("os.getenv", "io_read"),
    ("os.listdir", "io_read"),
    ("os.walk", "io_read"),
    ("os.stat", "io_read"),
    ("os.path.exists", "io_read"),
    ("os.path.isfile", "io_read"),
    ("os.path.isdir", "io_read"),
    ("boto3.client", "io_read"),
    # ── io_write ───────────────────────────────────────────────────────
    ("requests.post", "io_write"),
    ("requests.put", "io_write"),
    ("requests.patch", "io_write"),
    ("requests.delete", "io_write"),
    ("httpx.post", "io_write"),
    ("httpx.put", "io_write"),
    ("httpx.patch", "io_write"),
    ("httpx.delete", "io_write"),
    ("aiohttp.ClientSession.post", "io_write"),
    (".write_text", "io_write"),
    (".write_bytes", "io_write"),
    (".writelines", "io_write"),
    ("json.dump", "io_write"),
    ("pickle.dump", "io_write"),
    ("shutil.copy", "io_write"),
    ("shutil.move", "io_write"),
    ("shutil.rmtree", "io_write"),
    ("os.remove", "io_write"),
    ("os.unlink", "io_write"),
    ("os.rename", "io_write"),
    ("os.replace", "io_write"),
    ("os.mkdir", "io_write"),
    ("os.makedirs", "io_write"),
    ("os.rmdir", "io_write"),
    ("os.chmod", "io_write"),
    ("os.chown", "io_write"),
    ("os.fdopen", "io_write"),
    # W13.4: pathlib.Path equivalents — different qualified names, same
    # semantics. Classifier missed atomic-write helpers using Path.replace
    # for the ``tmp + Path.replace(target)`` idiom.
    ("Path.replace", "io_write"),
    ("Path.rename", "io_write"),
    ("Path.unlink", "io_write"),
    ("Path.mkdir", "io_write"),
    ("Path.rmdir", "io_write"),
    ("Path.chmod", "io_write"),
    ("Path.touch", "io_write"),
    ("tempfile.mktemp", "io_write"),
    ("tempfile.mkstemp", "io_write"),
    ("tempfile.NamedTemporaryFile", "io_write"),
    ("tempfile.TemporaryFile", "io_write"),
    ("psycopg2.connect", "io_write"),
    ("sqlite3.connect", "io_write"),
    (".commit", "io_write"),
    (".execute", "io_write"),
    (".executemany", "io_write"),
    (".insert", "io_write"),
    (".save", "io_write"),
    # ── process ────────────────────────────────────────────────────────
    ("subprocess.run", "process"),
    ("subprocess.Popen", "process"),
    ("subprocess.check_call", "process"),
    ("subprocess.check_output", "process"),
    ("subprocess.call", "process"),
    ("os.system", "process"),
    ("os.popen", "process"),
    ("os.spawnl", "process"),
    ("os.execv", "process"),
    ("os.fork", "process"),
    ("threading.Thread", "process"),
    ("multiprocessing.Process", "process"),
    ("asyncio.create_task", "process"),
    ("asyncio.ensure_future", "process"),
    ("asyncio.gather", "process"),
    ("concurrent.futures.ThreadPoolExecutor", "process"),
    ("concurrent.futures.ProcessPoolExecutor", "process"),
    # ── mutation ───────────────────────────────────────────────────────
    ("os.environ.__setitem__", "mutation"),
    ("os.environ.pop", "mutation"),
)


# ---------------------------------------------------------------------------
# Source-text patterns — substring / regex anchors used when call edges
# didn't resolve.  These are intentionally cheap (no AST walk).
# ---------------------------------------------------------------------------

_OPEN_RE = re.compile(r"\bopen\s*\(\s*[^,\)]+,\s*['\"]([rwabx+]+)['\"]")
_OPEN_DEFAULT_RE = re.compile(r"\bopen\s*\(\s*[^,\)]+\)")  # 1-arg → read mode

# (regex/substring, kind, ev_label)
_SOURCE_PATTERNS: tuple[tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"\brequests\.(get|head)\b"), "io_read", "requests.get/head"),
    (re.compile(r"\brequests\.(post|put|patch|delete)\b"), "io_write", "requests.write"),
    (re.compile(r"\bhttpx\.(get|head)\b"), "io_read", "httpx.get/head"),
    (re.compile(r"\bhttpx\.(post|put|patch|delete)\b"), "io_write", "httpx.write"),
    (re.compile(r"\baiohttp\."), "io_read", "aiohttp"),
    (re.compile(r"\burllib\b.*urlopen|\burlopen\("), "io_read", "urlopen"),
    (re.compile(r"\bsubprocess\.(run|Popen|call|check_call|check_output)\("), "process", "subprocess"),
    (re.compile(r"\bos\.system\("), "process", "os.system"),
    (re.compile(r"\bos\.popen\("), "process", "os.popen"),
    (re.compile(r"\bthreading\.Thread\("), "process", "threading"),
    (re.compile(r"\bmultiprocessing\.Process\("), "process", "multiprocessing"),
    (re.compile(r"\basyncio\.(create_task|ensure_future|gather)\("), "process", "asyncio.task"),
    (re.compile(r"\.fetchone\(|\.fetchall\(|\.fetchmany\("), "io_read", "db.fetch"),
    (re.compile(r"\.commit\("), "io_write", "db.commit"),
    (re.compile(r"\.execute(many)?\(\s*['\"](?:INSERT|UPDATE|DELETE|REPLACE)", re.IGNORECASE), "io_write", "db.execute(write)"),
    (re.compile(r"\.execute(many)?\(\s*['\"](?:SELECT|PRAGMA)", re.IGNORECASE), "io_read", "db.execute(read)"),
    (re.compile(r"\.write_text\(|\.write_bytes\(|\.writelines\("), "io_write", "path.write_*"),
    (re.compile(r"\.read_text\(|\.read_bytes\("), "io_read", "path.read_*"),
    (re.compile(r"\bjson\.dump\("), "io_write", "json.dump"),
    (re.compile(r"\bpickle\.dump\("), "io_write", "pickle.dump"),
    (re.compile(r"\bjson\.load\("), "io_read", "json.load"),
    (re.compile(r"\bpickle\.load\("), "io_read", "pickle.load"),
    (re.compile(r"\bshutil\.(copy|move|rmtree)"), "io_write", "shutil.write"),
    (re.compile(r"(?:^|[^A-Za-z0-9_])_?os\.(remove|unlink|rename|replace|mkdir|makedirs|rmdir|chmod|chown|fdopen)\("), "io_write", "os.fs.write"),
    (re.compile(r"(?:^|[^A-Za-z0-9_])_?os\.(listdir|walk|stat)\("), "io_read", "os.fs.read"),
    (re.compile(r"\btempfile\.(mktemp|mkstemp|NamedTemporaryFile|TemporaryFile)\("), "io_write", "tempfile.mkstemp"),
    # W13.4: Path-like atomic-write idioms — ``tmp_path.replace(target)``
    # is the canonical safe-write pattern. We match the method-call form
    # (``\.replace(``) only when paired with an import of pathlib in the
    # file (handled by the file-imports gate above); to keep the rule
    # tight, match the explicit ``Path.replace``/``Path.rename``/...
    # class-method form too.
    (re.compile(r"\bPath\.(replace|rename|unlink|mkdir|rmdir|chmod|touch)\b"), "io_write", "Path.fs.write"),
    (re.compile(r"\bos\.environ\b\s*\["), "io_read", "os.environ.read"),  # may be write too — handled below
    (re.compile(r"\bos\.environ\s*\[[^\]]+\]\s*="), "mutation", "os.environ.write"),
    (re.compile(r"\bos\.getenv\("), "io_read", "os.getenv"),
    (re.compile(r"^\s*global\s+\w+", re.MULTILINE), "mutation", "global"),
    (re.compile(r"^\s*nonlocal\s+\w+", re.MULTILINE), "mutation", "nonlocal"),
    (re.compile(r"\bboto3\.(client|resource)\("), "io_write", "boto3"),
    (re.compile(r"\bpsycopg2\.connect\(|\bsqlite3\.connect\("), "io_write", "db.connect"),
    (re.compile(r"\.send\(|\.recv\("), "io_write", "socket.send/recv"),
)

# `open(path, 'w'|'a'|'x'|'r+')` ⇒ io_write; `open(path, 'r')` or 1-arg
# defaults to io_read.  Handled out-of-band below because we need the
# capture group.

# Cheap pre-filter: if the body contains none of these anchor substrings
# we can skip the whole _SOURCE_PATTERNS loop.  Cuts classifier runtime
# from ~6s → ~1s on roam-code (most function bodies are short and don't
# mention any side-effecting API).
_PRE_FILTER_RE = re.compile(
    r"\b("
    r"open|requests|httpx|aiohttp|urllib|urlopen|subprocess|threading|"
    r"multiprocessing|asyncio|os\.|_os\.|tempfile|json\.dump|json\.load|pickle|shutil|"
    r"psycopg2|sqlite3|boto3|fetchone|fetchall|fetchmany|"
    r"write_text|write_bytes|read_text|read_bytes|writelines|"
    r"\.commit|\.execute|\.send|\.recv|global\s+\w+|nonlocal\s+\w+|"
    r"Path\."
    r")"
)

# ---------------------------------------------------------------------------
# Import-level signals (file-wide) — surface when per-symbol evidence
# is silent but the file imports a known side-effecting library.  Used
# only to flip "none" → "unknown" (low confidence), never to add a
# concrete kind.
# ---------------------------------------------------------------------------

_SIDE_EFFECTING_IMPORTS = frozenset(
    {
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "subprocess",
        "psycopg2",
        "sqlite3",
        "boto3",
        "redis",
        "kafka",
        "shutil",
    }
)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def _classify_one_symbol(
    body_text: str,
    callee_qnames: list[str],
    file_imports: set[str],
) -> tuple[list[str], dict, str]:
    """Run the three-layer classifier on a single symbol body.

    Returns ``(kinds, evidence, confidence)``.
    """
    kinds: set[str] = set()
    matched_calls: list[str] = []
    matched_patterns: list[str] = []

    # Layer 1: resolved call edges to known side-effecting prefixes
    if callee_qnames:
        for qname in callee_qnames:
            if not qname:
                continue
            for prefix, kind in KNOWN_SIDE_EFFECTING_PREFIXES:
                if qname == prefix or qname.endswith("." + prefix) or qname.startswith(prefix):
                    kinds.add(kind)
                    matched_calls.append(f"{qname}→{kind}")
                    break

    # Layer 2: source-text patterns (catches calls the indexer didn't resolve)
    if body_text and _PRE_FILTER_RE.search(body_text):
        for pat, kind, label in _SOURCE_PATTERNS:
            if pat.search(body_text):
                kinds.add(kind)
                matched_patterns.append(label)

        # `open(..., 'w'|'a'|...)` is special — mode arg drives kind.
        for m in _OPEN_RE.finditer(body_text):
            mode = m.group(1)
            if any(c in mode for c in ("w", "a", "x", "+")):
                kinds.add("io_write")
                matched_patterns.append("open(mode=w/a/x/+)")
            else:
                kinds.add("io_read")
                matched_patterns.append("open(mode=r)")
        if not _OPEN_RE.search(body_text):
            for _ in _OPEN_DEFAULT_RE.finditer(body_text):
                kinds.add("io_read")
                matched_patterns.append("open(default=r)")
                break

    # Confidence calibration:
    if matched_calls:
        confidence = "high"
    elif len(matched_patterns) >= 2:
        confidence = "high"
    elif matched_patterns:
        confidence = "medium"
    elif file_imports & _SIDE_EFFECTING_IMPORTS:
        # File imports a side-effecting lib but per-symbol evidence
        # is silent → safe to claim ``none`` (the symbol body has no
        # I/O of its own) but mark confidence ``low`` so callers can
        # decide whether to trust the verdict.  Earlier passes flipped
        # the result to ``unknown`` on a bare import signal, but that
        # caused trivially-pure helpers in I/O-heavy modules to be
        # mis-classified, dominating the global "unknown" bucket.
        confidence = "low"
        kinds = set()
        kinds.add("none")
    else:
        confidence = "high"  # confident this is pure
        kinds = set()
        kinds.add("none")

    evidence: dict = {}
    if matched_calls:
        evidence["calls_seen"] = matched_calls[:8]
    if matched_patterns:
        # de-dup while preserving order
        seen = set()
        uniq = []
        for label in matched_patterns:
            if label not in seen:
                seen.add(label)
                uniq.append(label)
        evidence["matched_patterns"] = uniq[:8]
    if file_imports & _SIDE_EFFECTING_IMPORTS:
        evidence["imports_seen"] = sorted(file_imports & _SIDE_EFFECTING_IMPORTS)
    if not evidence:
        evidence["reason"] = "no outgoing calls, no patterns, no risky imports"

    return sorted(kinds), evidence, confidence


def _file_imports(source_text: str) -> set[str]:
    """Cheap import scan — first 200 lines, top-level only.

    We don't AST-parse; the indexer already does that for the symbol
    table.  This routine just spots module names in `import X` /
    `from X import ...` lines so we can flag a file-level side-effect
    library import.
    """
    imports: set[str] = set()
    lines = source_text.splitlines()[:200]
    for line in lines:
        s = line.lstrip()
        if s.startswith("import "):
            rest = s[7:].split("#", 1)[0]
            for part in rest.split(","):
                name = part.strip().split(" as ")[0].split(".")[0]
                if name:
                    imports.add(name)
        elif s.startswith("from "):
            rest = s[5:].split(" import ", 1)[0].strip()
            top = rest.split(".")[0]
            if top:
                imports.add(top)
    return imports


def _load_source_slice(repo_root: Path, rel_path: str, ls: int, le: int) -> Optional[str]:
    """Read lines [ls..le] (1-based inclusive) from a file.  Returns None on error."""
    try:
        p = repo_root / rel_path
        if not p.exists():
            return None
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    if ls <= 0:
        ls = 1
    if le <= 0 or le > len(lines):
        le = len(lines)
    return "".join(lines[ls - 1 : le])


def classify_side_effects(
    conn,
    symbol_name: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[SideEffectClassification]:
    """Scan symbols and classify their side effects.

    Args:
        conn: Read-only DB connection.
        symbol_name: If given, only classify symbols whose ``name`` or
            ``qualified_name`` matches exactly.  Else scan ALL function
            / method / constructor symbols.
        limit: Optional cap on scanned symbols (None = no cap).

    Returns:
        List of :class:`SideEffectClassification`.  Order: by file then
        symbol id (stable).
    """
    # 1) Pull candidate symbols.
    if symbol_name:
        rows = conn.execute(
            """
            SELECT s.id, s.name, s.qualified_name, s.line_start, s.line_end,
                   s.kind, f.path AS file_path, f.id AS file_id, f.language
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
                   s.kind, f.path AS file_path, f.id AS file_id, f.language
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

    sym_ids = [r["id"] for r in rows]

    # 2) Build callee map: symbol_id → list of qualified_name strings of callees.
    #    Hand-rolled chunking — `batched_in()` from connection.py returns
    #    rows, not chunk iterators, so we use a simple manual split.
    callee_map: dict[int, list[str]] = {sid: [] for sid in sym_ids}
    CHUNK = 400
    for i in range(0, len(sym_ids), CHUNK):
        chunk = sym_ids[i : i + CHUNK]
        ph = ",".join("?" for _ in chunk)
        q = (
            f"SELECT e.source_id AS sid, "
            f"       COALESCE(ts.qualified_name, ts.name) AS cname "
            f"FROM edges e "
            f"JOIN symbols ts ON e.target_id = ts.id "
            f"WHERE e.source_id IN ({ph}) "
            f"  AND e.kind IN ('calls', 'invokes', 'reference', 'uses') "
        )
        for row in conn.execute(q, chunk).fetchall():
            callee_map.setdefault(row["sid"], []).append(row["cname"] or "")

    # 3) Group rows by file so we read each file once.
    try:
        repo_root = find_project_root()
    except Exception:
        repo_root = Path(".")

    rows_by_file: dict[str, list] = {}
    for r in rows:
        rows_by_file.setdefault(r["file_path"], []).append(r)

    out: list[SideEffectClassification] = []

    for file_path, file_rows in rows_by_file.items():
        # Read file once, derive imports + per-symbol source slices.
        try:
            p = repo_root / file_path
            if p.exists():
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    all_text = f.read()
                all_lines = all_text.splitlines(keepends=True)
            else:
                all_text = ""
                all_lines = []
        except OSError:
            all_text = ""
            all_lines = []

        imports = _file_imports(all_text) if all_text else set()

        for r in file_rows:
            sid = r["id"]
            ls = r["line_start"] or 1
            le = r["line_end"] or ls
            if all_lines:
                body = "".join(all_lines[max(0, ls - 1) : le])
            else:
                body = ""
            callees = callee_map.get(sid, [])
            kinds, evidence, confidence = _classify_one_symbol(body, callees, imports)
            out.append(
                SideEffectClassification(
                    symbol=r["qualified_name"] or r["name"],
                    file=file_path,
                    kinds=kinds,
                    evidence=evidence,
                    confidence=confidence,
                    symbol_id=sid,
                    line_start=ls,
                    line_end=le,
                )
            )

    return out


__all__ = [
    "SIDE_EFFECT_KINDS",
    "SideEffectClassification",
    "KNOWN_SIDE_EFFECTING_PREFIXES",
    "classify_side_effects",
]
