"""W590 — ``_gather_lease_policy_decisions`` discloses project-root lookup failures.

W447 added an info marker when ``.roam/leases/`` was missing in modes
that *expect* leases (``migration`` / ``autonomous_pr``). W590 closes
the SIBLING silent-fallback gap one level up: ``find_project_root()``
returning None — or raising — used to make ``_gather_lease_policy_decisions``
emit zero rows with no signal, indistinguishable from "this PR has no
leases."

The gatherer now appends a structured warning string to the caller's
``warnings`` bucket on each silent-fallback path:

  * ``find_project_root`` returns None → ``"leases: project_root_not_found — ..."``
  * ``find_project_root`` raises        → ``"leases: project_root_lookup_failed — ..."``

Each warning prefix is a deliberate string anchor (``leases:`` + closed-
form kind) so an operator can grep the producer-warnings bucket and
distinguish a "not a roam project" run from a "broken lease file" run.

The propagation chain BEFORE W590: gatherer → ``pre_warnings`` →
``warnings`` → stderr only (never the envelope). W590 plumbs a new
``producer_warnings_out`` bucket through ``_collect_change_evidence``
into the JSON envelope's top-level ``warnings_out`` field so CI gates
and audit dashboards can read the signal without tailing stderr.
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
# Unit-level tests on _gather_lease_policy_decisions
# ---------------------------------------------------------------------------


def test_lease_policy_warns_when_find_project_root_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``find_project_root`` returns None, the gatherer must emit a
    structured ``leases: project_root_not_found`` warning rather than
    silently returning an empty list.

    Pre-W590 behaviour: warnings list stays empty, rows stay empty,
    auditor cannot tell "no roam project" apart from "no leases".
    """
    # Monkeypatch the bound name the gatherer imports via
    # ``from roam.db.connection import find_project_root``. The import
    # happens inside the function so we patch the source module.
    import roam.db.connection as conn_mod
    from roam.commands import cmd_pr_replay

    monkeypatch.setattr(conn_mod, "find_project_root", lambda: None)

    warnings: list[str] = []
    rows = cmd_pr_replay._gather_lease_policy_decisions(warnings)

    assert rows == [], f"expected zero rows on missing project_root; got {rows!r}"
    assert len(warnings) == 1, f"expected one project_root_not_found warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("leases:"), msg
    assert "project_root_not_found" in msg, msg
    # The detail explains WHY (no .git ancestor / not a roam project).
    assert "find_project_root" in msg, msg


def test_lease_policy_warns_when_find_project_root_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``find_project_root`` raises, the gatherer must emit a
    structured ``leases: project_root_lookup_failed`` warning naming
    the exception class.
    """
    import roam.db.connection as conn_mod
    from roam.commands import cmd_pr_replay

    class _SyntheticLookupError(RuntimeError):
        pass

    def _boom() -> Path:
        raise _SyntheticLookupError("synthetic find_project_root failure")

    monkeypatch.setattr(conn_mod, "find_project_root", _boom)

    warnings: list[str] = []
    rows = cmd_pr_replay._gather_lease_policy_decisions(warnings)

    assert rows == [], f"expected zero rows on find_project_root raise; got {rows!r}"
    assert len(warnings) == 1, f"expected one project_root_lookup_failed warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("leases:"), msg
    assert "project_root_lookup_failed" in msg, msg
    assert "_SyntheticLookupError" in msg, msg
    assert "synthetic find_project_root failure" in msg, msg


def test_lease_policy_silent_when_find_project_root_resolves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sanity check the W590 plumbing only fires on the missing/raising
    paths — a successful lookup (with no ``.roam/leases/`` dir on disk
    and a non-migration mode) should still be silent.
    """
    import roam.db.connection as conn_mod
    from roam.commands import cmd_pr_replay

    # Build a tmp project with .git but no .roam/leases dir; default mode
    # → W447 marker stays quiet on safe_edit / read_only / unset.
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(conn_mod, "find_project_root", lambda: tmp_path)

    warnings: list[str] = []
    rows = cmd_pr_replay._gather_lease_policy_decisions(warnings)
    assert rows == [], f"expected zero rows on bare project; got {rows!r}"
    # Either zero warnings (default mode) or only the W447 mode-specific
    # marker — but NOT a project_root warning.
    for w in warnings:
        assert "project_root_not_found" not in w, w
        assert "project_root_lookup_failed" not in w, w


# ---------------------------------------------------------------------------
# Integration test — envelope surfaces W590 warning in warnings_out
# ---------------------------------------------------------------------------


def test_pr_replay_envelope_surfaces_lease_policy_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a warning emitted by ``_gather_lease_policy_decisions``
    must surface on the JSON envelope's top-level ``warnings_out`` field.

    Verifies the W590 propagation chain:
        gatherer warnings list (``pre_warnings``)
        → ``_collect_change_evidence``'s ``producer_warnings_out`` bucket
        → ``envelope_producer_warnings`` in ``pr_replay_cmd``
        → ``extra_payload["warnings_out"]``
        → ``json_envelope`` output.

    We can't monkeypatch ``find_project_root`` directly inside the full
    pr-replay invocation because ``ensure_index`` calls it BEFORE the
    gatherer runs and would crash on the None return. Instead we
    monkeypatch the gatherer itself to inject the canonical W590 marker
    string — the propagation chain is what's under test here; the
    gatherer's own emission is verified by the three unit-level tests
    above.
    """
    from roam.cli import cli
    from roam.commands import cmd_pr_replay

    # Build a tiny git repo so pr-replay's git rev-list call succeeds.
    proj = tmp_path / "tinyproj"
    proj.mkdir()
    (proj / "README.md").write_text("x\n")
    git_init(proj)

    canonical_marker = (
        "leases: project_root_not_found — find_project_root returned None "
        "(synthetic-injection from W590 propagation test)"
    )

    def _fake_gather_lease_policy_decisions(warnings: list[str]) -> list[dict]:
        warnings.append(canonical_marker)
        return []

    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_lease_policy_decisions",
        _fake_gather_lease_policy_decisions,
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

    # Envelope JSON should contain the warning. ``ensure_index`` chatter
    # and the optional ``Wrote ChangeEvidence JSON to ...`` line surround
    # the envelope; locate the LAST top-level JSON object via raw_decode
    # walking back from the final ``{``. Then deserialize that slice.
    body = result.output
    # Find the LAST occurrence of an opening brace that starts a parseable
    # JSON object. The pr-replay envelope is multi-line indented JSON so
    # rfind("{") returns the deepest brace; instead we step through each
    # ``{`` position and try raw_decode until one succeeds and consumes
    # to EOF (or near EOF, allowing trailing whitespace).
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
        f"envelope must carry top-level warnings_out (W590); keys={sorted(payload.keys())!r}"
    )
    warnings = payload["warnings_out"]
    assert isinstance(warnings, list), f"warnings_out must be a list; got {type(warnings).__name__}"
    assert canonical_marker in warnings, (
        f"envelope warnings_out must surface the gatherer's structured marker; got {warnings!r}"
    )
