"""W452 / #285 — CLOSED 2026-07-27. Was: pin the indexer-side gap that
silently no-op'd python-* taint rules. Now: regression pin for the fix.

The python-* taint rules in ``src/roam/security/taint_rules/`` enumerate
canonical Python sinks and sources using their import-bound,
attribute-access spellings (``request.args``, ``request.form``,
``render_template_string``, ``pickle.loads``, ``yaml.load``,
``cursor.execute``, ``subprocess.run``, ``os.system``, ...).

The Python indexer at ``src/roam/languages/python_lang.py`` only records
*function and class definitions* and *call edges between them* as
``symbols`` rows — it does NOT record import-bound names or
attribute-access chains as standalone symbols (they're not local
definitions). ``run_taint``'s original DB-only matching
(``_symbols_matching`` in ``roam.security.taint_engine``) therefore
matched ZERO rows for these rules on real Flask/Django code: rules
loaded cleanly, were listed in ``rule_ids``, advertised non-empty
source/sink sets, but emitted ZERO findings on a canonical positive
case — a silent-no-op shape (Pattern 2 in CLAUDE.md) where ``verdict:
"No taint findings"`` was indistinguishable from a clean run.

THE FIX (2026-07-27, task #285): ``run_taint`` gained a text-scan
fallback (``_text_scan_rule_anchors`` in ``taint_engine.py``) that
activates when a rule's DB-indexed sources/sinks come back empty and a
``project_root`` was supplied. It re-reads the already-indexed Python
file, masks comments/strings the same way the W167 import-verifier
does, and anchors each literal source/sink text occurrence to its
enclosing function via the already-indexed ``line_start``/``line_end``
span — real symbol ids, so they slot straight into the existing
forward-BFS pass, plus a same-function co-occurrence pass for the
"source and sink both in one handler" shape forward BFS can't express.
Opt-in via an explicit ``project_root`` kwarg (default ``None``): every
pre-existing direct ``run_taint(conn, rules)`` call site (most of
``tests/test_taint.py``, ``test_taint_ssti.py``'s engine-level classes,
``test_w681_taint_engine_positive_smoke.py``) is byte-identical; only
the CLI path (``cmd_taint.py``) passes ``project_root`` and gets the
fallback. Separately, ``roam taint``'s JSON envelope now discloses
``summary.anchor_coverage`` (``rules_evaluated`` / ``rules_zero_anchors``)
and, when non-zero, ``partial_success`` + a ``warnings_out`` entry
naming which rules never resolved an anchor at all — the residual
"instrument counted nothing" case (non-Python rules, or a rule with a
name/pattern this fallback can't yet resolve) stays LOUD rather than
folding into an indistinguishable "0 findings" verdict.

Existing engine-level tests (``tests/test_w681_taint_engine_positive_smoke.py``
and ``tests/test_taint_ssti.py``) predate this fix and deliberately call
``run_taint`` WITHOUT ``project_root`` — they still lock rule-shape
invariants on the DB-only path and are unaffected. This test file is
the END-TO-END regression pin: it walks the real CLI pipeline
(``roam index`` -> ``roam taint``) on a synthetic Flask SSTI fixture and
now asserts the CORRECT (fixed) behaviour.

This is the canonical agi-in-md CP44/CP45 discipline: load fallback
paths emit a finding, but the "no symbols matched" silent-zero path
above used to still inherit the engine's silence. The absence is now
loud, and the positive case is now detected.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli
from tests.conftest import make_src_project as _make_project


def _commit_fixture(proj: Path) -> None:
    """Initialise a tiny git repo under *proj* so ``roam index`` sees the
    fixture file via ``git ls-files``. ``make_src_project`` already
    does this for the ``src/`` tree, but our fixtures sit at the root."""
    subprocess.run(["git", "init", "-q"], cwd=proj, check=False)
    subprocess.run(["git", "add", "."], cwd=proj, check=False)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=proj,
        check=False,
    )


@pytest.fixture
def ssti_real_world_project(tmp_path: Path) -> Path:
    """A textbook Flask SSTI: ``request.args`` -> ``render_template_string``.

    Reproduces the canonical CVE shape (CVE-2018-1000656-class, Flask
    template injection). Both the source and the sink are import-bound
    from the flask package — the EXACT shape the python-ssti rule is
    designed to flag and the EXACT shape the indexer fails to capture.
    """
    proj = _make_project(
        tmp_path,
        {
            "app.py": """
                from flask import Flask, request, render_template_string

                app = Flask(__name__)

                @app.route('/greet')
                def handle_greet():
                    name = request.args.get('name')
                    template = '<h1>Hello ' + name + '</h1>'
                    return render_template_string(template)
            """,
        },
    )
    return proj


@pytest.fixture
def pickle_deserialization_project(tmp_path: Path) -> Path:
    """Textbook insecure deserialization: HTTP body -> pickle.loads.

    CVE-2022-22965 / CVE-2017-7235 class. Source is the import-bound
    ``request.data`` attribute, sink is the import-bound ``pickle.loads``
    callable. The python-deserialization rule should flag this; the
    indexer doesn't surface either symbol so the engine sees nothing.
    """
    proj = _make_project(
        tmp_path,
        {
            "app.py": """
                import pickle
                from flask import request

                def deserialize_user():
                    raw = request.data
                    return pickle.loads(raw)
            """,
        },
    )
    return proj


@pytest.fixture
def sqli_cursor_project(tmp_path: Path) -> Path:
    """Textbook string-formatted SQLi: ``request.args`` -> ``cursor.execute``.

    CWE-89 canonical positive. The python-sqli rule enumerates
    ``cursor.execute`` as a sink and ``request.args`` / ``request.form``
    as sources. Neither lands as a symbol on real code.
    """
    proj = _make_project(
        tmp_path,
        {
            "app.py": """
                import sqlite3
                from flask import request

                def lookup_user():
                    name = request.args.get('name')
                    conn = sqlite3.connect('users.db')
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM users WHERE name='" + name + "'")
                    return cursor.fetchone()
            """,
        },
    )
    return proj


def _run_taint_json(proj: Path, rules_pack: str) -> dict:
    """Index + run ``roam --json taint --rules-pack <pack>`` inside *proj*.

    Returns the parsed JSON envelope. Uses CliRunner (in-process) for
    speed and identical exit semantics to a real CLI invocation.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        r = runner.invoke(cli, ["index"])
        assert r.exit_code == 0, f"index failed: {r.output!r}"
        r = runner.invoke(cli, ["--json", "taint", "--rules-pack", rules_pack])
        assert r.exit_code == 0, f"taint failed: {r.output!r}"
        return json.loads(r.output)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# The W452 regression pins. Were xfail(strict=True); flipped to plain
# assertions 2026-07-27 when the text-scan fallback (see module docstring)
# closed the indexer gap for these three canonical shapes.
# ---------------------------------------------------------------------------


def test_python_ssti_flags_real_flask_request_args_to_render_template_string(
    ssti_real_world_project: Path,
) -> None:
    """python-ssti SHOULD flag the canonical Flask request -> template chain.

    W452/#285 CLOSED (2026-07-27): ``run_taint`` now falls back to a
    text-scan anchor pass (``roam.security.taint_engine._text_scan_rule_anchors``)
    when a rule's DB-indexed sources/sinks come back empty. It re-reads
    the already-indexed Python file, masks comments/strings the same
    way the W167 import-verifier does, and anchors each literal
    ``request.args`` / ``render_template_string`` occurrence to its
    enclosing function via the already-indexed ``line_start``/``line_end``
    span. Both patterns land in ``handle_greet`` here, so the
    same-function co-occurrence path fires directly.
    """
    data = _run_taint_json(ssti_real_world_project, "ssti")
    findings = data.get("summary", {}).get("findings", 0)
    assert findings >= 1, (
        f"python-ssti silently emitted 0 findings on canonical Flask SSTI; "
        f"verdict={data.get('summary', {}).get('verdict')!r}"
    )


def test_python_deserialization_flags_real_pickle_loads_chain(
    pickle_deserialization_project: Path,
) -> None:
    """python-deserialization SHOULD flag request.data -> pickle.loads.

    W452/#285 CLOSED (2026-07-27): see the ssti test above — same
    text-scan fallback, same-function co-occurrence shape
    (``request.data`` and ``pickle.loads`` both inside
    ``deserialize_user``).
    """
    data = _run_taint_json(pickle_deserialization_project, "deserialization")
    findings = data.get("summary", {}).get("findings", 0)
    assert findings >= 1, (
        f"python-deserialization silently emitted 0 findings on canonical "
        f"pickle RCE; verdict={data.get('summary', {}).get('verdict')!r}"
    )


def test_python_sqli_flags_real_request_args_to_cursor_execute(
    sqli_cursor_project: Path,
) -> None:
    """python-sqli SHOULD flag request.args -> cursor.execute.

    W452/#285 CLOSED (2026-07-27): see the ssti test above — same
    text-scan fallback, same-function co-occurrence shape
    (``request.args`` and ``cursor.execute`` both inside
    ``lookup_user``).
    """
    data = _run_taint_json(sqli_cursor_project, "sqli")
    findings = data.get("summary", {}).get("findings", 0)
    assert findings >= 1, (
        f"python-sqli silently emitted 0 findings on canonical SQLi; verdict={data.get('summary', {}).get('verdict')!r}"
    )


# ---------------------------------------------------------------------------
# Loud-fallback complement: prove the engine wiring + rules pack still load
# correctly. These DO pass today — they assert the rule loads + runs (no
# crash) and that ``rule_ids`` contains the expected rule, separating "the
# rule is broken" (would crash these) from "the rule loaded but the indexer
# starved it" (xfail above).
# ---------------------------------------------------------------------------


def test_python_ssti_rule_loads_and_runs_on_indexed_corpus(
    ssti_real_world_project: Path,
) -> None:
    """Loud-fallback: the rule itself loads, runs, and lists in the envelope.

    Distinguishes "no findings because the rule is broken" (this test
    would crash) from "no findings because the indexer starved it of
    matchable symbols" (the xfail above).
    """
    data = _run_taint_json(ssti_real_world_project, "ssti")
    assert "python-ssti" in data.get("rule_ids", []), f"python-ssti not in loaded rules list: {data.get('rule_ids')!r}"
    # The summary must claim 1 rule loaded (the ssti pack contains only it).
    assert data.get("summary", {}).get("rules") == 1, (
        f"expected 1 rule loaded in ssti pack; got {data.get('summary', {}).get('rules')}"
    )
