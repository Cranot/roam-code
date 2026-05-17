"""W1294 — ``roam smells --only <kind>`` detector-dispatch pushdown.

The second-cheapest perf win from ``(internal memo)``:
``roam smells`` ran all 24 detectors serially even when the caller wanted a
single signal. ``--only`` restricts the dispatch loop BEFORE each detector's
SQL/AST pass — the win is skipping work, not filtering output.

Three guarantees this file pins down:

1. ``--only`` is a work-skipping fast path: ``run_all_detectors(conn, only={...})``
   invokes only the named detectors, leaving others untouched. Verified by
   spying on the registered detector callables and asserting non-selected
   ones are never called.
2. Unknown ``--only`` ids hard-error at parse time with the registered set
   listed (Constraint 8 fixed-enum boundary at the work-dispatch layer).
   Distinct from ``--kind`` which warns gracefully for forward-compat with
   CI scripts.
3. Default behaviour (no ``--only``) is byte-identical to pre-W1294 —
   every registered detector still runs.

The closed-enum is registry-derived (``ALL_DETECTORS``) so this test does
not hard-code a smell-id list; new detectors land automatically.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.catalog import smells as smells_module
from roam.catalog.smells import ALL_DETECTORS, run_all_detectors
from roam.cli import cli

# ---------------------------------------------------------------------------
# run_all_detectors-level pushdown
# ---------------------------------------------------------------------------


def test_run_all_detectors_only_dispatches_subset(monkeypatch, tmp_path: Path) -> None:
    """``only`` restricts dispatch to the named ids BEFORE each detector runs.

    Patches the registered detector callables with spies that record their
    invocation, then asserts that ``only={"brain-method"}`` invokes exactly
    one spy (brain-method) and leaves every other spy untouched. This is
    the work-skipping guarantee — the win we ship.
    """
    invoked: set[str] = set()

    def _spy(smell_id: str):
        def _spy_inner(_conn):
            invoked.add(smell_id)
            return []

        _spy_inner.__name__ = f"spy_{smell_id.replace('-', '_')}"
        return _spy_inner

    patched = [(smell_id, _spy(smell_id)) for smell_id, _fn in ALL_DETECTORS]
    monkeypatch.setattr(smells_module, "ALL_DETECTORS", patched)

    db_path = tmp_path / "index.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        run_all_detectors(conn, only=frozenset({"brain-method"}))
    finally:
        conn.close()

    assert invoked == {"brain-method"}, f"Expected only brain-method to dispatch under --only; got {sorted(invoked)}"


def test_run_all_detectors_only_none_dispatches_all(monkeypatch, tmp_path: Path) -> None:
    """``only=None`` (default) keeps the pre-W1294 dispatch-all contract.

    Existing callers — ``cmd_suggest_refactoring``, ``file_health_scores``,
    every test in ``test_smells.py`` — pass no ``only`` and must see every
    detector fire.
    """
    invoked: set[str] = set()

    def _spy(smell_id: str):
        def _spy_inner(_conn):
            invoked.add(smell_id)
            return []

        _spy_inner.__name__ = f"spy_{smell_id.replace('-', '_')}"
        return _spy_inner

    patched = [(smell_id, _spy(smell_id)) for smell_id, _fn in ALL_DETECTORS]
    monkeypatch.setattr(smells_module, "ALL_DETECTORS", patched)

    db_path = tmp_path / "index.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        run_all_detectors(conn)
    finally:
        conn.close()

    expected = {smell_id for smell_id, _fn in patched}
    assert invoked == expected, (
        f"Default dispatch must invoke every registered detector. "
        f"Missing: {sorted(expected - invoked)}; "
        f"Extra: {sorted(invoked - expected)}"
    )


def test_run_all_detectors_only_empty_set_dispatches_none(monkeypatch, tmp_path: Path) -> None:
    """An explicit empty ``only`` set runs zero detectors.

    Pins the distinction between ``only=None`` (run all — default) and
    ``only=frozenset()`` (run none — explicit empty). Important because a
    caller computing the set programmatically must be able to distinguish
    "no filter" from "filter to nothing".
    """
    invoked: set[str] = set()

    def _spy(smell_id: str):
        def _spy_inner(_conn):
            invoked.add(smell_id)
            return []

        _spy_inner.__name__ = f"spy_{smell_id.replace('-', '_')}"
        return _spy_inner

    patched = [(smell_id, _spy(smell_id)) for smell_id, _fn in ALL_DETECTORS]
    monkeypatch.setattr(smells_module, "ALL_DETECTORS", patched)

    db_path = tmp_path / "index.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        result = run_all_detectors(conn, only=frozenset())
    finally:
        conn.close()

    assert invoked == set()
    assert result == []


# ---------------------------------------------------------------------------
# CLI-level validation
# ---------------------------------------------------------------------------


def test_cli_only_unknown_kind_raises_usage_error(tmp_path: Path) -> None:
    """Unknown ``--only`` id hard-errors with the valid id list (Constraint 8).

    Distinct from ``--kind`` (which warns into ``warnings_out`` and continues).
    ``--only`` gates which work runs, so a typo must surface fast — silent
    success on a typo means we did zero work and reported a false-clean.
    """
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # Build a minimal repo + index so ensure_index() doesn't bail before
        # our flag-validation fires.
        Path("a.py").write_text("x = 1\n", encoding="utf-8")
        import subprocess

        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i"],
            check=True,
        )
        result_init = runner.invoke(cli, ["init"])
        assert result_init.exit_code == 0, result_init.output

        result = runner.invoke(cli, ["smells", "--only", "not-a-real-smell"])

    assert result.exit_code != 0, f"Expected non-zero exit; got output: {result.output}"
    # UsageError message names the offending flag, the typo, and lists the
    # valid registered set so the user can pick a real id.
    assert "--only" in result.output
    assert "not-a-real-smell" in result.output
    # One representative registered id must appear in the listed valid set —
    # we don't hard-code the full list, but brain-method has been registered
    # since W93 and is a stable anchor.
    assert "brain-method" in result.output


def test_cli_only_valid_kind_runs_to_completion(tmp_path: Path) -> None:
    """A valid ``--only`` id completes successfully and the JSON envelope
    surfaces a result confined to the requested smell kind.

    Smoke-test the full CLI path end-to-end: parse, validate, dispatch,
    persist-side-effect-free, emit JSON. No detector-id hardcoding beyond
    the brain-method anchor used in the unknown-id test above.
    """
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("a.py").write_text("x = 1\n", encoding="utf-8")
        import subprocess

        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i"],
            check=True,
        )
        result_init = runner.invoke(cli, ["init"])
        assert result_init.exit_code == 0, result_init.output

        result = runner.invoke(cli, ["--json", "smells", "--only", "brain-method"])

    assert result.exit_code == 0, result.output
    import json as _json

    payload = _json.loads(result.output)
    # Every emitted finding (if any) must carry the requested smell_id —
    # by construction, the other 23 detectors did not run.
    findings = payload.get("smells", [])
    for f in findings:
        # Findings are wrapped in {value, confidence, reason} triples by
        # cmd_smells; the smell_id lives on the inner value dict.
        inner = f.get("value", f)
        assert inner.get("smell_id") == "brain-method", inner


# ---------------------------------------------------------------------------
# Forward-compat regression guard
# ---------------------------------------------------------------------------


def test_only_validation_set_is_registry_derived() -> None:
    """The ``--only`` valid set MUST be derived from ``ALL_DETECTORS``.

    Prevents a future refactor from hard-coding the smell-id list inline
    (which would mean every new detector requires a manual edit + drift
    risk). The dispatchable set is the single source of truth; cmd_smells
    must read from it at runtime.
    """
    import inspect

    import roam.commands.cmd_smells as cmd_smells_mod

    src = inspect.getsource(cmd_smells_mod.smells.callback)
    # The validation block must reference ALL_DETECTORS (the dispatchable
    # registry view), not a literal list / tuple of smell ids.
    assert "ALL_DETECTORS" in src, (
        "cmd_smells must derive --only valid set from ALL_DETECTORS; "
        "do not inline-hardcode smell ids (Constraint 8 + registry-parity-pattern)."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
