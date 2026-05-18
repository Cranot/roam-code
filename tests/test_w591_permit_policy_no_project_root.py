"""W591 — ``_gather_permit_policy_decisions`` discloses project-root lookup failures.

This is the SIBLING fix to W590 (which closed the same hole on the lease
gatherer). ``_gather_permit_policy_decisions`` previously silently
returned ``[]`` when ``find_project_root()`` returned None or raised —
making "not a roam project" indistinguishable from "no permits on this
PR" in the replay envelope.

The gatherer now appends a structured warning string to the caller's
``warnings`` bucket on each silent-fallback path:

  * ``find_project_root`` returns None → ``"permits: project_root_not_found — ..."``
  * ``find_project_root`` raises        → ``"permits: project_root_lookup_failed — ..."``

The propagation channel is the SAME W590 chain (``pre_warnings`` →
``producer_warnings_out`` → envelope ``warnings_out``). No new plumbing.
Each warning prefix is a deliberate string anchor (``permits:`` + closed-
form kind) so an operator can grep the bucket and distinguish a "not a
roam project" run from a "broken permit file" run.

The W421 wave dealt with regex / id-validation toggling on the permit
SCHEMA path; W591 covers the orthogonal project-root lookup path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

# ---------------------------------------------------------------------------
# Unit-level tests on _gather_permit_policy_decisions
# ---------------------------------------------------------------------------


def test_permit_policy_warns_when_find_project_root_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``find_project_root`` returns None, the gatherer must emit a
    structured ``permits: project_root_not_found`` warning rather than
    silently returning an empty list.

    Pre-W591 behaviour: warnings list stays empty, rows stay empty,
    auditor cannot tell "no roam project" apart from "no permits".
    """
    import roam.db.connection as conn_mod
    from roam.commands import cmd_pr_replay  # noqa: F401 — referenced via attr below

    # The gatherer imports ``find_project_root`` inside the function via
    # ``from roam.db.connection import find_project_root`` — patch the
    # source module so the name re-import inside the function picks up
    # the patched callable.
    monkeypatch.setattr(conn_mod, "find_project_root", lambda: None)

    warnings: list[str] = []
    from roam.commands.cmd_pr_replay import _gather_permit_policy_decisions

    rows = _gather_permit_policy_decisions(warnings)

    assert rows == [], f"expected zero rows on missing project_root; got {rows!r}"
    assert len(warnings) == 1, f"expected one project_root_not_found warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("permits:"), msg
    assert "project_root_not_found" in msg, msg
    # The detail explains WHY (no .git ancestor / not a roam project).
    assert "find_project_root" in msg, msg


def test_permit_policy_warns_when_find_project_root_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``find_project_root`` raises, the gatherer must emit a
    structured ``permits: project_root_lookup_failed`` warning naming
    the exception class.
    """
    import roam.db.connection as conn_mod

    class _SyntheticLookupError(RuntimeError):
        pass

    def _boom() -> Path:
        raise _SyntheticLookupError("synthetic find_project_root failure")

    monkeypatch.setattr(conn_mod, "find_project_root", _boom)

    warnings: list[str] = []
    from roam.commands.cmd_pr_replay import _gather_permit_policy_decisions

    rows = _gather_permit_policy_decisions(warnings)

    assert rows == [], f"expected zero rows on find_project_root raise; got {rows!r}"
    assert len(warnings) == 1, f"expected one project_root_lookup_failed warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("permits:"), msg
    assert "project_root_lookup_failed" in msg, msg
    assert "_SyntheticLookupError" in msg, msg
    assert "synthetic find_project_root failure" in msg, msg


def test_permit_policy_silent_when_find_project_root_resolves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sanity check the W591 plumbing only fires on the missing/raising
    paths — a successful lookup (with no ``.roam/permits/`` dir on disk)
    should still be silent (zero W591 warnings, no project_root marker).
    """
    import roam.db.connection as conn_mod

    # Build a tmp project with .git but no .roam/permits dir.
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(conn_mod, "find_project_root", lambda: tmp_path)

    warnings: list[str] = []
    from roam.commands.cmd_pr_replay import _gather_permit_policy_decisions

    rows = _gather_permit_policy_decisions(warnings)
    assert rows == [], f"expected zero rows on bare project; got {rows!r}"
    # No W591-style project_root warning. The validated permit reader
    # (``load_permits_from_disk``) MAY emit its own per-file warnings if
    # there are malformed permits — but those don't carry the W591
    # ``project_root_*`` markers.
    for w in warnings:
        assert "project_root_not_found" not in w, w
        assert "project_root_lookup_failed" not in w, w


# ---------------------------------------------------------------------------
# Integration test — envelope surfaces W591 warning in warnings_out
# ---------------------------------------------------------------------------


def test_pr_replay_envelope_surfaces_permit_policy_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a warning emitted by ``_gather_permit_policy_decisions``
    must surface on the JSON envelope's top-level ``warnings_out`` field.

    Verifies the W591 propagation chain reuses the same W590 channel:
        gatherer warnings list (``pre_warnings``)
        → ``_collect_change_evidence``'s ``producer_warnings_out`` bucket
        → ``envelope_producer_warnings`` in ``pr_replay_cmd``
        → ``extra_payload["warnings_out"]``
        → ``json_envelope`` output.

    Mirrors W590's integration test: we can't directly monkeypatch
    ``find_project_root`` inside the full pr-replay invocation because
    ``ensure_index`` calls it BEFORE the gatherer runs and would crash
    on the None return. Instead we monkeypatch the gatherer itself to
    inject the canonical W591 marker string — the propagation chain is
    what's under test here; the gatherer's own emission is verified by
    the three unit-level tests above.
    """
    from roam.cli import cli
    from roam.commands import cmd_pr_replay

    # Build a tiny git repo so pr-replay's git rev-list call succeeds.
    proj = tmp_path / "tinyproj"
    proj.mkdir()
    (proj / "README.md").write_text("x\n")
    git_init(proj)

    canonical_marker = (
        "permits: project_root_not_found — find_project_root returned None "
        "(synthetic-injection from W591 propagation test)"
    )

    def _fake_gather_permit_policy_decisions(warnings: list[str]) -> list[dict]:
        warnings.append(canonical_marker)
        return []

    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_permit_policy_decisions",
        _fake_gather_permit_policy_decisions,
    )

    evidence_target = tmp_path / "evidence.json"

    runner = CliRunner()
    monkeypatch.chdir(proj)
    result = runner.invoke(
        cli,
        [
            "--json",
            "pr-replay",
            "--tier",
            "sample",
            "--evidence",
            str(evidence_target),
        ],
        catch_exceptions=False,
    )

    # The command should still succeed — Pattern-2 disclosure is
    # additive, never blocks.
    assert result.exit_code == 0, f"non-zero exit: {result.output[:400]}"

    # Locate the pr-replay envelope in stdout (matching W590's parsing).
    body = result.output
    decoder = json.JSONDecoder()
    payload = None
    search_start = 0
    while True:
        idx = body.find("{", search_start)
        if idx < 0:
            break
        try:
            obj, end = decoder.raw_decode(body[idx:])
        except json.JSONDecodeError:
            search_start = idx + 1
            continue
        if isinstance(obj, dict) and obj.get("command") == "pr-replay":
            payload = obj
            break
        search_start = idx + end
    assert payload is not None, f"could not locate pr-replay envelope in stdout; first 400 chars: {body[:400]!r}"
    assert "warnings_out" in payload, (
        f"envelope must carry top-level warnings_out (W591 reuses W590 channel); keys={sorted(payload.keys())!r}"
    )
    warnings = payload["warnings_out"]
    assert isinstance(warnings, list), f"warnings_out must be a list; got {type(warnings).__name__}"
    assert canonical_marker in warnings, (
        f"envelope warnings_out must surface the gatherer's structured marker; got {warnings!r}"
    )
