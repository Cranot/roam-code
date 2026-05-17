"""W1259 — canonical detector vocabulary tests for ``roam findings``.

Pins the fix for the agent-E/agent-I dogfood gap: ``roam findings list
--detector <X>`` previously reported "unknown detector" for ~26 of the
~30 real detectors documented in CLAUDE.md, because the validator only
consulted ``count_by_detector(conn)`` — a runtime aggregate of
detectors that had ALREADY emitted rows on THIS project. Detectors
that are valid but haven't been invoked yet looked identical to typos.

Fix: maintain ``CANONICAL_DETECTOR_NAMES`` in ``roam.db.findings`` as a
static source-of-truth frozenset. ``known_detector_names(conn)`` returns
the UNION of canonical names + live counts. The ``--detector`` validator
in ``cmd_findings`` now disambiguates three states:

  * populated         — detector has rows, regular list path
  * not_yet_emitted   — canonical detector, 0 rows on this project
  * unknown_detector  — truly not in the canonical vocabulary

These tests pin all three transitions + verify the canonical set stays
synced with what detector modules actually emit.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli
from roam.db.connection import open_db
from roam.db.findings import (
    CANONICAL_DETECTOR_NAMES,
    FindingRecord,
    count_by_detector,
    emit_finding,
    known_detector_names,
)

# ---------------------------------------------------------------------------
# CANONICAL_DETECTOR_NAMES content
# ---------------------------------------------------------------------------


def test_canonical_detector_names_includes_well_known_detectors():
    """The canonical vocabulary recognises the high-traffic detectors that
    agent-E + agent-I previously got "unknown detector" for.

    Mirrors the 8-10 detector spread the wave-M scope asked for; the full
    set is much larger but this list guards the specific complaint."""
    expected = {
        "smells",
        "taint",
        "dead",
        "clones",
        "boundary",
        "vulns",
        "complexity",
        "n1",
        "missing-index",
        "auth-gaps",
    }
    missing = expected - set(CANONICAL_DETECTOR_NAMES)
    assert not missing, (
        f"CANONICAL_DETECTOR_NAMES is missing {missing} — extend the frozenset in src/roam/db/findings.py."
    )


def test_canonical_detector_names_size_at_least_25():
    """Headline: at least 25 distinct canonical detectors recognised."""
    assert len(CANONICAL_DETECTOR_NAMES) >= 25, (
        f"Only {len(CANONICAL_DETECTOR_NAMES)} canonical detectors — "
        f"CLAUDE.md documents ~26+ and the codebase emits ~30."
    )


# ---------------------------------------------------------------------------
# Drift guard: CANONICAL_DETECTOR_NAMES vs source tree
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Return the project root (the directory containing src/roam/)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src" / "roam").is_dir():
            return parent
    raise RuntimeError("Cannot locate repo root from " + str(here))


def _emitted_detector_names() -> set[str]:
    """AST-scan src/roam/ for every CALL-SITE keyword
    ``source_detector="<name>"``.

    Uses ``ast`` (not regex) so docstring mentions and comments don't
    pollute the result. Skips non-literal sites (f-strings, variable
    lookups, foreign re-emit in ``cmd_pr_risk``) — the canonical set
    lists "fan-symbol"/"fan-file" explicitly to cover the ``cmd_fan``
    conditional dispatch.
    """
    found: set[str] = set()
    root = _repo_root() / "src" / "roam"
    for path in root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "source_detector":
                    continue
                # Only count string-literal values — variable lookups
                # (cmd_fan's ``source_detector`` local var, cmd_pr_risk's
                # ``row["source_detector"]`` foreign re-emit) are not
                # name claims this guard should enforce.
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    found.add(kw.value.value)
    return found


def test_canonical_set_covers_every_string_literal_emit_site():
    """AST-scan guard: every ``source_detector="<X>"`` literal in src/roam/
    must be in CANONICAL_DETECTOR_NAMES.

    When you add a new detector that emits findings, you MUST extend
    CANONICAL_DETECTOR_NAMES in the same patch. Otherwise
    ``roam findings list --detector <new>`` will report "unknown" until
    the first row lands."""
    emitted = _emitted_detector_names()
    missing = emitted - set(CANONICAL_DETECTOR_NAMES)
    assert not missing, (
        f"Emit sites use these detector names but CANONICAL_DETECTOR_NAMES "
        f"does not list them: {sorted(missing)}. Extend the frozenset in "
        f"src/roam/db/findings.py."
    )


# ---------------------------------------------------------------------------
# known_detector_names helper
# ---------------------------------------------------------------------------


def _fresh_conn(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    return open_db(readonly=False, project_root=proj)


def test_known_detector_names_returns_union_of_canonical_and_live(tmp_path):
    """``known_detector_names`` = canonical UNION live distinct names.

    A custom non-canonical detector that has emitted rows still surfaces
    in the union (so a plugin-emitted detector won't be misclassified as
    a typo). Canonical detectors that have NOT emitted still surface
    (the main fix path)."""
    with _fresh_conn(tmp_path) as conn:
        # Seed a non-canonical "custom-plugin" row to prove the live
        # side of the union still feeds through.
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str="custom-plugin:sym:1",
                subject_kind="symbol",
                claim="from a plugin",
                source_detector="custom-plugin",
            ),
        )
        conn.commit()

        union = known_detector_names(conn)
        # Canonical names that have NOT emitted are still recognised.
        assert "taint" in union
        assert "smells" in union
        # Live non-canonical name flows through.
        assert "custom-plugin" in union
        # Sanity: at least 25 canonical + 1 live custom.
        assert len(union) >= 26


# ---------------------------------------------------------------------------
# CLI integration — the three states
# ---------------------------------------------------------------------------


def _seed_repo_and_index(tmp_path):
    """Tiny git repo + index. Findings table is empty."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()
    (src / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(proj),
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(proj), capture_output=True)

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
        assert result.exit_code == 0, f"index failed: {result.output}"
    finally:
        os.chdir(old_cwd)
    return proj


def _run_json(args, cwd):
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    raw = getattr(result, "stdout", None) or result.output
    try:
        return result, json.loads(raw)
    except json.JSONDecodeError:
        return result, None


def test_canonical_detector_with_zero_rows_returns_not_yet_emitted(tmp_path):
    """``--detector taint`` on a project with no taint rows now emits
    ``state: not_yet_emitted`` (NOT ``unknown_detector``).

    This is the agent-E + agent-I dogfood gap, sealed."""
    proj = _seed_repo_and_index(tmp_path)
    result, parsed = _run_json(["--json", "findings", "list", "--detector", "taint"], cwd=proj)
    assert result.exit_code == 0, result.output
    assert parsed is not None, f"non-JSON: {result.output!r}"
    summary = parsed["summary"]
    assert summary["state"] == "not_yet_emitted", summary
    assert summary["partial_success"] is True
    assert summary["requested_detector"] == "taint"
    assert "taint" in summary["known_detectors"]
    assert summary["total_findings"] == 0
    # Actionable next_command — agent should be told to run `roam taint`.
    next_commands = parsed["agent_contract"]["next_commands"]
    assert "roam taint" in next_commands, next_commands


def test_truly_unknown_detector_still_returns_unknown_detector(tmp_path):
    """A garbage name still resolves to ``unknown_detector`` — the fix
    only re-classifies CANONICAL-but-empty, not nonsense."""
    proj = _seed_repo_and_index(tmp_path)
    result, parsed = _run_json(
        ["--json", "findings", "list", "--detector", "garblargle"],
        cwd=proj,
    )
    assert result.exit_code == 0, result.output
    assert parsed is not None
    summary = parsed["summary"]
    assert summary["state"] == "unknown_detector", summary
    assert summary["requested_detector"] == "garblargle"


def test_unknown_detector_lists_canonical_vocabulary(tmp_path):
    """The "known detectors" disclosure on an unknown name now contains
    the full canonical set (not just live counts).

    Previously: 3 names listed on roam-code (boundary, clones, smells).
    Now: at least 25 names listed (the canonical vocabulary)."""
    proj = _seed_repo_and_index(tmp_path)
    result, parsed = _run_json(
        ["--json", "findings", "list", "--detector", "garblargle"],
        cwd=proj,
    )
    assert parsed is not None
    known = parsed["summary"]["known_detectors"]
    assert len(known) >= 25, (
        f"Only {len(known)} detectors disclosed on unknown_detector — "
        f"the fix should surface the full canonical vocabulary."
    )


def test_canonical_detector_with_rows_still_works(tmp_path):
    """Regression check: a populated detector still flows through the
    normal list path (not_yet_emitted only fires on 0-row canonical)."""
    proj = _seed_repo_and_index(tmp_path)
    with open_db(readonly=False, project_root=proj) as conn:
        emit_finding(
            conn,
            FindingRecord(
                finding_id_str="taint:sym:1",
                subject_kind="symbol",
                claim="taint test",
                source_detector="taint",
            ),
        )
        conn.commit()
    result, parsed = _run_json(
        ["--json", "findings", "list", "--detector", "taint"],
        cwd=proj,
    )
    assert result.exit_code == 0, result.output
    assert parsed is not None
    assert parsed["summary"]["state"] == "populated"
    assert parsed["summary"]["total_findings"] == 1
