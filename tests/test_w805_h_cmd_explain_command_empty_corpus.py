"""W805-H - empty-corpus smoke for ``roam explain-command`` (W805 Pattern 2 sweep).

Eighth-in-batch of the W805 sweep. Prior cohort:

- A (cmd_owner)        REAL BUG (silent ``"top owner: ?"``)
- B (cmd_minimap)      REAL BUG (silent ``"minimap rendered (148 chars)"``)
- C (cmd_oracle)       REAL BUG (verdict/metadata mismatch)
- D (cmd_workflow)     NO REAL BUG (inspector, milder surface-uniformity gap)
- E (cmd_path_coverage) NO REAL BUG (W807-hardened)
- F (cmd_for_bug_fix)  in flight
- G (cmd_pr_prep)      in flight
- H (cmd_explain_command, this wave)

W978 first-hypothesis re-run BEFORE writing any test
============================================================
``cmd_explain_command`` is **static-metadata-only**. It builds its
view via ``_build_surface()`` (``src/roam/commands/cmd_surface.py:57``)
which reads the CLI registry (``cli._COMMANDS``, ``cli._CATEGORIES``)
plus the AST-derived MCP-tool inventory. It does NOT call
``ensure_index()``, does NOT call ``open_db()``, and does NOT depend on
the workspace corpus at all. The empty-corpus probe is therefore
analogous to the W805-D ``cmd_workflow`` outcome: the empty-corpus
input is the wrong axis for catching a Pattern-2 silent-SAFE bug here
because the verdict on a 0-symbol corpus is byte-identical to the
verdict on a 1M-symbol corpus (both read the same in-memory registry).

Probe shifted from "is empty-corpus silent-SAFE?" -> "does the
canonical happy-path envelope satisfy LAW 4 / LAW 6 / W805 cohort
surface-uniformity?". Three milder gaps surfaced:

1. **Happy-path verdict is bare ``"OK"``** (``cmd_explain_command.py:282``
   and again at L308 for text). LAW 6 says the verdict must work
   without any other field. ``"OK"`` carries no command identifier
   and no concrete-noun anchor; it is the canonical "summary mode"
   activator listed in ``tests/test_law4_lint.py:278`` blocklist
   (alongside ``"completed"``, ``"see details"``, ``"n/a"``).

2. **Happy-path agent_contract.facts is ``["OK"]``** — derived
   from ``summary.verdict`` by ``_derive_agent_contract`` in
   ``src/roam/output/formatter.py:759``. Bare ``"OK"`` is in the
   LAW 4 lint blocklist explicitly. Concrete-noun-anchored alternative:
   ``"explain-command surface metadata read"`` (terminal ``read`` in
   ``_CONCRETE_NOUN_ANCHORS``) or ``"<command> metadata loaded"``.

3. **No closed-enum ``state`` field** on the happy path. Cohort
   uniformity: the unknown-command branch ALREADY emits
   ``state="unknown_command"`` (W1083-followup helper). The happy
   path should mirror with ``state="ok"`` / ``state="known_command"``
   so a consumer can switch on a single closed-enum value rather than
   parsing the verdict string.

The unknown-command branch (W1074 closest-match + W1083-followup
``structured_unknown_filter``) is ALREADY hardened — it emits
``state="unknown_command"``, ``partial_success: True``,
``verdict: "unknown command 'X' ..."``, and the ``did_you_mean``
suggestions. The closest-match regression is preserved here as a
W1074 drift guard.

DEGRADED path (``importlib.import_module`` raises) is not naturally
triggerable from the surface registry — it would require an entry
in ``_COMMANDS`` whose module is broken. Out of scope for this wave;
documented as a follow-up axis if a deliberate-corruption fixture
is built.

W805-H verdict
============================================================
NO REAL BUG (silent-SAFE on empty corpus does not apply: command is
static-metadata-only, same as W805-D ``cmd_workflow``). Three milder
LAW 4 / LAW 6 / cohort-uniformity gaps pinned via xfail-strict for
the separate fix wave.

Run isolation:
    python -m pytest tests/test_w805_h_cmd_explain_command_empty_corpus.py -x -n 0

Regression baseline:
    python -m pytest tests/test_w1074_workflow_explain_unknown.py -x -n 0
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers - invoke explain-command via the Click group so the top-level
# --json flag is honoured by ctx.obj.
# ---------------------------------------------------------------------------


def _invoke_explain(runner: CliRunner, cwd, *args: str, json_mode: bool = True):
    """Invoke ``roam explain-command`` through the group."""
    from roam.cli import cli

    cli_args: list[str] = []
    if json_mode:
        cli_args.append("--json")
    cli_args.append("explain-command")
    cli_args.extend(args)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, cli_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_envelope(result) -> dict:
    """Parse the first JSON object from stdout (tolerant of trailing prose)."""
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
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "empty.py").write_text("")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def populated_corpus(tmp_path, monkeypatch):
    """Indexed project with real symbols - regression / uniformity baseline."""
    proj = tmp_path / "populated_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "def main():\n    helper()\n\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


# ---------------------------------------------------------------------------
# Sealed-today contracts (always-on smoke)
# ---------------------------------------------------------------------------


class TestExplainCommandEmptyCorpusSealed:
    """Properties already satisfied by the current cmd_explain_command envelope."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus):
        """``roam explain-command surface`` on empty corpus exits 0."""
        result = _invoke_explain(cli_runner, empty_corpus, "surface", json_mode=True)
        assert result.exit_code == 0, f"expected exit 0, got {result.exit_code}; output:\n{result.output}"
        # Pattern-1C: stdout MUST be non-empty in --json mode.
        assert result.output.strip(), "stdout must NOT be empty in --json mode"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus):
        """Happy path emits ``command=explain-command`` + non-empty verdict."""
        result = _invoke_explain(cli_runner, empty_corpus, "surface", json_mode=True)
        envelope = _parse_envelope(result)
        assert envelope["command"] == "explain-command"
        summary = envelope.get("summary") or {}
        verdict = summary.get("verdict") or ""
        assert isinstance(verdict, str) and verdict, f"summary.verdict must be a non-empty string, got {verdict!r}"

    def test_empty_corpus_partial_success_handling(self, cli_runner, empty_corpus):
        """W978 outcome: cmd_explain_command does NOT depend on the corpus.

        ``summary.partial_success`` is auto-injected as False because the
        static-metadata read succeeded fully. Mirrors W805-D outcome on
        cmd_workflow: not a silent SAFE because there is no corpus-dependent
        branch to silently succeed on.
        """
        result = _invoke_explain(cli_runner, empty_corpus, "surface", json_mode=True)
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        assert "partial_success" in summary, (
            f"summary.partial_success key must be auto-injected; got summary keys = {sorted(summary.keys())}"
        )
        # Read on a static registry SUCCEEDED - partial_success False is correct
        # here, not a silent SAFE: the verdict reflects a real read.
        assert summary["partial_success"] is False

    def test_static_metadata_consistency(self, cli_runner, empty_corpus, populated_corpus):
        """Empty-vs-populated parity: cmd_explain_command is static-metadata.

        Reading the same canonical command (`surface`) on a 0-symbol corpus
        and a real corpus must produce the same ``command_info`` payload
        (module / function / category / maturity / mcp_exposed / stale_sensitivity).
        Drift here would mean the metadata accidentally became corpus-derived,
        which is the W978 "first-hypothesis-was-wrong" trap. Pinning this
        keeps the static-metadata invariant honest.
        """
        empty_result = _invoke_explain(cli_runner, empty_corpus, "surface", json_mode=True)
        full_result = _invoke_explain(cli_runner, populated_corpus, "surface", json_mode=True)
        empty_env = _parse_envelope(empty_result)
        full_env = _parse_envelope(full_result)
        # The corpus-independent slice that MUST match byte-for-byte.
        keys = (
            "name",
            "module",
            "function",
            "category",
            "maturity",
            "mcp_exposed",
            "stale_sensitivity",
        )
        empty_info = {k: empty_env["command_info"][k] for k in keys}
        full_info = {k: full_env["command_info"][k] for k in keys}
        assert empty_info == full_info, (
            f"cmd_explain_command must be corpus-independent; empty={empty_info!r} vs full={full_info!r}"
        )

    def test_explain_known_command_emits_real_explanation(self, cli_runner, empty_corpus):
        """Positive coverage: a known canonical command produces a real
        explanation card (category + module + function + stale_sensitivity).
        """
        result = _invoke_explain(cli_runner, empty_corpus, "health", json_mode=True)
        assert result.exit_code == 0, result.output
        envelope = _parse_envelope(result)
        info = envelope.get("command_info") or {}
        assert info["name"] == "health"
        assert info["module"].startswith("roam.commands."), info
        assert info["function"], info
        # ``health`` is in the high-sensitivity set in _STALE_SENSITIVE.
        assert info["stale_sensitivity"] == "high"

    def test_explain_unknown_command_w1074_closest_match_intact(self, cli_runner, empty_corpus):
        """W1074 regression: a near-match unknown command suggests a fix.

        ``roam explain-command healt`` (one missing char) should land within
        difflib cutoff 0.6 of ``health`` and surface a ``did you mean``
        suggestion in the structured envelope. This is the
        ``structured_unknown_filter`` (W1083-followup) path layered on top
        of the W1074 closest-match block.
        """
        result = _invoke_explain(cli_runner, empty_corpus, "healt", json_mode=True)
        assert result.exit_code == 2, result.output
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        # W1083-followup uniformity: closed-enum state on the unknown branch.
        assert summary.get("state") == "unknown_command", (
            f"unknown-command state must be auto-disclosed; got {summary!r}"
        )
        # W1074 closest-match: 'health' present in did_you_mean.
        did_you_mean = summary.get("did_you_mean") or []
        assert "health" in did_you_mean, (
            f"W1074 closest-match for 'healt' must include 'health'; got did_you_mean={did_you_mean!r}"
        )
        # Verdict should name the requested unknown command.
        verdict = summary.get("verdict") or ""
        assert "healt" in verdict, f"verdict must name the unknown command; got {verdict!r}"

    def test_unknown_command_partial_success_disclosed(self, cli_runner, empty_corpus):
        """Unknown-command branch already discloses partial_success=True.

        W1083-followup uniformity: ``structured_unknown_filter`` sets
        ``state`` and ``partial_success`` so the unknown branch contracts
        match the W805 cohort.
        """
        result = _invoke_explain(cli_runner, empty_corpus, "zzzzzzzz", json_mode=True)
        assert result.exit_code == 2
        envelope = _parse_envelope(result)
        summary = envelope.get("summary") or {}
        assert summary.get("partial_success") is True, (
            f"unknown-command branch must set partial_success=True; got {summary!r}"
        )
        assert summary.get("state") == "unknown_command", (
            f"unknown-command branch must set state=unknown_command; got {summary!r}"
        )

    def test_empty_corpus_no_silent_safe_anti_shape(self, cli_runner, empty_corpus):
        """Anti-shape: verdict must NOT be one of the known silent-SAFE tokens.

        The canonical Pattern-2 anti-shape tokens never appear because the
        verdict is a static read with no corpus-dependent branch. Drift guard
        so a future refactor doesn't accidentally introduce one.
        """
        result = _invoke_explain(cli_runner, empty_corpus, "surface", json_mode=True)
        envelope = _parse_envelope(result)
        verdict = (envelope["summary"]["verdict"] or "").lower()
        forbidden = (
            "completed",
            "non-conformant",
            "compound operation completed",
            "see details",
        )
        for token in forbidden:
            assert token not in verdict, (
                f"Pattern-2 silent SAFE shape detected (verdict contains {token!r}): {verdict!r}"
            )


# ---------------------------------------------------------------------------
# Pattern-2 milder-shape pins: cohort-uniformity gaps on the HAPPY path.
# Each xfail-strict marker queues the fix for a separate wave; the file
# is intentionally test-only per the W805 accumulate-only constraint.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-H Pattern-2 milder shape: cmd_explain_command.py L282 (JSON) "
        "and L308 (text) emit a bare 'OK' verdict on the happy path. "
        "LAW 6 says the verdict must work without any other field; bare "
        "'OK' carries no command identifier and is explicitly in the LAW 4 "
        "blocklist at tests/test_law4_lint.py:278 (alongside 'completed', "
        "'see details', 'n/a'). Cohort alternative: "
        "'<command> metadata read' or 'explain-command <name> loaded' "
        "(terminal anchors 'read' / 'loaded' are in _CONCRETE_NOUN_ANCHORS). "
        "Separate fix wave."
    ),
)
def test_happy_path_law6_verdict_standalone(cli_runner, empty_corpus):
    """Pin: the happy-path verdict must work as a standalone line.

    LAW 6 requires that ``summary.verdict`` carry enough context to be
    actionable without reading any other field. Bare ``"OK"`` fails this:
    a consumer reading only the verdict has no idea WHAT is OK.
    """
    result = _invoke_explain(cli_runner, empty_corpus, "surface", json_mode=True)
    envelope = _parse_envelope(result)
    verdict = (envelope["summary"]["verdict"] or "").strip().lower()
    # Bare "OK" is the LAW 6 violation. LAW 6-compliant alternatives carry
    # the command name OR a concrete-noun anchor.
    assert verdict != "ok", f"verdict must not be bare 'OK' (LAW 6 violation); got {verdict!r}"
    # Positive form: verdict mentions the command name OR an anchor noun.
    assert "surface" in verdict or any(
        anchor in verdict
        for anchor in (
            "command",
            "metadata",
            "explanation",
            "read",
            "loaded",
            "described",
        )
    ), f"LAW 6: verdict must self-describe; got {verdict!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-H Pattern-2 milder shape: cmd_explain_command.py L282 emits "
        "verdict='OK' which is auto-derived as agent_contract.facts=['OK'] "
        "by formatter._derive_agent_contract. Bare 'OK' is explicitly "
        "blocklisted by tests/test_law4_lint.py:278. LAW 4 says facts must "
        "anchor on a concrete-noun terminal. Cohort alternative: emit a "
        "structured fact like '<command> metadata read' OR set "
        "summary.verdict to a LAW 4-compliant string so the auto-derive "
        "produces an anchored fact. Separate fix wave."
    ),
)
def test_happy_path_law4_facts_anchored(cli_runner, empty_corpus):
    """Pin: ``agent_contract.facts`` must be LAW 4 anchored on a concrete noun.

    Mirrors the cohort discipline: cmd_workflow emits ``"25 workflow recipes
    available"`` (anchored on 'recipes'); cmd_path_coverage emits
    ``"0 entry points scanned"`` (anchored on 'entries' / 'scanned').
    cmd_explain_command emits the literal verdict 'OK' as its sole fact,
    which is the LAW 4 anchor-vocabulary blocklist exemplar.
    """
    result = _invoke_explain(cli_runner, empty_corpus, "surface", json_mode=True)
    envelope = _parse_envelope(result)
    facts = (envelope.get("agent_contract") or {}).get("facts") or []
    assert isinstance(facts, list) and facts, f"agent_contract.facts must be non-empty; got {facts!r}"
    # Mirror the LAW 4 lint's blocklist check (tests/test_law4_lint.py:278).
    forbidden_facts = {"ok", "no data", "completed", "see details", "tbd", "n/a", "done"}
    for fact in facts:
        if not isinstance(fact, str):
            continue
        assert fact.strip().lower() not in forbidden_facts, (
            f"LAW 4 violation: bare {fact!r} is in the anchor-blocklist "
            f"(see tests/test_law4_lint.py:278). All facts: {facts!r}"
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-H Pattern-2 milder shape: cmd_explain_command.py L276-306 "
        "happy path emits NO closed-enum summary.state. The unknown-command "
        "branch ALREADY emits state='unknown_command' via "
        "structured_unknown_filter (W1083-followup). Cohort uniformity gap: "
        "the happy path should mirror with state='ok' / 'known_command' so "
        "a consumer can switch on a single closed-enum value rather than "
        "parsing the verdict string. Separate fix wave."
    ),
)
def test_happy_path_state_closed_enum_disclosed(cli_runner, empty_corpus):
    """Pin: the happy path should expose a closed-enum ``state`` field.

    Cohort uniformity: cmd_path_coverage emits state in
    {'no_entry_points', 'no_sinks', 'no_paths_connecting', 'ok'}; the
    unknown-command branch in this same file already emits
    state='unknown_command'. The happy path should likewise emit a
    closed-enum disclosure.
    """
    result = _invoke_explain(cli_runner, empty_corpus, "surface", json_mode=True)
    envelope = _parse_envelope(result)
    summary = envelope.get("summary") or {}
    state = summary.get("state") or envelope.get("state")
    accepted = {"ok", "known_command", "loaded", "resolved"}
    assert state in accepted, (
        f"summary.state should disclose the resolution state on the happy "
        f"path; got {state!r}; expected one of {accepted}"
    )
