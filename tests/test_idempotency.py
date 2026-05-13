"""Tests for the world-model idempotency detector (R28 sub-feature 2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402


def test_pure_function_is_idempotent(project_factory, monkeypatch):
    """A pure function is trivially idempotent."""
    proj = project_factory(
        {
            "src/pure.py": (
                "def add(a, b):\n"
                "    return a + b\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.idempotency import classify_idempotency

    with open_db(readonly=True) as conn:
        results = classify_idempotency(conn, symbol_name="add")

    assert results, "Expected classification for 'add'"
    c = results[0]
    assert c.kind == "idempotent"
    assert c.confidence == "high"


def test_mkdir_with_exist_ok_is_idempotent(project_factory, monkeypatch):
    """`Path(...).mkdir(parents=True, exist_ok=True)` is idempotent (write-with-check)."""
    proj = project_factory(
        {
            "src/dirs.py": (
                "from pathlib import Path\n"
                "\n"
                "def ensure_dir(p):\n"
                "    out = Path(p)\n"
                "    out.mkdir(parents=True, exist_ok=True)\n"
                "    out.write_text('marker')\n"
                "    return out\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.idempotency import classify_idempotency

    with open_db(readonly=True) as conn:
        results = classify_idempotency(conn, symbol_name="ensure_dir")

    assert results, "Expected classification for 'ensure_dir'"
    c = results[0]
    assert c.kind == "idempotent", f"Expected idempotent, got {c.kind} (evidence={c.evidence})"
    assert "check_patterns" in c.evidence or "reason" in c.evidence


def test_naive_write_is_non_idempotent(project_factory, monkeypatch):
    """A `with open(path, 'w'): f.write(content)` with no check is non_idempotent."""
    proj = project_factory(
        {
            "src/naive.py": (
                "def overwrite(path, content):\n"
                "    with open(path, 'w') as f:\n"
                "        f.write(content)\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.idempotency import classify_idempotency

    with open_db(readonly=True) as conn:
        results = classify_idempotency(conn, symbol_name="overwrite")

    assert results
    c = results[0]
    assert c.kind == "non_idempotent", (
        f"Expected non_idempotent, got {c.kind} (evidence={c.evidence})"
    )


def test_subprocess_is_unknown(project_factory, monkeypatch):
    """A function that spawns a subprocess has unknown idempotency."""
    proj = project_factory(
        {
            "src/proc.py": (
                "import subprocess\n"
                "\n"
                "def run_external():\n"
                "    return subprocess.run(['true'])\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db
    from roam.world_model.idempotency import classify_idempotency

    with open_db(readonly=True) as conn:
        results = classify_idempotency(conn, symbol_name="run_external")

    assert results
    c = results[0]
    assert c.kind == "unknown"
    assert "subprocess" in c.evidence.get("reason", "")


def test_envelope_uses_side_effects_input(project_factory, monkeypatch, cli_runner):
    """``roam --json idempotency`` envelope composes on side-effects kinds."""
    proj = project_factory(
        {
            "src/mixed.py": (
                "import subprocess\n"
                "import requests\n"
                "\n"
                "def pure_add(a, b):\n"
                "    return a + b\n"
                "\n"
                "def overwrite(path):\n"
                "    with open(path, 'w') as f:\n"
                "        f.write('x')\n"
                "\n"
                "def ensure_dir(p):\n"
                "    from pathlib import Path\n"
                "    Path(p).mkdir(parents=True, exist_ok=True)\n"
                "\n"
                "def spawner():\n"
                "    return subprocess.run(['ls'])\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["idempotency", "--top", "10"], json_mode=True)
    assert result.exit_code == 0, f"idempotency failed: {result.output}"
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)

    assert data["command"] == "idempotency"
    summary = data["summary"]
    assert "by_kind" in summary
    assert summary["state"] == "ok"
    assert summary["partial_success"] is False
    by_kind = summary["by_kind"]
    # Expect each of our three buckets to surface at least once.
    assert by_kind.get("idempotent", 0) >= 1
    assert by_kind.get("non_idempotent", 0) >= 1
    assert by_kind.get("unknown", 0) >= 1

    # Every classification carries side_effect_kinds OR a reason — proving
    # composition on the side-effects detector.
    classifications = data["classifications"]
    assert len(classifications) > 0
    found_composition_evidence = False
    for c in classifications:
        ev = c.get("evidence") or {}
        if "side_effect_kinds" in ev:
            found_composition_evidence = True
            break
    assert found_composition_evidence, (
        "Expected at least one classification's evidence to include "
        "'side_effect_kinds' (proving the idempotency detector composes "
        "on top of the side-effects classifier)"
    )

    # next_commands link back to side-effects (cross-reference)
    ac = data["agent_contract"]
    assert any("roam side-effects" in nc for nc in ac["next_commands"])


def test_idempotency_command_appears_in_cli(cli_runner, indexed_project, monkeypatch):
    """`roam idempotency` and `roam side-effects` are wired into the CLI."""
    monkeypatch.chdir(indexed_project)

    # Just verifies the commands are dispatchable (don't crash on a tiny repo).
    result = invoke_cli(cli_runner, ["side-effects"], json_mode=True)
    assert result.exit_code == 0
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert data["command"] == "side-effects"

    result = invoke_cli(cli_runner, ["idempotency"], json_mode=True)
    assert result.exit_code == 0
    raw = getattr(result, "stdout", None) or result.output
    data = json.loads(raw)
    assert data["command"] == "idempotency"
