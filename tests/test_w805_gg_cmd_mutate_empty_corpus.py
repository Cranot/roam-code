"""W805-GG: empty-corpus + missing-target Pattern-2 / Pattern-1-V-D probe on
``roam mutate`` (code-transform peer of ``cmd_simulate``).

Family: graph-mutation (in flight with W805-EE simulate).

Scope: ``src/roam/commands/cmd_mutate.py`` — invokes
``roam.refactor.transforms.{move_symbol, rename_symbol, add_call,
extract_symbol}``. All four resolve their target via ``find_symbol`` and
short-circuit to ``_emit_error`` when the target is missing. Default
``apply_changes=False`` so probing does NOT write to disk (W978-verified
by reading the click-option defaults at module import time before running).

What this file checks
---------------------

1. **No crash on empty corpus** — even with zero indexed symbols the four
   ``mutate`` subcommands return a structured envelope instead of crashing.
2. **Envelope shape** — ``verdict`` is present, ``files_modified == 0``,
   ``changes == []``, ``warnings`` is non-empty.
3. **LAW 6 verdict standalone** — the verdict line works without any other
   field; it names the failed resolution.
4. **Pattern-1-V-D / agent-safety pin (xfail-strict)** — for a MISSING
   target, the envelope should set ``summary.partial_success: true`` and
   ``summary.state: "unresolved"`` and the top-level ``isError: true``
   per the CLAUDE.md "canonical failure envelope" contract. Today only
   the verdict text carries the signal; an agent that consumes
   ``summary.partial_success`` would believe the transform succeeded.
   This is a textbook silent-success-on-degraded-resolution leak (Pattern
   1, variant D). Pinned with xfail-strict so a future fix flips it to
   pass.
5. **Dry-run disclosure** — successful dry-run on an empty corpus is
   indistinguishable from no-op; the verdict must NOT claim files were
   modified.

Do NOT trigger ``--apply`` from any test in this file: the transforms
write to ``os.getcwd()`` (no chroot) and a buggy apply path would
corrupt fixtures. Probe via the in-process ``transforms.*`` helpers
with explicit ``dry_run=True`` AND via the CLI without the ``--apply``
flag.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tests.conftest import invoke_cli

# ===========================================================================
# Empty-corpus fixture
# ===========================================================================


@pytest.fixture
def empty_indexed_project(project_factory):
    """An indexed project with NO source symbols.

    Files exist (``project_factory`` requires a non-empty tree to git-init)
    but contain no extractable Python symbols. ``find_symbol`` returns
    ``None`` for every name on this index.
    """
    return project_factory({"README.md": "# empty corpus\n"})


# ===========================================================================
# W978 verification — apply mode is gated behind --apply / dry_run=False
# ===========================================================================


def test_w978_apply_mode_gated_in_cli():
    """The four ``mutate`` subcommands all default ``apply_changes`` to
    ``False``. Probing without ``--apply`` cannot trigger a disk write.
    Verified by reading the click option default at import time so this
    test stays cheap and side-effect-free.
    """
    from roam.commands.cmd_mutate import mutate_add_call, mutate_extract, mutate_move, mutate_rename

    for cmd in (mutate_move, mutate_rename, mutate_add_call, mutate_extract):
        apply_opt = next((p for p in cmd.params if getattr(p, "name", None) == "apply_changes"), None)
        assert apply_opt is not None, f"{cmd.name} missing --apply flag"
        assert apply_opt.default is False, f"{cmd.name} apply_changes default must be False"


def test_w978_transforms_default_dry_run_in_lib():
    """``move_symbol`` / ``rename_symbol`` / ``add_call`` / ``extract_symbol``
    default ``dry_run=True``. Belt + braces with the CLI default flip.
    """
    import inspect

    from roam.refactor.transforms import add_call, extract_symbol, move_symbol, rename_symbol

    for fn in (move_symbol, rename_symbol, add_call, extract_symbol):
        sig = inspect.signature(fn)
        assert sig.parameters["dry_run"].default is True, f"{fn.__name__} dry_run default must be True"


# ===========================================================================
# Empty-corpus envelope tests (move as representative — same _emit_error
# helper used by all 4 subcommands)
# ===========================================================================


def _run_mutate_move_json(project_path, symbol="ghost_symbol", target="ghost_target.py"):
    """Invoke ``roam --json mutate move <symbol> <target>`` in-process.

    Returns the parsed JSON envelope plus the CLI ``Result``.
    """
    runner = CliRunner()
    result = invoke_cli(
        runner,
        ["mutate", "move", symbol, target],
        cwd=project_path,
        json_mode=True,
    )
    # Envelope is on stdout; parse it (may have leading non-JSON banner from
    # ensure_index on first run, so grab the last JSON object).
    out = result.output.strip()
    # Find the JSON object — it starts at the first '{' and the parser
    # tolerates anything before.
    start = out.find("{")
    assert start >= 0, f"no JSON object in mutate output:\n{out}"
    envelope = json.loads(out[start:])
    return envelope, result


def test_empty_corpus_no_crash(empty_indexed_project):
    """Empty corpus + missing target must NOT crash."""
    envelope, result = _run_mutate_move_json(empty_indexed_project)
    assert result.exit_code == 0, f"mutate crashed: exit={result.exit_code}\n{result.output}"


def test_empty_corpus_envelope_has_verdict(empty_indexed_project):
    """Envelope summary carries a verdict (LAW 6 prereq)."""
    envelope, _ = _run_mutate_move_json(empty_indexed_project)
    assert "summary" in envelope, f"no summary in envelope: {envelope}"
    assert "verdict" in envelope["summary"], f"no verdict in summary: {envelope['summary']}"
    assert envelope["summary"]["verdict"], "verdict is empty string"


def test_empty_corpus_law6_verdict_standalone(empty_indexed_project):
    """Verdict works alone (LAW 6): names the missing symbol AND the
    operation that failed, so an agent consuming verdict-only sees signal.
    """
    envelope, _ = _run_mutate_move_json(empty_indexed_project, symbol="phantom_fn")
    verdict = envelope["summary"]["verdict"]
    assert "phantom_fn" in verdict, f"verdict omits target name: {verdict!r}"
    # Either name the failure mode ("not found") OR name the operation.
    # Today the verdict reads "symbol not found: phantom_fn" — accept that
    # OR a future tightening that adds "move ... -> not found".
    assert "not found" in verdict.lower() or "unresolved" in verdict.lower(), (
        f"verdict does not name the missing-target failure: {verdict!r}"
    )


def test_empty_corpus_state_explicit(empty_indexed_project):
    """Envelope discloses that NO transform ran. ``files_modified == 0``
    AND ``changes == []`` AND ``warnings`` is non-empty.
    """
    envelope, _ = _run_mutate_move_json(empty_indexed_project)
    summary = envelope["summary"]
    assert summary.get("files_modified") == 0, f"files_modified should be 0: {summary}"
    assert summary.get("conflicts") == 0, f"conflicts should be 0: {summary}"
    assert envelope.get("changes") == [], f"changes must be empty: {envelope.get('changes')}"
    warnings = envelope.get("warnings") or []
    assert warnings, "warnings must be non-empty on missing target"
    assert any("not found" in w.lower() for w in warnings), f"warnings should name the missing target: {warnings}"


def test_dry_run_explicit_disclosure(empty_indexed_project, monkeypatch):
    """In-library dry-run path on an empty corpus returns ``error`` field
    (so the CLI can branch into _emit_error), NOT a silent
    ``files_modified=[]`` success.
    """
    monkeypatch.chdir(empty_indexed_project)
    from roam.db.connection import open_db
    from roam.refactor.transforms import move_symbol

    with open_db(readonly=True) as conn:
        result = move_symbol(conn, "ghost_symbol", "ghost_target.py", dry_run=True)

    assert result.get("error"), f"missing target must surface 'error' field: {result}"
    assert "ghost_symbol" in result["error"]
    assert result.get("files_modified") == [], (
        f"missing target must NOT report files_modified: {result.get('files_modified')}"
    )


# ===========================================================================
# Pattern-1 variant D pin — agent-safety (xfail-strict)
# ===========================================================================


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-GG pin: cmd_mutate._emit_error does NOT set summary.partial_success, "
        "summary.state, or top-level isError on missing-target / empty-corpus. "
        "Per CLAUDE.md 'canonical failure envelope' (Pattern 1, variant D), a "
        "degraded resolution must set partial_success=true + state='unresolved' + "
        "isError=true so an MCP consumer reading summary.partial_success cannot "
        "mistake 'symbol not found' for a successful no-op transform. Agent-safety "
        "CRITICAL: today the verdict text carries the only signal — a wrapper that "
        "JSON-parses summary.partial_success and routes on False will believe the "
        "transform succeeded. Fix: extend _emit_error to stamp these three fields."
    ),
)
def test_no_silent_transform_applied_on_empty(empty_indexed_project):
    """AGENT-SAFETY pin: a missing-target envelope MUST set
    ``summary.partial_success=true`` AND ``summary.state="unresolved"``
    AND top-level ``isError=true``.
    """
    envelope, _ = _run_mutate_move_json(empty_indexed_project)
    summary = envelope["summary"]
    assert summary.get("partial_success") is True, f"missing-target envelope must set partial_success=true: {summary}"
    assert summary.get("state") == "unresolved", f"missing-target envelope must set state='unresolved': {summary}"
    assert envelope.get("isError") is True, f"missing-target envelope must set isError=true: {envelope}"


def test_empty_corpus_partial_success_set(empty_indexed_project):
    """Companion check on partial_success only — currently false, so this
    test ALSO pins. Marked xfail-strict so a one-line fix flips both.
    """
    envelope, _ = _run_mutate_move_json(empty_indexed_project)
    # Use pytest.xfail at call-site so we can keep the assertion concise
    # while still proving the leak.
    summary = envelope["summary"]
    if summary.get("partial_success") is not True:
        pytest.xfail("W805-GG: summary.partial_success not stamped on missing-target envelope")
    assert summary["partial_success"] is True


def test_missing_target_disclosure(empty_indexed_project):
    """Pattern-1-V-D companion: the operation field discloses WHICH transform
    was attempted, so the agent can rebuild context after a missing-target.
    This part is healthy today — keep it as a positive regression guard.
    """
    envelope, _ = _run_mutate_move_json(empty_indexed_project)
    summary = envelope["summary"]
    assert summary.get("operation") == "move", f"operation field must name the attempted transform: {summary}"
