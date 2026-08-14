"""W1317 -- envelope-floor ``schema_version`` drift-guard.

THE DEFECT
----------

``roam.output.formatter.ENVELOPE_SCHEMA_VERSION`` (``"1.1.0"``) is the
canonical version stamped on every envelope built via
``json_envelope()`` / ``_base_json_envelope()``. 36 W607 "envelope
floor" sites -- hand-built emergency envelopes passed as
``default=<floor>`` to a ``json_envelope(...)`` call, used ONLY when the
normal builder raises -- hardcoded a stale literal ``"schema_version":
"1.0.0"`` instead of importing the constant (42 occurrences across the
36 files; ``grep -c '"schema_version": ENVELOPE_SCHEMA_VERSION'
src/roam/commands/*.py`` returned 0 before this wave).

This is worse than a cosmetic version slip: the disagreement fires
PRECISELY on the degraded path, where the envelope also carries
``partial_success: True`` and a consumer most needs the shape signal to
be trustworthy. A consumer branching on ``schema_version`` got an
accurate ``1.1.0`` when things were fine and a stale ``1.0.0`` exactly
when they were not -- the signal was correct only when nobody needed
it. ``tests/test_schema_versioning.py`` only drives the happy path, so
the 42 stale stamps were invisible to a fully green suite.

THE FIX
-------

All 36 genuine floor sites now import and reference
``ENVELOPE_SCHEMA_VERSION`` instead of the literal. One file was
DELIBERATELY EXCLUDED: ``commands/cmd_metrics_push.py``'s 3 occurrences
version the ``roam-metrics-v1`` contract (the Cloud Lite metrics
receiver's nested wire format under the envelope's ``payload`` key) --
an independently-owned external contract, not ``roam-envelope-v1``.
Its happy path and its W607-DI floor already agree (both hardcode
``"1.0.0"``) -- there is no drift there, and coupling that wire format
to ``ENVELOPE_SCHEMA_VERSION`` would introduce a NEW correctness bug
(an envelope schema bump would silently re-version an external API
contract nobody asked to change).

``mcp_server.py``'s ``_compound_envelope`` was a separate, adjacent gap
-- it bypasses ``json_envelope()`` entirely and emitted NO
``schema_version`` at all (not stale -- absent). Also fixed in this
wave: it and its sibling raise-path floor in
``_finalize_compound_recipe`` now stamp the same canonical constants.

THE STRUCTURAL GUARD (this file)
---------------------------------

A 36-site manual sweep with no guard drifts again the next time
``ENVELOPE_SCHEMA_VERSION`` bumps. This module AST-walks every dict
literal under ``src/roam/**/*.py`` and fails on any ``"schema_version"``
key bound to a hardcoded, version-shaped string constant -- the same
shape the W607 floors used. The canonical envelope contract
(``roam-envelope-v1``) may ONLY reference the constant; a handful of
independently-versioned contracts (the MCP handle/validate-plan specs,
``roam-metrics-v1``, the SLSA VSA predicate) are each allowlisted with a
one-line rationale, mirroring the existing ``test_w547_severity_drift.py``
/ ``test_canonical_constant_citations.py`` convention: no bare
exemptions, every entry cites why. This test file lives under
``tests/`` alongside those, so it runs wherever the rest of the suite
does -- ``roam-ci.yml``'s ``test`` job runs ``pytest tests/`` with no
additional wiring required.
"""

from __future__ import annotations

import ast
import json as _json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests._helpers.repo_root import repo_root

SRC_ROOT = repo_root() / "src" / "roam"

# A version-shaped literal: "1", "1.0", "1.0.0", "1.0.0.0", ... Deliberately
# narrow -- catches real version stamps while leaving field-DESCRIPTION
# dicts alone (e.g. schema_registry.py's ``"schema_version": "Semantic
# version of the envelope format"`` or cmd_compile_stats.py's
# ``"schema_version": "privacy-safe compile telemetry schema version"``,
# neither of which is a version stamp -- they document what the key MEANS).
_VERSION_LITERAL_RE = re.compile(r"^\d+(\.\d+){0,3}$")

# Sites that legitimately hardcode their OWN "schema_version" because they
# version a contract OTHER than roam-envelope-v1. Matched by the sibling
# "schema" key literal in the same dict when present; falls back to the
# enclosing function name when the dict has no "schema" sibling (the VSA
# predicate). Each entry MUST cite the divergent contract it owns.
_ALLOWLIST: list[dict[str, str]] = [
    {
        "file": "mcp_server.py",
        "schema": "roam-code.com/spec/handle/v1",
        "rationale": (
            "roam_fetch_handle large-response envelope -- public "
            "spec/handle/v1 contract, versioned independently of the CLI "
            "envelope (roam-envelope-v1)."
        ),
    },
    {
        "file": "mcp_server.py",
        "schema": "roam-code.com/spec/validate-plan/v1",
        "rationale": (
            "roam_validate_plan MCP tool result -- public "
            "spec/validate-plan/v1 contract, versioned independently of "
            "the CLI envelope."
        ),
    },
    {
        "file": "commands/cmd_metrics_push.py",
        "schema": "roam-metrics-v1",
        "rationale": (
            "Cloud Lite metrics-receiver wire format nested under the "
            "envelope's payload key -- an external API contract, not "
            "roam-envelope-v1. Happy path and the W607-DI floor already "
            "agree (both hardcode the same literal); there is no drift "
            "to fix here, and coupling it to ENVELOPE_SCHEMA_VERSION "
            "would re-version an external contract nobody asked to change."
        ),
    },
    {
        "file": "attest/vsa.py",
        "function": "build_run_ledger_root_predicate",
        "rationale": (
            "SLSA in-toto VSA predicate schema_version -- an independent "
            "attestation-predicate contract, not the roam CLI envelope."
        ),
    },
]


def _iter_source_files() -> list[Path]:
    return [p for p in SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def _is_allowlisted(rel: str, schema_sibling: object, enclosing_func: str | None) -> bool:
    for entry in _ALLOWLIST:
        if entry["file"] != rel:
            continue
        if "schema" in entry:
            if schema_sibling == entry["schema"]:
                return True
            continue
        if "function" in entry:
            if enclosing_func == entry["function"]:
                return True
            continue
    return False


def _find_violations(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(SRC_ROOT).as_posix()
    violations: list[str] = []

    def walk(node: ast.AST, func_stack: list[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_stack = func_stack + [node.name]
        if isinstance(node, ast.Dict):
            version_literal = None
            version_lineno = node.lineno
            schema_sibling = None
            for k, v in zip(node.keys, node.values):
                if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                    continue
                if k.value == "schema_version" and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    if _VERSION_LITERAL_RE.match(v.value):
                        version_literal = v.value
                        version_lineno = getattr(v, "lineno", node.lineno)
                elif k.value == "schema" and isinstance(v, ast.Constant):
                    schema_sibling = v.value
            if version_literal is not None:
                enclosing_func = func_stack[-1] if func_stack else None
                if not _is_allowlisted(rel, schema_sibling, enclosing_func):
                    violations.append(
                        f"{rel}:{version_lineno} hardcoded schema_version={version_literal!r} "
                        f"(schema sibling={schema_sibling!r}, func={enclosing_func!r})"
                    )
        for child in ast.iter_child_nodes(node):
            walk(child, func_stack)

    walk(tree, [])
    return violations


def test_no_hardcoded_schema_version_outside_allowlist() -> None:
    """W1317: every ``"schema_version"`` dict literal must reference
    ``ENVELOPE_SCHEMA_VERSION`` (or belong to an explicitly-cited,
    independently-versioned contract in ``_ALLOWLIST``).

    This is the guard that would have caught the original defect: 36
    files hardcoded ``"schema_version": "1.0.0"`` in a W607 floor dict
    with zero references to the canonical constant. A future bump of
    ``ENVELOPE_SCHEMA_VERSION`` cannot silently leave a floor behind --
    any new hardcoded version literal fails this test immediately.
    """
    violations: list[str] = []
    for path in _iter_source_files():
        violations.extend(_find_violations(path))
    assert not violations, (
        "W1317: hardcoded schema_version literal(s) found outside the "
        "canonical ENVELOPE_SCHEMA_VERSION constant (roam.output.formatter). "
        "Import and reference ENVELOPE_SCHEMA_VERSION instead of a literal. "
        "If this genuinely versions a DIFFERENT, independently-owned "
        "contract, add a cited entry to _ALLOWLIST in "
        "tests/test_w1317_envelope_floor_schema_version_drift.py:\n  " + "\n  ".join(violations)
    )


def test_allowlist_entries_still_match_real_code() -> None:
    """Every _ALLOWLIST entry must still resolve to a real hardcoded
    schema_version site -- catches drift when a file is renamed, a
    schema string changes, or the site is removed/migrated, so the
    allowlist can't silently rot into an unused exemption.
    """
    remaining = list(_ALLOWLIST)
    for path in _iter_source_files():
        rel = path.relative_to(SRC_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue

        def walk(node: ast.AST, func_stack: list[str]) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_stack = func_stack + [node.name]
            if isinstance(node, ast.Dict):
                has_version_literal = False
                schema_sibling = None
                for k, v in zip(node.keys, node.values):
                    if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                        continue
                    if k.value == "schema_version" and isinstance(v, ast.Constant) and isinstance(v.value, str):
                        if _VERSION_LITERAL_RE.match(v.value):
                            has_version_literal = True
                    elif k.value == "schema" and isinstance(v, ast.Constant):
                        schema_sibling = v.value
                if has_version_literal:
                    enclosing_func = func_stack[-1] if func_stack else None
                    for entry in list(remaining):
                        if entry["file"] != rel:
                            continue
                        if "schema" in entry and schema_sibling == entry["schema"]:
                            remaining.remove(entry)
                        elif "function" in entry and enclosing_func == entry["function"]:
                            remaining.remove(entry)
            for child in ast.iter_child_nodes(node):
                walk(child, func_stack)

        walk(tree, [])

    assert not remaining, (
        "W1317 allowlist entries no longer match any real hardcoded "
        "schema_version site -- remove or update them in "
        f"tests/test_w1317_envelope_floor_schema_version_drift.py: {remaining!r}"
    )


def test_canonical_constant_still_the_single_definition() -> None:
    """Sanity anchor: ENVELOPE_SCHEMA_VERSION is still a plain literal
    assignment in output/formatter.py (the one place a version literal
    SHOULD live), and every genuine floor references it by name rather
    than by value.
    """
    from roam.output.formatter import ENVELOPE_SCHEMA_VERSION

    assert _VERSION_LITERAL_RE.match(ENVELOPE_SCHEMA_VERSION), (
        f"ENVELOPE_SCHEMA_VERSION must be a semver literal; got {ENVELOPE_SCHEMA_VERSION!r}"
    )

    formatter_path = SRC_ROOT / "output" / "formatter.py"
    src = formatter_path.read_text(encoding="utf-8")
    assert f'ENVELOPE_SCHEMA_VERSION = "{ENVELOPE_SCHEMA_VERSION}"' in src, (
        "ENVELOPE_SCHEMA_VERSION's definition site has moved or changed shape -- update this anchor alongside it."
    )


# ---------------------------------------------------------------------------
# Floor-driving regression test -- force a REAL floor and inspect its output
# ---------------------------------------------------------------------------
#
# The AST guard above proves no NEW hardcoded literal can slip in, but the
# original 36-file defect survived a fully green suite precisely because
# no test forced ``json_envelope()`` to raise and inspected what the floor
# actually emitted. This test drives cmd_auth_gaps's W607-ED
# ``serialize_envelope`` floor end-to-end -- the exact site quoted in the
# task brief (cmd_auth_gaps.py's ``_envelope_floor`` -> ``click.echo(to_json
# (envelope))`` with no further wrapping) -- and asserts the emitted JSON
# carries ENVELOPE_SCHEMA_VERSION, not a stale literal.


@pytest.fixture
def cli_runner():
    return CliRunner()


def _build_minimal_auth_gaps_project(tmp_path: Path) -> Path:
    """Minimal indexed project root for cmd_auth_gaps (mirrors the
    fixture in test_w607_ed_cmd_auth_gaps_warnings_out_envelope.py).
    """
    import sqlite3
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine.py").write_text("def helper():\n    return 0\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    db_path = tmp_path / ".roam" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            language TEXT, file_role TEXT DEFAULT 'source',
            hash TEXT, mtime REAL, line_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
            signature TEXT, line_start INTEGER, line_end INTEGER,
            docstring TEXT, visibility TEXT DEFAULT 'public',
            is_exported INTEGER DEFAULT 1, parent_id INTEGER,
            default_value TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id)
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'call',
            line INTEGER, bridge TEXT, confidence REAL,
            source_file_id INTEGER,
            FOREIGN KEY(source_id) REFERENCES symbols(id),
            FOREIGN KEY(target_id) REFERENCES symbols(id)
        );
        CREATE TABLE IF NOT EXISTS file_edges (
            id INTEGER PRIMARY KEY, source_file_id INTEGER NOT NULL,
            target_file_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'imports',
            symbol_count INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY,
            finding_id_str TEXT NOT NULL UNIQUE,
            subject_kind TEXT NOT NULL,
            subject_id INTEGER,
            claim TEXT NOT NULL,
            evidence_json TEXT,
            confidence TEXT NOT NULL,
            source_detector TEXT NOT NULL,
            source_version TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'open'
        );
        """
    )
    from roam.db.connection import USER_VERSION

    conn.execute(f"PRAGMA user_version = {USER_VERSION}")  # task #147: pass the open_db version gate
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'src/engine.py', 'python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, "
        "visibility, is_exported) VALUES "
        "(1, 1, 'helper', 'src.engine.helper', 'function', 1, 2, 'public', 1)"
    )
    conn.commit()
    conn.close()
    return tmp_path


def _invoke_auth_gaps(cli_runner, project_root, *args):

    from roam.commands.cmd_auth_gaps import auth_gaps_cmd

    obj = {"json": True, "sarif": False, "budget": 0, "ci_mode": False}
    old_cwd = Path.cwd()
    try:
        import os as _os

        _os.chdir(str(project_root))
        return cli_runner.invoke(auth_gaps_cmd, list(args), obj=obj, catch_exceptions=False)
    finally:
        import os as _os

        _os.chdir(str(old_cwd))


def test_forced_floor_carries_canonical_schema_version(cli_runner, tmp_path, monkeypatch):
    """W1317 regression: force cmd_auth_gaps's W607-ED ``serialize_envelope``
    floor to fire (monkeypatch ``json_envelope`` to raise, exactly as
    ``test_serialize_envelope_failure_marker_format`` in
    test_w607_ed_cmd_auth_gaps_warnings_out_envelope.py does) and assert
    the floor stub emitted straight through ``click.echo(to_json(envelope))``
    carries ``ENVELOPE_SCHEMA_VERSION`` -- NOT the pre-fix stale
    ``"1.0.0"`` literal.

    This is the test that did not exist: test_schema_versioning.py only
    ever drives json_envelope()'s happy path, so a floor that diverged
    from the canonical constant was invisible to a green suite. Driving
    ONE real floor end-to-end (rather than a static grep) proves the
    fix actually reaches the JSON a consumer parses.
    """
    from roam.commands import cmd_auth_gaps as _mod
    from roam.output.formatter import ENVELOPE_SCHEMA_VERSION

    project_root = _build_minimal_auth_gaps_project(tmp_path)

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("W1317-synthetic-serialize-envelope-raise")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_auth_gaps(cli_runner, project_root)
    assert result.exit_code == 0, result.output

    data = _json.loads(result.output)
    # Confirm we actually exercised the floor (the marker + command name
    # prove this isn't a happy-path envelope that skipped the raise).
    assert data.get("command") == "auth-gaps", data
    markers = [m for m in data.get("warnings_out") or [] if m.startswith("auth_gaps_serialize_envelope_failed:")]
    assert markers, f"expected the serialize_envelope floor to fire; got warnings_out={data.get('warnings_out')!r}"

    # THE regression assertion: the floor's schema_version must be the
    # live canonical constant, not a stale hardcoded literal.
    assert data.get("schema_version") == ENVELOPE_SCHEMA_VERSION, (
        f"W1317: envelope-floor schema_version drifted from the canonical "
        f"constant; expected {ENVELOPE_SCHEMA_VERSION!r}, got {data.get('schema_version')!r}. "
        f"This is the exact defect this wave fixed -- the floor is stamping "
        f"a hardcoded literal again."
    )
    assert data["schema_version"] != "1.0.0" or ENVELOPE_SCHEMA_VERSION == "1.0.0", (
        "W1317: floor regressed to the pre-fix stale '1.0.0' literal"
    )
