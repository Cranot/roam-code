"""Tests for the world-model transaction-boundary detector (R28 sub-feature 4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402


def _classify(sym, *, proj):
    from roam.db.connection import open_db
    from roam.world_model.tx_boundaries import classify_tx_boundaries

    with open_db(readonly=True) as conn:
        return classify_tx_boundaries(conn, symbol_name=sym)


def test_transactional_classified_correctly(project_factory, monkeypatch):
    """`with db.transaction(): db.execute('INSERT ...')` → transactional."""
    proj = project_factory(
        {
            "src/svc.py": (
                "import db\n"
                "\n"
                "def create_user(name):\n"
                "    with db.transaction():\n"
                '        db.execute("INSERT INTO users (name) VALUES (?)", (name,))\n'
                "        db.execute(\"INSERT INTO audit (action) VALUES ('create_user')\")\n"
                "        db.commit()\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    results = _classify("create_user", proj=proj)

    assert results, "Expected classification for 'create_user'"
    c = results[0]
    assert c.classification == "transactional", (
        f"Expected transactional, got {c.classification} "
        f"(begin={c.begin_markers}, commit={c.commit_markers}, "
        f"mut_in={c.mutations_inside}, mut_out={c.mutations_outside}, "
        f"issues={c.issues})"
    )
    assert c.mutations_outside == 0
    assert c.confidence in ("high", "medium")
    assert c.begin_markers, "Expected begin marker recorded"
    assert c.commit_markers, "Expected commit marker recorded"


def test_unsafe_mutation_classified(project_factory, monkeypatch):
    """`db.execute('INSERT ...')` without transaction wrapper → unsafe_mutation."""
    proj = project_factory(
        {
            "src/svc.py": (
                "import db\n"
                "\n"
                "def create_user_raw(name):\n"
                '    db.execute("INSERT INTO users (name) VALUES (?)", (name,))\n'
            ),
        }
    )
    monkeypatch.chdir(proj)
    results = _classify("create_user_raw", proj=proj)

    assert results
    c = results[0]
    assert c.classification == "unsafe_mutation", (
        f"Expected unsafe_mutation, got {c.classification} (mut_in={c.mutations_inside}, mut_out={c.mutations_outside})"
    )
    assert c.mutations_outside >= 1
    assert c.issues, "Expected an issue noting mutations-outside-transaction"


def test_unmatched_begin_detected(project_factory, monkeypatch):
    """`db.begin(); db.execute('INSERT...')` no commit → unmatched_begin."""
    proj = project_factory(
        {
            "src/svc.py": (
                "import db\n"
                "\n"
                "def leaky_save(name):\n"
                "    db.begin()\n"
                '    db.execute("INSERT INTO users (name) VALUES (?)", (name,))\n'
                "    return name\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    results = _classify("leaky_save", proj=proj)

    assert results
    c = results[0]
    assert c.classification == "unmatched_begin", (
        f"Expected unmatched_begin, got {c.classification} "
        f"(begin={c.begin_markers}, commit={c.commit_markers}, "
        f"rollback={c.rollback_markers})"
    )
    assert c.begin_markers, "Expected begin marker recorded"
    assert not c.commit_markers and not c.rollback_markers
    assert any("leak" in i or "begin" in i for i in c.issues)


def test_unmatched_commit_detected(project_factory, monkeypatch):
    """`db.commit()` with no preceding begin → unmatched_commit."""
    proj = project_factory(
        {
            "src/svc.py": ("import db\n\ndef stray_commit():\n    db.commit()\n    return True\n"),
        }
    )
    monkeypatch.chdir(proj)
    results = _classify("stray_commit", proj=proj)

    assert results
    c = results[0]
    assert c.classification == "unmatched_commit", (
        f"Expected unmatched_commit, got {c.classification} (begin={c.begin_markers}, commit={c.commit_markers})"
    )
    assert c.commit_markers, "Expected commit marker recorded"
    assert not c.begin_markers


def test_partial_transactional(project_factory, monkeypatch):
    """One mutation inside the scope, one outside → partial_transactional."""
    proj = project_factory(
        {
            "src/svc.py": (
                "import db\n"
                "\n"
                "def mixed_save(name):\n"
                "    db.execute(\"INSERT INTO log (action) VALUES ('start')\")\n"
                "    with db.transaction():\n"
                '        db.execute("INSERT INTO users (name) VALUES (?)", (name,))\n'
                "        db.commit()\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    results = _classify("mixed_save", proj=proj)

    assert results
    c = results[0]
    assert c.classification == "partial_transactional", (
        f"Expected partial_transactional, got {c.classification} "
        f"(mut_in={c.mutations_inside}, mut_out={c.mutations_outside}, "
        f"issues={c.issues})"
    )
    assert c.mutations_inside >= 1
    assert c.mutations_outside >= 1


def test_non_transactional_pure_function(project_factory, monkeypatch):
    """A pure function with no mutations → non_transactional."""
    proj = project_factory(
        {
            "src/pure.py": ("def add(a, b):\n    return a + b\n"),
        }
    )
    monkeypatch.chdir(proj)
    results = _classify("add", proj=proj)

    assert results
    c = results[0]
    assert c.classification == "non_transactional", f"Expected non_transactional, got {c.classification}"
    assert c.mutations_inside == 0
    assert c.mutations_outside == 0
    assert not c.issues


def test_django_atomic_recognized(project_factory, monkeypatch):
    """`@transaction.atomic` decorator + mutations → transactional."""
    proj = project_factory(
        {
            "src/views.py": (
                "from django.db import transaction\n"
                "import db\n"
                "\n"
                "@transaction.atomic\n"
                "def save_user(name):\n"
                '    db.execute("INSERT INTO users (name) VALUES (?)", (name,))\n'
                '    db.execute("UPDATE counters SET value = value + 1")\n'
            ),
        }
    )
    monkeypatch.chdir(proj)
    results = _classify("save_user", proj=proj)

    assert results
    c = results[0]
    assert c.classification == "transactional", (
        f"Expected transactional via @transaction.atomic, got {c.classification} (begin={c.begin_markers})"
    )
    assert any("atomic" in m.get("pattern", "") for m in c.begin_markers), (
        f"Expected @transaction.atomic marker in begin_markers={c.begin_markers}"
    )


def test_envelope_includes_by_classification(project_factory, monkeypatch, cli_runner):
    """``roam --json tx-boundaries`` envelope surfaces by_classification rollup."""
    proj = project_factory(
        {
            "src/mixed.py": (
                "import db\n"
                "\n"
                "def pure_add(a, b):\n"
                "    return a + b\n"
                "\n"
                "def unsafe_insert(name):\n"
                '    db.execute("INSERT INTO users (name) VALUES (?)", (name,))\n'
                "\n"
                "def proper_save(name):\n"
                "    with db.transaction():\n"
                '        db.execute("INSERT INTO users (name) VALUES (?)", (name,))\n'
                "        db.commit()\n"
                "\n"
                "def leaky():\n"
                "    db.begin()\n"
                "    db.execute(\"INSERT INTO users (name) VALUES ('a')\")\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["tx-boundaries", "--top", "10"], json_mode=True)
    assert result.exit_code == 0, f"tx-boundaries failed: {result.output}"
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)

    assert data["command"] == "tx-boundaries"
    summary = data["summary"]
    assert "by_classification" in summary
    assert "high_severity_count" in summary
    assert "classification_definition" in summary
    by_cls = summary["by_classification"]
    # Expect at least one unsafe_mutation and one transactional.
    assert by_cls.get("unsafe_mutation", 0) >= 1, f"Expected unsafe_mutation in by_classification={by_cls}"
    assert by_cls.get("transactional", 0) >= 1, f"Expected transactional in by_classification={by_cls}"

    # boundaries list is non-empty and contains the structured shape.
    boundaries = data["boundaries"]
    assert len(boundaries) > 0
    first = boundaries[0]
    for key in (
        "symbol",
        "classification",
        "begin_markers",
        "commit_markers",
        "rollback_markers",
        "mutations_inside",
        "mutations_outside",
        "confidence",
        "issues",
    ):
        assert key in first, f"Missing {key} in boundaries[0]: {first}"

    ac = data["agent_contract"]
    assert "facts" in ac and len(ac["facts"]) > 0
    # LAW 4: facts should anchor on concrete-noun ("functions with ...").
    assert any("functions" in f or "function" in f for f in ac["facts"]), (
        f"Expected concrete-noun facts, got {ac['facts']}"
    )
    # LAW 2: next_commands use imperative voice.
    assert any(nc.startswith("roam ") for nc in ac["next_commands"])


def test_command_registered_in_cli(cli_runner, indexed_project, monkeypatch):
    """`roam tx-boundaries` is wired into the CLI registry."""
    monkeypatch.chdir(indexed_project)
    result = invoke_cli(cli_runner, ["tx-boundaries"], json_mode=True)
    assert result.exit_code == 0
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert data["command"] == "tx-boundaries"
    # Always emit a non-empty envelope (Pattern 1: never empty stdout).
    assert "summary" in data
    assert "verdict" in data["summary"]
