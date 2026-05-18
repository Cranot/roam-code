"""W805-FFF: Empty-corpus Pattern-2 + Pattern-1-V-D smoke test on ``cmd_cga``.

cmd_cga is the in-toto v1 predicate producer one tier under cmd_attest
(W805-CCC clean counter-example): emits a Code Graph Attestation with
predicate type ``https://roam-code.com/spec/CodeGraph/v1``, carrying a
merkle root over per-file symbol fingerprints, an edge-bundle digest,
symbol/edge counts, languages, and optional taint reachability claims.
Distinct from cmd_attest -- attest is a *composite-evidence aggregator*
over a diff, cga is a *predicate producer* over the whole indexed graph.

W978 first-hypothesis discipline: empirical probe across (truly empty
corpus = 0 files / 0 symbols / 0 edges) and (minimal corpus = 1 file
/ 1 symbol / 0 edges) shows cmd_cga DOES exhibit a Pattern-2 +
Pattern-1-V-D bug:

  * Truly empty corpus -> ``verdict: "CGA emitted: 0 symbols / 0 edges,
    merkle=e3b0c44298fc..."`` with ``partial_success: false``, NO
    ``state`` field, NO ``resolution`` field. The merkle root is the
    SHA-256 of empty string ``e3b0c44298fc1c149afbf4c8996fb92427ae41e4
    649b934ca495991b7852b855`` -- a well-known constant. The edge-
    bundle digest is the same. Two empty-corpus CGAs verify byte-
    identical, making the attestation cryptographically meaningless
    while still presenting a green "CGA emitted" verdict.

This is a real Pattern-2 silent-SAFE bug AND a Pattern-1-V-D silent-
success-on-degraded-resolution bug. The "predicate emitted"
verdict is indistinguishable between (real graph attestation over a
populated codebase) and (zero-symbol empty-string-hash stub over a
degenerate or unindexed repository). An agent consuming the envelope
cannot tell which one ran.

BUG class: Pattern-2 (silent fallback) + Pattern-1-V-D (silent success
on degraded resolution). file:line --
``src/roam/commands/cmd_cga.py:340-344`` (verdict construction) +
``cmd_cga.py:353-364`` (envelope summary lacks ``state`` /
``resolution`` / ``partial_success: True`` on empty corpus).

This test pins via xfail-strict on the bug assertions and positive
regression pins on the disclosure-shape invariants that SHOULD hold.

W805 sweep tally update (incl. this entry):
  * Aggregator-family bugs: ~6+ (cmd_brief / cmd_audit / cmd_dogfood
    etc., all _compound_envelope-rooted) -- unchanged.
  * Predicate-producer family: cmd_cga -- FIRST bug (W805-FFF).
  * Clean counter-examples (now FOUR): cmd_next (W805-VV),
    cmd_intent_check (W805-YY), cmd_mode (W805-???), cmd_attest
    (W805-CCC). cmd_cga is NOT joining the clean roster.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402

_CMD_PATH = Path(__file__).resolve().parents[1] / "src" / "roam" / "commands" / "cmd_cga.py"

# SHA-256 of the empty byte-string. Surfaced in the CGA predicate's
# merkle_root and edge_bundle_digest when the indexed graph is empty.
# A well-known constant -- presence in a "successful" CGA envelope is
# a smoking-gun signal that the attestation is cryptographically empty.
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@pytest.fixture
def cli_runner():
    return CliRunner()


def _git_init_empty(path: Path) -> None:
    """Initialize a git repo with NO tracked files (truly empty corpus)."""
    subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-qm", "init"],
        cwd=path,
        capture_output=True,
    )


def _git_init_minimal(path: Path) -> None:
    """Initialize a git repo with one trivial python file."""
    subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    # Configure git to NOT mangle line endings so --allow-dirty isn't needed
    # on Windows. (CI runs on Linux too -- this is a no-op there.)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=path, capture_output=True)
    (path / "x.py").write_text("def f():\n    return 1\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, capture_output=True)


def _parse_json_envelope(result, command="cga-emit"):
    """Parse JSON from CliRunner result, tolerating index-progress prefix."""
    raw = getattr(result, "stdout", None)
    if raw is None:
        raw = result.output
    idx = raw.find("{")
    if idx < 0:
        pytest.fail(f"No JSON object in {command} output:\n{raw[:500]}")
    # Walk forward until we find a parseable JSON suffix.
    last_err = None
    for start in range(idx, min(len(raw), idx + 5000)):
        if raw[start] != "{":
            continue
        try:
            data = json.loads(raw[start:])
        except json.JSONDecodeError as e:
            last_err = e
            continue
        if data.get("command") == command:
            return data
    pytest.fail(f"No JSON envelope with command={command!r} in output (last err: {last_err}):\n{raw[:600]}")


# ---------------------------------------------------------------------------
# 1. Existence guard (W978 + W907 discipline)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """If cmd_cga.py vanishes, this whole module skips."""
    if not _CMD_PATH.is_file():
        pytest.skip(f"cmd_cga.py absent at {_CMD_PATH}")
    assert _CMD_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# 2. Empty corpus must not crash
# ---------------------------------------------------------------------------


def test_empty_corpus_no_crash(cli_runner, tmp_path, monkeypatch):
    """Truly empty git corpus (no tracked files) -- cga emit must NOT
    traceback. The producer auto-indexes (0 files, 0 symbols, 0 edges)
    and emits SOME envelope (whether degenerate or properly disclosed)."""
    proj = tmp_path / "empty"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["cga", "emit", "--no-write", "--allow-dirty"],
        cwd=proj,
        json_mode=True,
    )
    assert "Traceback" not in result.output, result.output
    # Exit may be 0 (current bug) or non-zero (post-fix). Either is fine
    # as long as the process didn't crash.
    assert result.exit_code in (0, 1, 5), f"unexpected exit code {result.exit_code}: {result.output[:400]}"


# ---------------------------------------------------------------------------
# 3. Envelope always has a verdict (LAW 6 baseline)
# ---------------------------------------------------------------------------


def test_empty_corpus_envelope_has_verdict(cli_runner, tmp_path, monkeypatch):
    """LAW 6: ``summary.verdict`` must be a non-empty string regardless
    of corpus state. Positive regression pin -- this invariant should
    hold under both pre- and post-fix behavior."""
    proj = tmp_path / "verdict"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["cga", "emit", "--no-write", "--allow-dirty"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), f"summary.verdict must be non-empty, got {verdict!r}"


# ---------------------------------------------------------------------------
# 4. State is explicit on empty corpus  (xfail-strict: real bug)
#
#     Pattern-2 invariant: degraded resolution (empty corpus -> empty-
#     string-hash predicate) must disclose ``summary.state``. cmd_cga
#     CURRENTLY emits no state field -- pinned via xfail-strict.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-FFF Pattern-2 bug: cmd_cga emits no `state` field on empty corpus. "
        "Verdict reads 'CGA emitted: 0 symbols / 0 edges' indistinguishable from "
        "a populated-graph attestation. Fix: cmd_cga.py:340-344 must set "
        "summary.state='empty_corpus' (or similar) when symbol_count == 0."
    ),
)
def test_empty_corpus_state_explicit(cli_runner, tmp_path, monkeypatch):
    """Empty corpus -> ``summary.state`` MUST be explicitly set (e.g.
    ``"empty_corpus"`` / ``"degenerate_graph"``), NOT a silent green
    "emitted" verdict. Pattern-2 invariant."""
    proj = tmp_path / "state_explicit"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["cga", "emit", "--no-write", "--allow-dirty"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    summary = data["summary"]
    state = summary.get("state")
    assert state is not None and state != "ok", f"empty-corpus path must set a non-default `state` field; got {state!r}"


# ---------------------------------------------------------------------------
# 5. partial_success must be True on empty corpus  (xfail-strict: real bug)
#
#     Pattern-2 invariant: ANY check that didn't fully run flips this.
#     An empty-corpus CGA is the textbook degraded-resolution case.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-FFF Pattern-2 bug: cmd_cga emits partial_success=false on empty "
        "corpus. The predicate is the empty-string SHA-256 stub -- not a real "
        "attestation. Fix: set partial_success=True whenever symbol_count == 0 "
        "(or edge_count == 0, or both digests equal _EMPTY_SHA256)."
    ),
)
def test_empty_corpus_partial_success_set(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "partial"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["cga", "emit", "--no-write", "--allow-dirty"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    assert data["summary"].get("partial_success") is True, (
        f"empty-corpus must set partial_success=true, got {data['summary'].get('partial_success')!r}"
    )


# ---------------------------------------------------------------------------
# 6. LAW 6: verdict on empty corpus names the degenerate state
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-FFF Pattern-2 bug: empty-corpus verdict reads 'CGA emitted: 0 "
        "symbols / 0 edges, merkle=e3b0c44298fc...'. LAW 6 requires the "
        "verdict to stand alone; an agent reading just this string cannot "
        "tell the corpus was empty. Fix: include 'empty corpus' / "
        "'degenerate' / '(no symbols indexed)' in the verdict prefix when "
        "symbol_count == 0."
    ),
)
def test_empty_corpus_law6_verdict_standalone(cli_runner, tmp_path, monkeypatch):
    """LAW 6 (compression forces domain neutrality): the verdict alone
    must signal degeneracy on empty corpus. An agent consuming only the
    verdict must be able to tell this was not a real attestation."""
    proj = tmp_path / "law6"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["cga", "emit", "--no-write", "--allow-dirty"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    verdict = data["summary"]["verdict"].lower()
    # At least ONE of these degeneracy signals must appear.
    degeneracy_signals = (
        "empty",
        "degenerate",
        "no symbols",
        "no graph",
        "uninitialized",
        "not indexed",
    )
    assert any(sig in verdict for sig in degeneracy_signals), (
        f"verdict {verdict!r} contains no empty-corpus signal (any of {degeneracy_signals})"
    )


# ---------------------------------------------------------------------------
# 7. Pattern-1-V-D: missing graph state disclosure  (xfail-strict)
#
#     Pattern-1-V-D = silent success on degraded resolution. The CGA
#     resolves the graph from the SQLite index; an empty index is the
#     graph-state analogue of "symbol resolved to file then unresolved".
#     The current envelope provides NO `resolution` field disclosing
#     whether the graph was real or stub.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-FFF Pattern-1-V-D bug: cmd_cga emits no `resolution` field "
        "(closed enum: graph / empty_graph / unindexed / ...). The CGA "
        "predicate built over an empty index is byte-identical to ANY other "
        "empty-index CGA -- the merkle is SHA-256('') -- yet the envelope "
        "reads as a successful emission. Fix: stamp resolution='empty_graph' "
        "(or similar) on the summary when symbol_count + edge_count == 0."
    ),
)
def test_missing_graph_disclosure(cli_runner, tmp_path, monkeypatch):
    """Pattern-1-V-D: the resolution state of the graph-read MUST be
    disclosed via a ``resolution`` field on the summary. Without it,
    a downstream verifier cannot distinguish an empty-corpus stub from
    a real attestation -- the bytes are the same."""
    proj = tmp_path / "missing_graph"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["cga", "emit", "--no-write", "--allow-dirty"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    resolution = data["summary"].get("resolution")
    assert resolution is not None and resolution != "graph", (
        f"empty-corpus path must set a non-default `resolution` field, got {resolution!r}"
    )


# ---------------------------------------------------------------------------
# 8. No silent "predicate emitted" verdict on empty corpus  (xfail-strict)
#
#     The Pattern-2 invariant pin: empty-corpus MUST NOT emit a verdict
#     prefix indistinguishable from a real-graph attestation.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-FFF Pattern-2 bug: empty-corpus verdict starts with 'CGA emitted:' "
        "exactly like a populated-graph attestation. An agent reading the verdict "
        "cannot tell which one ran. Fix: prefix with 'CGA stub' / 'EMPTY CORPUS:' "
        "/ 'no symbols indexed' when symbol_count == 0."
    ),
)
def test_no_silent_predicate_emitted_on_empty(cli_runner, tmp_path, monkeypatch):
    """The verdict MUST NOT start with the same green prefix used for
    real-graph emissions. Agent-safety: a silent 'CGA emitted' on empty
    corpus would teach the agent the attestation is valid, when in fact
    the predicate is the SHA-256 of the empty string."""
    proj = tmp_path / "no_silent"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["cga", "emit", "--no-write", "--allow-dirty"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    verdict = data["summary"]["verdict"].lower()

    # The verdict MUST NOT collapse to the populated-graph green prefix.
    forbidden_prefixes = (
        "cga emitted:",
        "attestation emitted",
        "predicate signed",
    )
    for prefix in forbidden_prefixes:
        assert not verdict.startswith(prefix), (
            f"Pattern-2 silent SAFE: empty-corpus emitted forbidden green prefix {prefix!r}: {verdict!r}"
        )


# ---------------------------------------------------------------------------
# 9. Smoking-gun: empty-string SHA-256 in merkle on empty corpus
#                (xfail-strict)
#
#     The cryptographic-meaninglessness pin: when both digests equal the
#     SHA-256 of the empty string, the producer MUST refuse OR loudly
#     disclose the stub. Two empty-corpus CGAs from two different repos
#     verify byte-identical today -- that is the bug.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-FFF cryptographic-meaninglessness bug: empty-corpus CGA "
        "predicate has merkle_root = edge_bundle_digest = SHA-256(empty "
        "string) = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b78"
        "52b855. Two empty CGAs are byte-identical -- the predicate is "
        "cryptographically meaningless. Fix: refuse with EXIT_DEGRADED or "
        "stamp summary.empty_predicate=True so verifier can reject."
    ),
)
def test_empty_corpus_predicate_not_silently_empty_string_sha(cli_runner, tmp_path, monkeypatch):
    """Smoking-gun: when symbol_count == 0 AND merkle_root equals the
    empty-string SHA-256, the producer must EITHER refuse (non-zero
    exit) OR stamp an explicit empty-predicate disclosure on the
    summary. The cryptographic claim 'this attestation binds the
    code graph' is FALSE when the graph is empty."""
    proj = tmp_path / "smoking_gun"
    proj.mkdir()
    _git_init_empty(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["cga", "emit", "--no-write", "--allow-dirty"],
        cwd=proj,
        json_mode=True,
    )
    data = _parse_json_envelope(result)
    summary = data["summary"]

    is_empty_predicate = summary.get("symbol_count") == 0 and summary.get("merkle_root") == _EMPTY_SHA256
    if is_empty_predicate:
        # The producer emitted a cryptographically empty predicate. The
        # post-fix contract: this MUST be loudly disclosed via at least
        # one of these channels.
        disclosed = (
            summary.get("partial_success") is True
            or summary.get("state") in {"empty_corpus", "degenerate_graph", "unindexed"}
            or summary.get("empty_predicate") is True
            or summary.get("resolution") in {"empty_graph", "unindexed"}
        )
        assert disclosed, (
            "empty-string-SHA predicate emitted without loud disclosure on "
            f"any of (partial_success, state, empty_predicate, resolution): "
            f"summary={dict(summary)!r}"
        )


# ---------------------------------------------------------------------------
# 10. Clean corpus: real predicate is loud and non-degenerate
#
#     The affirmative path. With a tracked source file + a real indexed
#     symbol, cga emits a predicate whose merkle is NOT the empty-string
#     SHA-256. Positive regression pin (NOT xfail) -- this is the path
#     that already works.
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_predicate(cli_runner, tmp_path, monkeypatch):
    """With a real source file present, cga emit produces a predicate
    with a non-empty-string merkle root and a non-zero symbol count.
    This is the path that currently works -- positive regression pin
    so a future "fix" cannot accidentally regress real emissions."""
    proj = tmp_path / "clean"
    proj.mkdir()
    _git_init_minimal(proj)
    monkeypatch.chdir(proj)

    # Index explicitly so the graph is populated.
    index_result = invoke_cli(cli_runner, ["index"], cwd=proj, json_mode=False)
    if index_result.exit_code != 0:
        pytest.skip(f"index failed in fixture: {index_result.output[:300]}")

    result = invoke_cli(
        cli_runner,
        ["cga", "emit", "--no-write", "--allow-dirty"],
        cwd=proj,
        json_mode=True,
    )
    if result.exit_code != 0:
        pytest.skip(f"cga emit exit_code={result.exit_code} -- env-dependent: {result.output[:300]}")
    data = _parse_json_envelope(result)
    summary = data["summary"]

    # Real predicate must have at least one symbol indexed AND a merkle
    # root distinct from the empty-string SHA-256.
    assert summary.get("symbol_count", 0) >= 1, f"clean corpus produced symbol_count={summary.get('symbol_count')!r}"
    assert summary.get("merkle_root") != _EMPTY_SHA256, (
        f"clean corpus produced empty-string-SHA merkle root: {summary.get('merkle_root')!r}"
    )
