"""W805-BBBB - cmd_simulate bogus-target Pattern-1-V-D + Pattern-2 (W805 sweep).

Eightieth-in-batch of the W805 sweep. Sibling pin to W805-EE (which covered
empty-corpus + no-op-transform + missing-symbol fabricated-health). THIS letter
attacks the orthogonal Pattern-1-V-D axis: ``apply_move``/``apply_merge``
*accept any string* as the target_file argument with NO existence / validity
check against the indexed corpus. The transform runs to completion on the
cloned graph - reports ``operation: move``, populated ``to_file``, real
metric deltas - and emits a verdict structurally indistinguishable from a
genuine fully-resolved move. The TARGET-side resolution state is never
disclosed.

W978 first-hypothesis re-run BEFORE writing any test
============================================================

Probed live behaviour twice (same result):

1. Indexed clean 2-symbol corpus (``a.py`` contains ``helper`` + ``caller``).
2. ``roam --json simulate move helper /totally/bogus/path/does_not_exist.py``
   - exit 0, envelope has ``summary.operation: "move"``,
     ``operation.to_file: "/totally/bogus/path/does_not_exist.py"``, real
     non-zero ``health_delta``, no ``partial_success``, no
     ``target_resolution`` field disclosing that the target file is not part
     of the corpus.
3. ``roam --json simulate merge bogus/sink.py a.py`` - apply_merge sets every
   ``a.py`` node's ``file_path`` to ``"bogus/sink.py"``. Emits success verdict
   with no disclosure that the target SINK file does not exist in the corpus.

Counterfactual-axis novelty (W978 confirmation)
-----------------------------------------------

Per CLAUDE.md, cmd_simulate operates on a CLONED graph + applies transforms,
NOT on the live index. Most W805 sweep coverage probed live-graph commands
(impact / preflight / context / understand / health / etc.). cmd_simulate
is structurally distinct: ``clone_graph(G)`` then mutates the clone in
memory. The bug surface is therefore different - resolution of the TARGET
side of a transform is uniquely a counterfactual-graph concern (live graph
commands have no concept of "target file that doesn't exist yet"). W805-EE
pinned the SOURCE-side resolution gap (symbol not found + no-op self-move +
fabricated-health-on-empty); W805-BBBB pins the TARGET-side resolution gap.

Pattern-1-V-D fit
-----------------

CLAUDE.md Pattern-1, variant D: "Silent success on degraded resolution.
Command resolves a target partially (symbol -> file -> unresolved fallback,
fuzzy-match, etc.), proceeds to act on the degraded resolution, and emits a
success verdict indistinguishable from a fully-resolved success."

cmd_simulate does not even ATTEMPT to resolve the target_file argument. The
TARGET resolution state is silently ``unresolved`` (or worse, ``invented``)
on every invocation. Fix template: ``apply_move`` / ``apply_merge`` should
disclose ``target_resolution: "existing_file" | "new_file" | "unindexed"`` +
set ``partial_success=True`` when the target is unindexed.

W907 verify-cycle check
=======================

grep -i 'avoid.*cycle|circular import|kept local' on
src/roam/commands/cmd_simulate.py + src/roam/graph/simulate.py = NO MATCHES.
No defensive lazy-import comments present in cmd_simulate's substrate -
verify-cycle clean.

DO NOT FIX this wave - accumulate xfail-strict pins only.

Run isolation:
    python -m pytest tests/test_w805_bbbb_cmd_simulate_empty_corpus.py -x -n 0

Regression baseline:
    python -m pytest tests/test_simulate.py -x -n 0
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_init_with_baseline(proj: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=proj,
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=proj,
        check=True,
    )


def _invoke_simulate(runner: CliRunner, cwd: Path, *extra, json_mode: bool = True):
    """Invoke ``roam simulate <subcommand> ...`` via the Click group."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("simulate")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_envelope(result) -> dict:
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Indexed project with a single empty .py file - 0 symbols, 0 edges."""
    proj = tmp_path / "empty_sim_corpus_bbbb"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    _git_init_with_baseline(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def clean_corpus(tmp_path, monkeypatch):
    """Indexed project with 2 symbols + 1 edge - regression baseline."""
    proj = tmp_path / "clean_sim_corpus_bbbb"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "a.py").write_text(
        "def helper():\n    return 1\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )
    _git_init_with_baseline(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Sealed-today contracts (always-on smoke)
# ---------------------------------------------------------------------------


class TestSimulateBogusTargetSealed:
    """Properties already satisfied by cmd_simulate today (regression-only)."""

    def test_empty_corpus_move_disclosure_no_crash(self, cli_runner, empty_corpus):
        """Pattern-1-V-D smoke: empty corpus + move ghost-> ghost.py exits 0 with JSON envelope."""
        result = _invoke_simulate(cli_runner, empty_corpus, "move", "ghost", "ghost.py", json_mode=True)
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        assert env["command"] == "simulate"
        # Verdict must not be empty even on full failure.
        verdict = env.get("summary", {}).get("verdict") or ""
        assert verdict.strip(), f"empty verdict on empty corpus: {env!r}"

    def test_bogus_symbol_resolution_state_named_in_verdict(self, cli_runner, clean_corpus):
        """Resolution failure for SOURCE symbol explicitly names it in the verdict.

        Lock in the half that cmd_simulate already gets right - the SOURCE-side
        symbol miss IS named in the verdict text ('symbol not found: <name>').
        This is the floor pin; the gap is the TARGET side (see xfail below).
        """
        result = _invoke_simulate(
            cli_runner,
            clean_corpus,
            "move",
            "nonexistent_sym",
            "newfile.py",
            json_mode=True,
        )
        env = _parse_envelope(result)
        verdict = env["summary"]["verdict"].lower()
        assert "not found" in verdict or "nonexistent_sym" in verdict, (
            f"SOURCE-side resolution failure must be named in verdict; got {verdict!r}"
        )

    def test_clean_corpus_real_delta_emits_operation(self, cli_runner, clean_corpus):
        """Happy-path regression: real move on populated corpus has full envelope shape."""
        result = _invoke_simulate(cli_runner, clean_corpus, "move", "helper", "newfile.py", json_mode=True)
        assert result.exit_code == 0, result.output
        env = _parse_envelope(result)
        op = env["operation"]
        assert op.get("operation") == "move"
        assert op.get("symbol") == "helper"
        assert op.get("to_file") == "newfile.py"
        assert env["summary"]["verdict"].strip()


# ---------------------------------------------------------------------------
# Pattern-1-V-D BUG PIN #1 - HIGH: bogus target_file silently "succeeds"
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-BBBB REAL BUG (HIGH, Pattern-1-V-D): graph/simulate.py L177-189 "
        "apply_move blindly assigns data['file_path'] = target_file with NO "
        "validity check against the indexed corpus. Targeting "
        "'/totally/bogus/path/does_not_exist.py' produces a verdict + "
        "operation block STRUCTURALLY INDISTINGUISHABLE from a real move "
        "to an existing project file. Agents reading the envelope cannot "
        "tell from summary.verdict / summary.operation / "
        "operation.to_file alone that the destination does not exist. "
        "Closed enum needed: target_resolution in {existing_file, "
        "new_file_in_project, unindexed, outside_corpus}. Pattern-1-V-D "
        "fit: 'Silent success on degraded resolution'. Separate fix wave."
    ),
)
def test_bogus_target_file_state_disclosure(cli_runner, clean_corpus):
    """Pin: target_file outside corpus MUST be disclosed via target_resolution field.

    Today: apply_move accepts any string as target_file. Envelope reports
    operation=move + populated to_file + real health_delta - no signal that
    the destination is /totally/bogus/path/does_not_exist.py.
    """
    bogus_target = "/totally/bogus/path/does_not_exist.py"
    result = _invoke_simulate(cli_runner, clean_corpus, "move", "helper", bogus_target, json_mode=True)
    env = _parse_envelope(result)
    # Either operation.target_resolution OR summary.target_resolution OR
    # summary.partial_success=True signalling target-side degradation must be
    # present.
    summary = env["summary"]
    op = env.get("operation", {})
    target_res = op.get("target_resolution") or summary.get("target_resolution")
    partial = summary.get("partial_success")
    assert target_res in {"unindexed", "outside_corpus", "new_file_in_project"} or partial is True, (
        f"bogus target_file '{bogus_target}' triggered no target_resolution "
        f"disclosure AND no partial_success flag. Operation block: {op!r}; "
        f"summary: {summary!r}. Pattern-1-V-D: silent success on degraded "
        f"target resolution."
    )


# ---------------------------------------------------------------------------
# Pattern-1-V-D BUG PIN #2 - HIGH: merge with bogus SINK file silently succeeds
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-BBBB REAL BUG (HIGH, Pattern-1-V-D): cmd_simulate.py L318-322 "
        "checks that file_b (source side) has symbols but performs NO "
        "validity check on file_a (target sink). graph/simulate.py L222-236 "
        "apply_merge iterates and reassigns file_path = file_a for every "
        "file_b node, even when file_a does not exist anywhere in the "
        "indexed corpus. Envelope reports merge succeeded with "
        "operation.target_file = '<bogus>' as if it were real. Same "
        "target-side disclosure gap as PIN #1, distinct subcommand. Fix "
        "template: apply_merge needs a file_a existence check + "
        "target_resolution disclosure. Separate fix wave."
    ),
)
def test_bogus_merge_target_state_disclosure(cli_runner, clean_corpus):
    """Pin: merge with bogus target file must disclose target_resolution."""
    bogus_sink = "totally/bogus/sink.py"
    # file_a (sink) is bogus; file_b (a.py) has real symbols.
    result = _invoke_simulate(cli_runner, clean_corpus, "merge", bogus_sink, "a.py", json_mode=True)
    env = _parse_envelope(result)
    summary = env["summary"]
    op = env.get("operation", {})
    target_res = op.get("target_resolution") or summary.get("target_resolution")
    partial = summary.get("partial_success")
    assert target_res in {"unindexed", "outside_corpus", "new_file_in_project"} or partial is True, (
        f"merge into bogus sink '{bogus_sink}' emitted success envelope "
        f"with no target-resolution disclosure. operation={op!r}; "
        f"summary={summary!r}"
    )


# ---------------------------------------------------------------------------
# Pattern-2 BUG PIN #3 - HIGH: error envelope omits next_command / hint
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-BBBB REAL BUG (HIGH, Pattern-2 + LAW 2): cmd_simulate.py "
        "L60-83 error envelope emits {verdict: 'symbol not found: <name>'} "
        "with no hint, no next_command, no agent_contract. CLAUDE.md "
        "canonical failure envelope shape requires both: 'hint' (imperative: "
        "what to do next) and 'next_command' (copy-paste-executable). "
        "Agents reading 'symbol not found: foo' cannot recover - they "
        "must guess that the recovery is 'roam search foo' or "
        "'roam index --force'. CONSTRAINT 12: next_command MUST be a "
        "literal roam <subcommand> string. Separate fix wave."
    ),
)
def test_error_envelope_has_recovery_hint(cli_runner, clean_corpus):
    """Pin: error envelope MUST carry hint or next_command for agent recovery."""
    result = _invoke_simulate(
        cli_runner,
        clean_corpus,
        "move",
        "totally_made_up_symbol_name",
        "newfile.py",
        json_mode=True,
    )
    env = _parse_envelope(result)
    hint = env.get("hint") or env.get("summary", {}).get("hint")
    next_cmd = env.get("next_command") or env.get("summary", {}).get("next_command")
    agent_contract = env.get("agent_contract") or {}
    next_cmds = agent_contract.get("next_commands") or []
    assert hint or next_cmd or next_cmds, (
        f"error envelope has no recovery affordance for agent. "
        f"hint={hint!r}, next_command={next_cmd!r}, "
        f"agent_contract.next_commands={next_cmds!r}. "
        f"Envelope keys: {sorted(env.keys())}"
    )


# ---------------------------------------------------------------------------
# Pattern-1-V-D BUG PIN #4 - MEDIUM: no resolution field on success envelope
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-BBBB REAL BUG (MEDIUM, Pattern-1-V-D shape gap): "
        "cmd_simulate.py L122-141 success envelope has NO 'resolution' "
        "field disclosing how the SOURCE symbol was resolved (exact match "
        "vs fuzzy match vs file-path match vs partial file match - "
        "graph/simulate.py L262-286 resolve_target tries all four). CLAUDE.md "
        "Pattern-1-V-D fix template: 'disclose the resolution state "
        "explicitly via a resolution field on the envelope (closed enum: "
        "symbol / file / unresolved / fuzzy / etc.)'. cmd_simulate silently "
        "picks the first node from a partial-file-path match (L283) without "
        "telling the agent it fell back from exact symbol -> file -> partial. "
        "Separate fix wave."
    ),
)
def test_no_op_transform_distinct_verdict(cli_runner, clean_corpus):
    """Pin: success envelope must disclose how the source was resolved.

    Today: cmd_simulate uses 4 resolution strategies (symbol exact, file
    exact, file partial) and silently picks the first hit without disclosing
    which path resolved. Fix template adds summary.resolution to the
    enum {symbol, file_exact, file_partial, fuzzy_symbol}.
    """
    result = _invoke_simulate(cli_runner, clean_corpus, "move", "helper", "newfile.py", json_mode=True)
    env = _parse_envelope(result)
    summary = env["summary"]
    op = env.get("operation", {})
    resolution = summary.get("resolution") or op.get("resolution") or op.get("source_resolution")
    assert resolution in {
        "symbol",
        "symbol_exact",
        "file_exact",
        "file_partial",
        "fuzzy_symbol",
    }, (
        f"success envelope must disclose source resolution path; got "
        f"resolution={resolution!r}. summary={summary!r}; op={op!r}"
    )
