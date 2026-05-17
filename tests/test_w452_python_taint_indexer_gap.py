"""W452 — pin the indexer-side gap that silently no-ops python-* taint rules.

The python-* taint rules in ``src/roam/security/taint_rules/`` enumerate
canonical Python sinks and sources using their import-bound,
attribute-access spellings (``request.args``, ``request.form``,
``render_template_string``, ``pickle.loads``, ``yaml.load``,
``cursor.execute``, ``subprocess.run``, ``os.system``, ...).

But the Python indexer at ``src/roam/languages/python_lang.py`` only
records *function and class definitions* and *call edges between them*
as symbols. It does NOT record:

* Import-bound names (the imported ``render_template_string`` from
  ``from flask import render_template_string`` never lands as a symbol
  separate from its definition in the flask package — which is not in
  the workspace).
* Attribute-access chains (``request.args.get(...)`` produces zero
  symbols matching ``request.args``).

Net effect on real Flask / Django code: the python-* taint rules load
cleanly, are listed in ``rule_ids``, advertise non-empty source/sink
sets, but match ZERO symbols and emit ZERO findings on a canonical
positive case. This is a silent-no-op shape (Pattern 2 in CLAUDE.md):
``verdict: "No taint findings"`` is indistinguishable from a clean run.

Existing engine-level tests (``tests/test_w681_taint_engine_positive_smoke.py``
and ``tests/test_taint_ssti.py``) document this in their docstrings and
work around it by inserting synthetic ``qualified_name`` rows directly
(W681) or by asserting rule-shape invariants and skipping the
empirically-zero finding outcome (test_taint_ssti). This test file is
the END-TO-END regression pin: it walks the real CLI pipeline
(``roam index`` -> ``roam taint``) on a synthetic Flask SSTI fixture and
asserts the bug.

The tests below are marked ``xfail(strict=True)``. They will:

* PASS (as xfail) for as long as the indexer does NOT capture
  import-bound name references, so python-ssti continues to silently
  no-op on real Flask code.
* FAIL (as XPASS) the moment W452 is closed — at which point the
  ``strict=True`` makes the suite explode, forcing the W452-closer to
  flip these tests to assertions of correct behaviour.

This is the canonical agi-in-md CP44/CP45 discipline: load fallback
paths emit a finding, but the "no symbols matched" silent-zero path
above still inherits the engine's silence. Make the absence loud.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
# The W452 regression pins. xfail(strict=True) — they will XPASS the moment
# the indexer gains import-bound reference capture, forcing W452 closure to
# convert these into positive assertions.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W452: indexer does not capture import-bound names "
        "(request.args, render_template_string). The python-ssti rule "
        "silently emits 0 findings on canonical Flask SSTI. Pin will "
        "XPASS once W452 closes the indexer gap; flip to assertion."
    ),
)
def test_python_ssti_flags_real_flask_request_args_to_render_template_string(
    ssti_real_world_project: Path,
) -> None:
    """python-ssti SHOULD flag the canonical Flask request -> template chain.

    When W452 lands, this test must pass (W452-closer flips the xfail to
    a positive assertion).
    """
    data = _run_taint_json(ssti_real_world_project, "ssti")
    findings = data.get("summary", {}).get("findings", 0)
    assert findings >= 1, (
        f"python-ssti silently emitted 0 findings on canonical Flask SSTI; "
        f"verdict={data.get('summary', {}).get('verdict')!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W452: indexer does not capture import-bound names "
        "(pickle.loads, request.data). The python-deserialization rule "
        "silently emits 0 findings on canonical pickle RCE shape. Pin "
        "will XPASS once W452 closes the indexer gap; flip to assertion."
    ),
)
def test_python_deserialization_flags_real_pickle_loads_chain(
    pickle_deserialization_project: Path,
) -> None:
    """python-deserialization SHOULD flag request.data -> pickle.loads."""
    data = _run_taint_json(pickle_deserialization_project, "deserialization")
    findings = data.get("summary", {}).get("findings", 0)
    assert findings >= 1, (
        f"python-deserialization silently emitted 0 findings on canonical "
        f"pickle RCE; verdict={data.get('summary', {}).get('verdict')!r}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W452: indexer does not capture import-bound names "
        "(request.args, cursor.execute). The python-sqli rule "
        "silently emits 0 findings on canonical SQLi chain. Pin will "
        "XPASS once W452 closes the indexer gap; flip to assertion."
    ),
)
def test_python_sqli_flags_real_request_args_to_cursor_execute(
    sqli_cursor_project: Path,
) -> None:
    """python-sqli SHOULD flag request.args -> cursor.execute."""
    data = _run_taint_json(sqli_cursor_project, "sqli")
    findings = data.get("summary", {}).get("findings", 0)
    assert findings >= 1, (
        f"python-sqli silently emitted 0 findings on canonical SQLi; "
        f"verdict={data.get('summary', {}).get('verdict')!r}"
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
    assert "python-ssti" in data.get("rule_ids", []), (
        f"python-ssti not in loaded rules list: {data.get('rule_ids')!r}"
    )
    # The summary must claim 1 rule loaded (the ssti pack contains only it).
    assert data.get("summary", {}).get("rules") == 1, (
        f"expected 1 rule loaded in ssti pack; got "
        f"{data.get('summary', {}).get('rules')}"
    )
