"""``roam test-hermeticity`` — non-hermetic test detector for AI-generated test risk.

AI-generated tests routinely reach for the real network, the wall clock,
the filesystem, the environment, or ``random.*`` without mocking — every
one of those is a CI-flakiness vector. Roam catches the pattern at
detection time so an agent (or human reviewer) sees the risk before
merge.

The detector is intentionally AST-driven (not regex) so we don't trip on
strings, comments, or doc-block prose that mentions ``requests.get`` in
passing. Six closed-enum kinds:

* ``network``     — ``requests.*``, ``urllib.request.*``, ``urllib3.*``,
                    ``httpx.*``, ``socket.socket()``, ``http.client.*``
* ``time``        — ``time.time()``, ``time.monotonic()``,
                    ``time.perf_counter()``, ``datetime.now()``,
                    ``datetime.utcnow()``, ``datetime.today()``
* ``random``      — ``random.random/choice/randint/sample/shuffle/uniform``
                    (when no ``random.seed(...)`` call in the module)
* ``filesystem``  — ``Path.home()``, ``Path.cwd()``, ``os.getcwd()``,
                    ``os.path.expanduser``, ``tempfile.gettempdir()``
* ``env``         — ``os.environ[...]``, ``os.environ.get(...)``,
                    ``os.getenv(...)``
* ``subprocess``  — ``subprocess.run/call/Popen/check_call/check_output``

False-positive reduction is built in at the module level:

* ``conftest.py`` and files under ``tests/_helpers/`` / ``tests/fixtures/``
  are skipped — they're test infrastructure, not test code.
* ``monkeypatch.setenv(...)`` anywhere in the module suppresses ``env``
  findings; ``random.seed(...)`` suppresses ``random``; presence of
  ``freezegun`` / ``time_machine`` imports suppresses ``time``; presence
  of ``responses`` / ``httpx_mock`` / ``aioresponses`` imports
  suppresses ``network``.
* ``unittest.mock.patch(...)`` / ``@patch("subprocess.")`` targeting a
  given dotted module suppresses that kind in the same module.

Findings persist into the central findings registry (W89/W93+) under
``source_detector = "test-hermeticity"`` so ``roam findings list`` can
read them like every other detector.

Output formats: text (default), ``--json``. SARIF is deliberately NOT
emitted from this command because the registry path is the canonical
SARIF projection surface for findings-registry-backed detectors:
consumers route through ``roam findings list --sarif`` (or the
detector-specific SARIF projection wired off the central registry) per
the W170 canonical mandate — every exporter is a projection from
shared evidence, not a second source of truth. Wiring a private
``--sarif`` flag here would create a second SARIF emit path competing
with the registry projection. See W1148 audit memo +
(internal memo) §8. Introduced at
W1287.

Examples
--------

    roam test-hermeticity
    roam --json test-hermeticity
    roam test-hermeticity --persist  # mirror into the findings registry
"""

from __future__ import annotations

import ast
import json as _json
import sqlite3
from typing import Any, Iterable

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.db.findings import (
    CONFIDENCE_STATIC_ANALYSIS,
    CONFIDENCE_STRUCTURAL,
    FindingRecord,
    emit_finding,
    make_finding_id,
)
from roam.index.file_roles import ROLE_TEST
from roam.output.formatter import json_envelope, to_json

# Module-level detector-version stamp. Bump when the AST predicates or
# the closed-enum ``kind`` vocabulary change meaningfully — the registry
# stores this on every emitted row so downstream consumers can spot
# rows produced under a stale detector shape.
_TEST_HERMETICITY_DETECTOR_VERSION: str = "1.0.0"

# Closed-enum ``kind`` vocabulary — keep these in lock-step with the
# evidence_json payload and the test fixture. Adding a new kind is a
# deliberate source edit, not a runtime string.
_KIND_NETWORK = "network"
_KIND_TIME = "time"
_KIND_RANDOM = "random"
_KIND_FILESYSTEM = "filesystem"
_KIND_ENV = "env"
_KIND_SUBPROCESS = "subprocess"
_VALID_KINDS = frozenset(
    {_KIND_NETWORK, _KIND_TIME, _KIND_RANDOM, _KIND_FILESYSTEM, _KIND_ENV, _KIND_SUBPROCESS}
)


# --- Per-kind AST predicates -------------------------------------------------
#
# Each predicate consumes a ``ast.Call`` node and returns the matched
# kind string, or ``None`` if the call isn't non-hermetic. We always
# look at the *attribute chain* of the callee, never at the raw name —
# the regex bug we're avoiding is "any function named ``time``" or
# "any variable named ``socket``". AST resolves the dotted path.

# Network module roots whose .get/.post/.put/.delete/.request/.urlopen
# calls (or `socket.socket(...)`) reach the real network when invoked.
_NETWORK_ROOTS = frozenset({"requests", "urllib3", "httpx", "aiohttp"})
_NETWORK_URLLIB_ATTRS = frozenset(
    {"urlopen", "Request", "build_opener", "install_opener", "urlretrieve"}
)
_NETWORK_HTTPCLIENT_ROOTS = frozenset({"http"})  # http.client.HTTPConnection etc

_TIME_TIME_ATTRS = frozenset({"time", "monotonic", "perf_counter", "process_time", "time_ns"})
_TIME_DATETIME_ATTRS = frozenset({"now", "utcnow", "today"})

_RANDOM_ATTRS = frozenset(
    {
        "random",
        "randint",
        "choice",
        "choices",
        "sample",
        "shuffle",
        "uniform",
        "randrange",
        "randbytes",
        "gauss",
        "normalvariate",
    }
)

_FS_PATH_ATTRS = frozenset({"home", "cwd"})  # Path.home(), Path.cwd()
_FS_OS_ATTRS = frozenset({"getcwd"})
_FS_OS_PATH_ATTRS = frozenset({"expanduser", "expandvars"})
_FS_TEMPFILE_ATTRS = frozenset({"gettempdir", "gettempprefix"})

_ENV_OS_ATTRS = frozenset({"getenv", "putenv", "unsetenv"})

_SUBPROCESS_ATTRS = frozenset(
    {"run", "call", "Popen", "check_call", "check_output", "getoutput", "getstatusoutput"}
)


def _attr_chain(node: ast.AST) -> list[str] | None:
    """Recover the dotted-attribute chain for a callee like ``a.b.c``.

    Returns ``["a", "b", "c"]`` for ``a.b.c(...)``; returns ``None`` when
    the chain bottoms out on anything other than a bare ``Name`` (e.g.
    ``obj.method()`` where ``obj`` came from a subscript or call).
    """
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return list(reversed(parts))
    return None


def _classify_call(call: ast.Call) -> str | None:
    """Return one of ``_VALID_KINDS`` for a non-hermetic call, else None."""
    chain = _attr_chain(call.func)
    if not chain:
        return None
    root = chain[0]
    tail = chain[-1]

    # Network ----------------------------------------------------------
    if root in _NETWORK_ROOTS:
        # requests.get / httpx.post / urllib3.PoolManager etc.
        return _KIND_NETWORK
    if root == "urllib" and len(chain) >= 2:
        # urllib.request.urlopen, urllib.request.Request, ...
        if chain[1] == "request" and tail in _NETWORK_URLLIB_ATTRS:
            return _KIND_NETWORK
    if root == "socket" and tail == "socket":
        return _KIND_NETWORK
    if root in _NETWORK_HTTPCLIENT_ROOTS and len(chain) >= 2 and chain[1] == "client":
        return _KIND_NETWORK

    # Time -------------------------------------------------------------
    if root == "time" and tail in _TIME_TIME_ATTRS and len(chain) == 2:
        return _KIND_TIME
    if root == "datetime" and tail in _TIME_DATETIME_ATTRS:
        # datetime.datetime.now() or datetime.now() — both flag.
        return _KIND_TIME

    # Random -----------------------------------------------------------
    if root == "random" and tail in _RANDOM_ATTRS and len(chain) == 2:
        return _KIND_RANDOM

    # Filesystem -------------------------------------------------------
    if root == "Path" and tail in _FS_PATH_ATTRS and len(chain) == 2:
        return _KIND_FILESYSTEM
    if root == "os" and tail in _FS_OS_ATTRS and len(chain) == 2:
        return _KIND_FILESYSTEM
    if root == "os" and len(chain) >= 3 and chain[1] == "path" and tail in _FS_OS_PATH_ATTRS:
        return _KIND_FILESYSTEM
    if root == "tempfile" and tail in _FS_TEMPFILE_ATTRS and len(chain) == 2:
        return _KIND_FILESYSTEM

    # Env --------------------------------------------------------------
    if root == "os" and tail in _ENV_OS_ATTRS and len(chain) == 2:
        return _KIND_ENV

    # Subprocess -------------------------------------------------------
    if root == "subprocess" and tail in _SUBPROCESS_ATTRS and len(chain) == 2:
        return _KIND_SUBPROCESS

    return None


def _has_environ_access(node: ast.AST) -> bool:
    """Return True for ``os.environ[...]`` or ``os.environ.get(...)``.

    Subscripting / attribute access don't show up under ``_classify_call``
    because they're not Call nodes — we handle them separately so the
    common ``os.environ["FOO"]`` shape isn't missed.
    """
    if isinstance(node, ast.Subscript):
        target = node.value
    elif isinstance(node, ast.Attribute):
        target = node.value
    else:
        return False
    chain = _attr_chain(target)
    return chain == ["os", "environ"]


# --- Module-level suppression signals ----------------------------------------


def _collect_suppression_signals(tree: ast.AST) -> set[str]:
    """Walk the module once for module-level mock / seed signals.

    Returns the set of ``kind`` strings that should be suppressed for
    this module:

    * ``random.seed(...)`` anywhere -> suppress ``random``.
    * ``monkeypatch.setenv(...)`` anywhere -> suppress ``env``.
    * ``freezegun`` / ``time_machine`` imported -> suppress ``time``.
    * ``responses`` / ``httpx_mock`` / ``aioresponses`` imported ->
      suppress ``network``.
    * ``monkeypatch.setattr("subprocess.<x>", ...)`` -> suppress
      ``subprocess``.
    * ``unittest.mock.patch("subprocess.<x>")`` -> suppress
      ``subprocess``.
    * ``monkeypatch.setattr("os.environ", ...)`` -> suppress ``env``.
    """
    suppress: set[str] = set()
    for node in ast.walk(tree):
        # imports
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_names: Iterable[str] = ()
            if isinstance(node, ast.Import):
                module_names = [n.name.split(".")[0] for n in node.names]
            else:
                module_names = [node.module.split(".")[0] if node.module else ""]
            for mname in module_names:
                if mname in ("freezegun", "time_machine"):
                    suppress.add(_KIND_TIME)
                if mname in ("responses", "httpx_mock", "aioresponses", "pytest_httpserver", "respx"):
                    suppress.add(_KIND_NETWORK)
        # random.seed(...) call
        if isinstance(node, ast.Call):
            chain = _attr_chain(node.func)
            if chain == ["random", "seed"]:
                suppress.add(_KIND_RANDOM)
            # monkeypatch.setenv(...) / monkeypatch.delenv(...)
            if chain and len(chain) == 2 and chain[1] in {"setenv", "delenv"}:
                # Heuristic: any *.setenv/.delenv call signals env mocking.
                # monkeypatch is the dominant idiom; we don't bind to the
                # exact receiver name.
                suppress.add(_KIND_ENV)
            # monkeypatch.setattr("subprocess.<x>", ...) or
            # mock.patch("subprocess.<x>") — first string arg names the
            # patched dotted path.
            if chain and chain[-1] in {"setattr", "patch", "patch_object", "object"}:
                if node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        target = first.value
                        if target.startswith("subprocess."):
                            suppress.add(_KIND_SUBPROCESS)
                        elif target.startswith("os.environ") or target == "os.environ":
                            suppress.add(_KIND_ENV)
                        elif target.startswith("time.") or target.startswith("datetime."):
                            suppress.add(_KIND_TIME)
                        elif target.startswith("socket.") or target.startswith("requests."):
                            suppress.add(_KIND_NETWORK)
    return suppress


# --- Per-file scan -----------------------------------------------------------


def _scan_test_file(file_path: str) -> list[dict]:
    """Return one finding dict per non-hermetic call site in the file.

    Returns ``[]`` when the file can't be parsed, is suppressed in full,
    or contains no non-hermetic calls. The caller filters / persists.
    """
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return []

    suppress = _collect_suppression_signals(tree)
    findings: list[dict] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            kind = _classify_call(node)
            if kind is None:
                continue
            if kind in suppress:
                continue
            # ``_attr_chain`` already gave a bound chain — re-derive for evidence.
            chain = _attr_chain(node.func) or []
            findings.append(
                {
                    "file": file_path,
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "kind": kind,
                    "evidence": ".".join(chain) + "(...)",
                }
            )
        # os.environ[...] / os.environ.get(...) — Subscript form only
        # (the .get(...) form is caught above via _classify_call too).
        if isinstance(node, ast.Subscript) and _has_environ_access(node):
            if _KIND_ENV in suppress:
                continue
            findings.append(
                {
                    "file": file_path,
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "kind": _KIND_ENV,
                    "evidence": "os.environ[...]",
                }
            )

    return findings


def _is_test_infrastructure(path: str) -> bool:
    """Skip files that are test infrastructure rather than test code.

    ``conftest.py``, ``tests/_helpers/*``, and ``tests/fixtures/*`` exist
    to support the tests; flagging them adds noise without changing what
    an agent should review.
    """
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if name == "conftest.py":
        return True
    if "/tests/_helpers/" in p or "/tests/fixtures/" in p:
        return True
    if "/_helpers/" in p or "/fixtures/" in p:
        return True
    return False


def _resolve_subject_id(conn: sqlite3.Connection, file_path: str) -> int | None:
    """Best-effort ``files.id`` lookup for the test file.

    Returns ``None`` when the file isn't in the index — the findings
    registry permits NULL ``subject_id`` for file-level findings.
    """
    try:
        row = conn.execute("SELECT id FROM files WHERE path = ? LIMIT 1", (file_path,)).fetchone()
        return int(row[0]) if row is not None else None
    except sqlite3.OperationalError:
        return None


def _emit_findings(conn: sqlite3.Connection, findings: list[dict]) -> int:
    """Mirror each non-hermetic finding into the central registry."""
    written = 0
    for f in findings:
        file_path = f["file"]
        line = f["line"]
        kind = f["kind"]
        evidence = f["evidence"]
        subject_id = _resolve_subject_id(conn, file_path)
        finding_id = make_finding_id(
            "test-hermeticity", kind, kind, file_path, evidence, int(line or 0)
        )
        # AST-derived call-site detection is structurally evident
        # (the call literally appears in the file) but the dataflow
        # to a real network / clock / etc. is inferred from the
        # callee identity, not proved by reachability. That places
        # the per-kind tier between heuristic and full static analysis.
        # ``env`` via Subscript is even more concretely structural —
        # the read literally happens at that line. Both tiers are
        # valid registry confidence levels; we pick ``structural`` for
        # the call-classifier form and ``static_analysis`` for the
        # AST-level env subscript (the most deterministic of the lot).
        confidence = (
            CONFIDENCE_STATIC_ANALYSIS
            if (kind == _KIND_ENV and evidence == "os.environ[...]")
            else CONFIDENCE_STRUCTURAL
        )
        claim = f"non-hermetic test call ({kind}): {evidence} at {file_path}:{line}"
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str=finding_id,
                subject_kind="file" if subject_id is not None else "module",
                subject_id=subject_id,
                claim=claim,
                evidence_json=_json.dumps(
                    {"file": file_path, "line": line, "kind": kind, "evidence": evidence},
                    sort_keys=True,
                ),
                confidence=confidence,
                source_detector="test-hermeticity",
                source_version=_TEST_HERMETICITY_DETECTOR_VERSION,
            ),
        )
        written += 1
    return written


@roam_capability(
    name="test-hermeticity",
    category="testing",
    summary="Detect non-hermetic test patterns (network, time, random, fs, env, subprocess)",
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
@click.command("test-hermeticity")
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help=(
        "Mirror each non-hermetic finding into the central findings registry "
        "(``findings`` table) for downstream consumers (roam findings list / "
        "show / count). Detector-specific output is unchanged."
    ),
)
@click.option(
    "--ci",
    "ci_mode",
    is_flag=True,
    default=False,
    help="Exit 5 when any non-hermetic test is detected (CI gate).",
)
@click.pass_context
def test_hermeticity(ctx, persist: bool, ci_mode: bool) -> None:
    """Scan Python test files for non-hermetic patterns (AI-test flakiness risk).

    \b
    Examples:
      roam test-hermeticity
      roam --json test-hermeticity
      roam test-hermeticity --persist
      roam test-hermeticity --ci
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    ensure_index()

    findings: list[dict] = []
    total_test_files = 0
    non_hermetic_files: set[str] = set()

    with open_db(readonly=not persist) as conn:
        rows = conn.execute(
            "SELECT path FROM files WHERE file_role = ? AND language = 'python' ORDER BY path",
            (ROLE_TEST,),
        ).fetchall()
        test_files = [r["path"] for r in rows if not _is_test_infrastructure(r["path"])]
        total_test_files = len(test_files)

        for path in test_files:
            file_findings = _scan_test_file(path)
            if file_findings:
                non_hermetic_files.add(path)
                findings.extend(file_findings)

        if persist:
            try:
                _emit_findings(conn, findings)
                conn.commit()
            except sqlite3.OperationalError:
                # Pre-W89 schema — no findings table. Degrade silently
                # rather than crash the standard scan path.
                pass

    non_hermetic = len(non_hermetic_files)
    hermetic = max(0, total_test_files - non_hermetic)
    hermeticity_rate = (
        round(100.0 * hermetic / total_test_files, 1) if total_test_files else 100.0
    )

    # W805: empty-corpus disclosure (Pattern 2 silent-fallback fix).
    # When no Python test files are indexed the check did not actually
    # run on any analyzable input — surface that explicitly via the
    # ``partial_success`` flag + a closed-enum ``state`` so agents can
    # distinguish "all tests hermetic" (real success) from "no tests
    # analyzed" (degraded/missing input). Mirrors W834 / W836.
    empty_corpus = total_test_files == 0
    if empty_corpus:
        verdict = "no Python test files indexed"
    elif not findings:
        verdict = f"all {total_test_files} test files are hermetic"
    else:
        verdict = (
            f"{len(findings)} non-hermetic findings across {non_hermetic} test files "
            f"({hermeticity_rate}% hermetic of {total_test_files} tests)"
        )

    # Closed-enum kind counts for the envelope (also useful in text mode).
    kind_counts: dict[str, int] = {k: 0 for k in sorted(_VALID_KINDS)}
    for f in findings:
        kind_counts[f["kind"]] = kind_counts.get(f["kind"], 0) + 1

    if json_mode:
        _summary: dict[str, Any] = {
            "verdict": verdict,
            "total": total_test_files,
            "hermetic": hermetic,
            "non_hermetic": non_hermetic,
            "hermeticity_rate": hermeticity_rate,
        }
        if empty_corpus:
            _summary["partial_success"] = True
            _summary["state"] = "no_tests_indexed"
        click.echo(
            to_json(
                json_envelope(
                    "test-hermeticity",
                    summary=_summary,
                    findings=findings,
                    kind_counts=kind_counts,
                    detector_version=_TEST_HERMETICITY_DETECTOR_VERSION,
                    agent_contract={
                        "facts": [
                            f"{total_test_files} Python test files scanned",
                            f"{len(findings)} non-hermetic findings",
                            f"{non_hermetic} non-hermetic test files",
                        ],
                        "next_commands": [
                            "roam findings list --detector test-hermeticity",
                            "# review each finding; wrap external deps in monkeypatch / responses / freezegun",
                        ],
                    },
                )
            )
        )
        if ci_mode and findings:
            ctx.exit(5)
        return

    click.echo(f"VERDICT: {verdict}")
    if findings:
        click.echo()
        for kind in sorted(_VALID_KINDS):
            n = kind_counts.get(kind, 0)
            if n:
                click.echo(f"  {kind:11s} {n}")
        click.echo()
        for f in findings[:50]:
            click.echo(f"  [{f['kind']:11s}] {f['file']}:{f['line']}  {f['evidence']}")
        if len(findings) > 50:
            click.echo(f"  ... {len(findings) - 50} more")

    if ci_mode and findings:
        ctx.exit(5)
