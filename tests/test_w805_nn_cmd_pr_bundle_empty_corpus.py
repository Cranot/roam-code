"""W805-NN -- empty-corpus Pattern-2 smoke test on ``roam pr-bundle validate``.

Fortieth-in-batch W805 sweep. Governance-critical command -- proof bundle
compose with multiple state-mutating subcommands (init / add / emit / validate).
This file probes the READ-ONLY ``validate`` subcommand ONLY (DO NOT trigger
state-mutating init / add / emit on real ``.roam/``).

Scope
-----

``cmd_pr_bundle`` (``src/roam/commands/cmd_pr_bundle.py``) is the canonical
proof-carrying PR bundle command (R26 -- Roam Review MVP differentiator).
Read-only subcommand surface:

* ``validate`` -- check bundle completeness, exit 5 on ``--strict`` when
  ``state != "complete"``. Additive ``--strict-resolved`` extends the gate
  to flag ghost (unresolved) affected_symbols. ``--ci`` implies BOTH.

State-mutating subcommands (NOT probed here): ``init`` / ``set`` /
``add affected`` / ``add risk`` / ``add test-required`` / ``add test-run`` /
``add non-goal`` / ``add context-cmd`` / ``add context-symbol`` /
``add context-file`` / ``add-approval`` / ``add-accepted-risk`` / ``emit``.

W978 first-hypothesis discipline
--------------------------------

Hypothesis: "governance-critical, multi-state command with many failure
paths; highly likely Pattern-2 silent-SAFE on missing bundle / empty diff /
no-context-files". Direct probes against four empty-state rows:

* **No bundle on disk** (no init ran). ``_require_bundle`` correctly emits
  ``state="not_initialized"``, ``partial_success=True``, ``verdict="no
  bundle on this branch -- run roam pr-bundle init --intent <text> first"``,
  exit 2. **No bug.** Pattern-2 explicit-absence is well-handled.

* **Empty bundle (hand-crafted at .roam/pr-bundles/<branch>.json)** + no
  flags. ``_validate_bundle`` correctly flags ``state="incomplete"`` +
  4 missing proofs (intent / affected_symbols / context_read.commands_run /
  roam_verdict). ``partial_success=True``. Exit 0 (advisory mode -- without
  ``--strict`` the gate doesn't fire). **No bug.**

* **Empty bundle + ``--strict``**. Correctly exits 5. **No bug.**

* **Empty bundle + ``--strict --strict-resolved``**. Correctly exits 5
  (the --strict gate fires before --strict-resolved adds its own clauses).
  **No bug.**

* **Empty bundle + ``--ci``**. Correctly implies ``--strict
  --strict-resolved``, exits 5. **No bug.**

* **Ghost-symbol bundle** (intent + affected_symbols with
  resolution_state='not_found' + commands_run + blast_radius=1 supplying
  has_signal) + ``--strict``: state="complete" exit 0 (W21.4 design --
  --strict-resolved is additive; the ghost is recorded but does not block
  the basic strict gate). + ``--strict --strict-resolved``: exit 5
  (unresolved_affected_symbols flagged as missing proof). **No bug.**

* **Tests-zero bundle** (intent + affected w/ blast=1 + commands_run + no
  tests_required + no tests_run) + ``--ci``: state="complete" exit 0.
  By design -- ``_validate_bundle`` only flags missing tests_run when
  ``tests_required`` is non-empty. An agent that declares zero required
  tests is explicit-absence in the producer (Pattern-2 compliant). **Not
  a bug; this is documented governance discipline.**

Conclusion: the validate-side governance gates are sound across all six
probed rows. The only real defect is a drive-by ASCII violation in the
verdict template.

REAL BUG pinned (drive-by ASCII)
--------------------------------

``cmd_pr_bundle.py`` lines 1694 / 1697 / 1701 / 1704 / 1705 / 1715 / 1716 /
1720 embed UTF-8 middle-dot (U+00B7 ``·`` -- ``\xc2\xb7``) in the verdict
template. Probed: a complete-state bundle's verdict reads
``"PR proof bundle complete (1 affected · 0 risks · 0/0 tests run)
(risk_level low)"`` -- contains TWO non-ASCII middle-dots.

This violates CLAUDE.md ``§Conventions``: *"No emojis, no colors, no
box-drawing in output - plain ASCII only for token efficiency."* Same
root-cause family as the W937 mojibake guard (high-bit characters
round-trip badly through cp1253 / Windows console encoding) and the
W805-G drive-by pin on ``cmd_pr_prep.py:174+177`` (em-dashes). LAW 6
cross-check: the verdict is the standalone field agents read; non-ASCII
bytes survive into log files, screen scrapes, and git grep -- where they
mojibake.

Fix template (one Edit): replace each ``·`` with ``-`` in
cmd_pr_bundle.py:1694+1697+1701+1704+1705+1715+1716+1720. Same fix shape
that landed for cmd_pr_prep em-dashes. **Pinned xfail-strict here.**

Sweep brief: W805-NN (Wave805-NN, fortieth-in-batch).
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process, invoke_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bundle_path(repo: Path, branch: str = "master") -> Path:
    """Return the .roam/pr-bundles/<branch>.json path under ``repo``."""
    return repo / ".roam" / "pr-bundles" / f"{branch}.json"


def _write_bundle(repo: Path, bundle: dict, branch: str = "master") -> Path:
    """Hand-write a bundle JSON to .roam/pr-bundles/<branch>.json.

    DO NOT call ``roam pr-bundle init`` (state-mutating subcommand). Instead,
    write the same canonical shape (``_empty_bundle`` from
    ``cmd_pr_bundle.py:239-272``) directly. This keeps the test isolated to
    the READ-ONLY ``validate`` path per W805-NN constraints.
    """
    p = _bundle_path(repo, branch)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(bundle), encoding="utf-8")
    return p


def _empty_bundle_shape(intent: str = "") -> dict:
    """Mirror of ``cmd_pr_bundle._empty_bundle`` -- single-sourced shape."""
    return {
        "schema": "roam-pr-bundle",
        "schema_version": 1,
        "created_at": "2026-05-17T00:00:00Z",
        "updated_at": "2026-05-17T00:00:00Z",
        "git": {},
        "intent": intent,
        "context_read": {
            "symbols_inspected": [],
            "files_inspected": [],
            "commands_run": [],
        },
        "affected_symbols": [],
        "risks": [],
        "tests_required": [],
        "tests_run": [],
        "known_non_goals": [],
        "roam_verdict": {
            "blast_radius_high": False,
            "complexity_increase": False,
            "fitness_violations": [],
            "conventions_violations": [],
        },
        "approvals": [],
        "accepted_risks": [],
    }


@pytest.fixture
def empty_corpus(tmp_path, monkeypatch):
    """Indexed git repo with one committed empty Python file.

    Zero-symbol corpus + clean tree. ``ensure_index`` is happy. validate
    probes the .roam/pr-bundles/<branch>.json file directly so the corpus
    shape doesn't matter for the validate path, but indexing keeps the
    test honest about the wider env.
    """
    repo = tmp_path / "empty-pr-bundle-repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (repo / "empty.py").write_text("", encoding="utf-8")
    git_init(repo)
    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo, "--force")
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


def _invoke_validate(runner: CliRunner, repo: Path, *args: str, json_mode: bool = True):
    """Invoke ``roam pr-bundle validate`` through the Click group.

    Threads ``--json`` (or ``--ci``) at the cli level so the global flags
    are honoured -- ``--ci`` implies ``--strict --strict-resolved`` per
    ``cmd_pr_bundle.py:3153-3157``.
    """
    cli_args: list[str] = []
    if json_mode:
        cli_args.append("--json")
    cli_args.append("pr-bundle")
    cli_args.append("validate")
    cli_args.extend(args)
    return invoke_cli(runner, cli_args, cwd=repo)


def _parse_envelope(result) -> dict:
    """Parse the first JSON object from stdout, tolerating trailing prose."""
    raw = (getattr(result, "stdout", None) or result.output).lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output}"
    decoder = _json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# Existence gate
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """``cmd_pr_bundle.pr_bundle_validate`` is importable + a Click command."""
    try:
        from roam.commands.cmd_pr_bundle import pr_bundle_validate
    except ImportError:
        pytest.skip("cmd_pr_bundle not importable -- skipping W805-NN smoke test")
    import click

    assert isinstance(pr_bundle_validate, click.Command), (
        f"pr_bundle_validate must be a Click command; got {type(pr_bundle_validate)!r}"
    )


# ---------------------------------------------------------------------------
# SMOKE -- always-on contracts (sealed today)
# ---------------------------------------------------------------------------


class TestPrBundleValidateEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_pr_bundle validate envelope."""

    def test_empty_corpus_no_crash_no_bundle(self, empty_corpus):
        """No bundle on disk -> validate exits 2 (guided error) + non-empty stdout."""
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        # _require_bundle's guided-error path exits 2 (cmd_pr_bundle.py:2140).
        assert result.exit_code == 2, f"expected exit 2 (no bundle), got {result.exit_code}; output:\n{result.output}"
        assert result.output.strip(), "stdout must NOT be empty in --json mode (Pattern-1C)"

    def test_empty_corpus_no_crash_empty_bundle(self, empty_corpus):
        """Empty bundle on disk -> validate exits 0 (advisory mode, no --strict)."""
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        assert result.exit_code == 0, f"expected exit 0 (advisory), got {result.exit_code}; output:\n{result.output}"

    def test_validate_no_bundle_envelope_has_verdict(self, empty_corpus):
        """No-bundle path emits ``command=pr-bundle`` + non-empty
        ``summary.verdict``."""
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        assert env["command"] == "pr-bundle"
        verdict = env.get("summary", {}).get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_validate_empty_envelope_has_verdict(self, empty_corpus):
        """Empty-bundle path emits ``command=pr-bundle-validate`` + non-empty
        ``summary.verdict``."""
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        assert env["command"] == "pr-bundle-validate"
        verdict = env.get("summary", {}).get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_validate_no_bundle_state_explicit(self, empty_corpus):
        """No-bundle path discloses ``state="not_initialized"`` (Pattern-2 explicit absence)."""
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        state = env.get("summary", {}).get("state")
        assert state == "not_initialized", f"no-bundle path should disclose state='not_initialized'; got {state!r}"

    def test_validate_empty_state_explicit(self, empty_corpus):
        """Empty-bundle path discloses ``state="incomplete"`` (Pattern-2)."""
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        state = env.get("summary", {}).get("state")
        assert state == "incomplete", f"empty-bundle should disclose state='incomplete'; got {state!r}"

    def test_validate_no_bundle_partial_success_set(self, empty_corpus):
        """No-bundle path sets ``partial_success=True``."""
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        assert env["summary"].get("partial_success") is True, (
            f"no-bundle path must set partial_success=True; got summary={env['summary']!r}"
        )

    def test_validate_empty_partial_success_set(self, empty_corpus):
        """Empty-bundle path sets ``partial_success=True`` (state=incomplete)."""
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        assert env["summary"].get("partial_success") is True, (
            f"empty-bundle path must set partial_success=True; got summary={env['summary']!r}"
        )

    def test_law6_verdict_standalone_no_bundle(self, empty_corpus):
        """LAW 6: the no-bundle verdict is single-line + self-describing.

        Carries the action the agent should take next (``run roam pr-bundle
        init``) so an agent reading only the verdict has all the info it needs.
        """
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        verdict = env["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict embeds newline: {verdict!r}"
        assert "pr-bundle init" in verdict, f"LAW 6: no-bundle verdict must name the next command; got {verdict!r}"

    def test_law6_verdict_standalone_empty(self, empty_corpus):
        """LAW 6: the empty-bundle verdict is single-line + names the gap.

        The verdict lists which proofs are missing so an agent reading only
        the verdict knows what to do next.
        """
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        verdict = env["summary"]["verdict"]
        assert "\n" not in verdict, f"verdict embeds newline: {verdict!r}"
        # "incomplete" + "missing:" tokens disclose the gate state.
        assert "incomplete" in verdict.lower(), f"LAW 6: empty-bundle verdict must say 'incomplete'; got {verdict!r}"

    def test_strict_empty_bundle_exits_5(self, empty_corpus):
        """``validate --strict`` on an empty bundle exits 5 (gate-fail)."""
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, "--strict", json_mode=True)
        assert result.exit_code == 5, (
            f"expected exit 5 from --strict on incomplete bundle; got {result.exit_code}\noutput:\n{result.output}"
        )

    def test_ci_implies_strict_resolved_disclosure(self, empty_corpus):
        """``--ci`` flag implies both ``--strict`` and ``--strict-resolved``.

        Per ``cmd_pr_bundle.py:3146-3157`` (W21.6 + W22.3): under --ci,
        default strict=True AND default strict_resolved=True. The envelope's
        ``summary.strict_resolved`` flag must reflect that the gate ran.
        """
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        cli_args = ["--ci", "--json", "pr-bundle", "validate"]
        result = invoke_cli(runner, cli_args, cwd=empty_corpus)
        # --ci on incomplete bundle should exit 5 (strict implied).
        assert result.exit_code == 5, (
            f"--ci should imply --strict + exit 5 on incomplete; got {result.exit_code}\noutput:\n{result.output}"
        )
        env = _parse_envelope(result)
        assert env["summary"].get("strict_resolved") is True, (
            f"--ci should imply --strict-resolved=True in summary; got "
            f"strict_resolved={env['summary'].get('strict_resolved')!r}"
        )

    def test_strict_resolved_zero_symbols_disclosure(self, empty_corpus):
        """Governance-axis: ``--strict-resolved`` on a bundle with ZERO
        affected_symbols still surfaces ``unresolved_affected_symbols_count=0``.

        Pattern-2 explicit-absence: a value of zero is itself a positive
        signal that no ghosts exist. The summary must always carry the key.
        """
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, "--strict-resolved", json_mode=True)
        env = _parse_envelope(result)
        summary = env["summary"]
        assert "unresolved_affected_symbols_count" in summary, (
            f"Pattern-2: summary must always carry unresolved_affected_symbols_count; got keys={sorted(summary.keys())}"
        )
        assert summary["unresolved_affected_symbols_count"] == 0, (
            f"zero affected_symbols should yield count 0; got {summary['unresolved_affected_symbols_count']!r}"
        )

    def test_strict_resolved_ghost_symbol_exits_5(self, empty_corpus):
        """Bundle with a ghost (unresolved) affected_symbol + ``--strict
        --strict-resolved`` exits 5 (W21.4)."""
        bundle = _empty_bundle_shape(intent="fix retry")
        # Make the basic --strict gate happy: intent + context_read + an
        # affected_symbol carrying blast_radius (for has_signal). Then
        # taint the affected_symbol with resolution_state='not_found'.
        bundle["context_read"]["commands_run"] = ["roam preflight ghost_sym"]
        bundle["affected_symbols"] = [
            {"name": "ghost_sym", "blast_radius": 1, "resolution_state": "not_found"},
        ]
        _write_bundle(empty_corpus, bundle)
        runner = CliRunner()
        result = _invoke_validate(
            runner,
            empty_corpus,
            "--strict",
            "--strict-resolved",
            json_mode=True,
        )
        assert result.exit_code == 5, (
            f"--strict --strict-resolved should exit 5 on ghost; got {result.exit_code}\noutput:\n{result.output}"
        )
        env = _parse_envelope(result)
        assert env["summary"]["unresolved_affected_symbols_count"] == 1, (
            f"ghost should yield unresolved count 1; got {env['summary']!r}"
        )

    def test_no_silent_bundle_complete_on_empty(self, empty_corpus):
        """Governance CRITICAL: empty bundle must NEVER read state="complete".

        Pattern-2 silent-SAFE prevention. The verdict must explicitly disclose
        incompleteness, NOT silently report a bundle ready to merge.
        """
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        state = env["summary"]["state"]
        verdict = env["summary"]["verdict"].lower()
        assert state != "complete", (
            f"Pattern-2 violation: empty bundle reported state='complete'; summary={env['summary']!r}"
        )
        assert "complete" not in verdict.split("(")[0] or "incomplete" in verdict, (
            f"empty-bundle verdict must not say 'complete' without 'incomplete' qualifier; got {verdict!r}"
        )

    def test_no_silent_ci_pass_on_empty(self, empty_corpus):
        """Governance CRITICAL: ``--ci`` MUST exit non-zero on empty bundle.

        If --ci silently exited 0 on an empty bundle, a CI gate would
        accept any PR whose author forgot to populate the bundle. The
        ``cmd_pr_bundle.py:3153-3157`` --ci -> --strict implication path
        is the load-bearing protection here.
        """
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        cli_args = ["--ci", "--json", "pr-bundle", "validate"]
        result = invoke_cli(runner, cli_args, cwd=empty_corpus)
        assert result.exit_code != 0, (
            f"governance CRITICAL: --ci on empty bundle exited {result.exit_code} "
            f"(expected non-zero); output:\n{result.output}"
        )
        assert result.exit_code == 5, f"expected exit 5 specifically (gate-fail); got {result.exit_code}"

    def test_empty_bundle_envelope_has_partial_success_key(self, empty_corpus):
        """Drift guard: auto-injected ``summary.partial_success`` key present."""
        _write_bundle(empty_corpus, _empty_bundle_shape())
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        assert "partial_success" in env.get("summary", {}), (
            "summary.partial_success key must be auto-injected; got summary keys "
            f"= {sorted(env.get('summary', {}).keys())}"
        )

    def test_no_bundle_agent_contract_next_command_executable(self, empty_corpus):
        """CONSTRAINT 12: the next-command field MUST be a copy-pasteable
        ``roam <subcommand>`` string."""
        runner = CliRunner()
        result = _invoke_validate(runner, empty_corpus, json_mode=True)
        env = _parse_envelope(result)
        contract = env.get("agent_contract") or {}
        next_cmds = contract.get("next_commands") or []
        assert next_cmds, f"no agent_contract.next_commands; got {contract!r}"
        first = next_cmds[0]
        assert first.startswith("roam pr-bundle init"), (
            f"CONSTRAINT 12: next_command must be literal 'roam ...'; got {first!r}"
        )


# ---------------------------------------------------------------------------
# DRIVE-BY: CLAUDE.md "§Conventions" violation -- cmd_pr_bundle.py:1694 /
# 1697 / 1701 / 1704 / 1705 / 1715 / 1716 / 1720 embed UTF-8 middle-dot
# (U+00B7 -- ``·`` -- \xc2\xb7) in the runtime verdict template. CLAUDE.md
# is explicit: "No emojis, no colors, no box-drawing in output - plain
# ASCII only for token efficiency." LAW 6 cross-check: the verdict is the
# standalone field agents read; non-ASCII bytes survive into log files,
# screen scrapes, and git grep -- where they mojibake.
#
# Same root cause family as W937's mojibake guard + the W805-G drive-by
# pin on cmd_pr_prep.py:174+177 (em-dashes). The fix template is one
# Edit: replace U+00B7 with " - " on each line.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-NN drive-by: cmd_pr_bundle.py:1694+1697+1701+1704+1705+1715+1716+1720 "
        "embed UTF-8 middle-dot (U+00B7 -- '\\xc2\\xb7') in the runtime "
        "verdict template, violating CLAUDE.md §Conventions ('plain "
        "ASCII only for token efficiency'). Same root cause family as W937's "
        "mojibake guard + W805-G's em-dash drive-by on cmd_pr_prep.py:174+177. "
        "Probe (complete bundle): "
        "verdict='PR proof bundle complete (1 affected · 0 risks · 0/0 "
        "tests run) (risk_level low)'. Fix template: replace each U+00B7 "
        "with ' - '. Separate fix wave per W805 accumulate-only constraint."
    ),
)
def test_complete_bundle_verdict_ascii_only(empty_corpus):
    """Verdict on a complete bundle must be plain ASCII (CLAUDE.md
    §Conventions).

    Builds a complete-state bundle (intent + commands_run + affected with
    blast_radius=1 supplying has_signal) so ``_validate_bundle`` returns
    state='complete' and the verdict hits the multi-segment template at
    cmd_pr_bundle.py:1702-1707 which embeds the U+00B7 separators.
    """
    bundle = _empty_bundle_shape(intent="ship the fix")
    bundle["context_read"]["commands_run"] = ["roam preflight foo"]
    bundle["affected_symbols"] = [
        {"name": "real_sym", "blast_radius": 1, "resolution_state": "resolved"},
    ]
    _write_bundle(empty_corpus, bundle)
    runner = CliRunner()
    result = _invoke_validate(runner, empty_corpus, json_mode=True)
    env = _parse_envelope(result)
    verdict = env["summary"]["verdict"]
    assert verdict.isascii(), (
        f"verdict carries non-ASCII bytes (CLAUDE.md §Conventions "
        f"violation); got {verdict!r} ({verdict.encode('utf-8')!r})"
    )
