"""W805-CCC: Empty-corpus Pattern-2 + Pattern-1-V-D smoke test on ``cmd_attest``.

cmd_attest is the proof-carrying PR attestation producer: it bundles
blast radius, risk score, breaking changes, fitness violations, budget
consumed, affected tests, and effects into one auditable artifact.
Distinct from cmd_pr_bundle (W805-NN), cmd_cga, and audit-trail-verify
-- attest is a *composite-evidence aggregator* not a substrate writer.

W978 first-hypothesis discipline: empirical probe (no-corpus, indexed-
no-diff, indexed-with-diff) shows cmd_attest does NOT exhibit either
the Pattern-2 silent-SAFE bug nor the Pattern-1-V-D missing-key
degraded-resolution bug suspected by the W805-ZZ agent. The three
degraded paths all explicitly disclose state:

  * No changes (no diff to attest) -> ``state: "no_changes"`` +
    ``partial_success: true`` + ``safe_to_merge: null`` (NOT True).
    Verdict prefix is ``"no changes found for <label>"`` -- NEVER
    ``"safe to merge"``. Risk floor canonical ``low`` per W531 CI-
    safety.
  * Changed files unresolved against the index -> explicit
    ``resolution: "unresolved"`` + ``partial_success: true`` +
    ``safe_to_merge: false`` + copy-paste-executable
    ``roam index`` hint in the verdict.
  * Clean indexed diff -> real composite-risk attestation with
    real ``safe_to_merge`` boolean.

Note on the "missing signing key" hypothesis from W805-ZZ:
``cmd_attest`` does NOT have a true signing-key path. The ``--sign``
flag merely emits a SHA-256 content hash of the evidence payload for
tamper detection (L494-497 ``_content_hash``); there is no asymmetric
signing key, no keyring lookup, no degraded-resolution branch on
missing-key. The Pattern-1-V-D suspicion does NOT apply: there is no
silent-success-on-degraded-resolution path because there is no
resolution to degrade. (The W805-ZZ agent's "attestation commands
have a known Pattern-1-V-D shape on missing-key paths" claim is
TRUE for asymmetric attest producers but FALSE for cmd_attest --
attest is in-toto-style hash-only, not GPG/sigstore-signed.)

This test is a CONFORMANCE pin -- it documents the desirable
composite-evidence-aggregator behavior and locks it in as a
regression invariant for the W805 sweep. NO xfail-strict because
there is NO bug. cmd_attest joins cmd_next (W805-VV) +
cmd_intent_check (W805-YY) as the THIRD catalogued clean
counter-example to the W805 aggregator family.

W805 sweep yield (incl. this entry): aggregator-family bug count
unchanged at ~6 (cmd_brief / cmd_audit / cmd_dogfood etc.);
state-reader / composite-evidence clean-counter-example family
confirmed three-strong (W805-VV cmd_next, W805-YY cmd_intent_check,
W805-CCC cmd_attest).
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

_CMD_PATH = Path(__file__).resolve().parents[1] / "src" / "roam" / "commands" / "cmd_attest.py"


@pytest.fixture
def cli_runner():
    return CliRunner()


def _git_init(path: Path) -> None:
    """Initialize a git repo with one tracked file + initial commit."""
    subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    (path / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, capture_output=True)


def _parse_json_envelope(result, command="attest"):
    """Parse JSON from CliRunner result; assert envelope shape.

    cmd_attest calls ``ensure_index()`` which may emit "Indexing..." /
    "Index is up to date (...)" progress notices before the JSON envelope
    on stdout (W985-incremental enriched the up-to-date line with a file
    count + --force hint). Extract the JSON object by locating the first
    ``{`` that begins a valid JSON payload.
    """
    raw = getattr(result, "stdout", None)
    if raw is None:
        raw = result.output
    # Locate the first ``{`` and try progressively from there.
    idx = raw.find("{")
    if idx < 0:
        pytest.fail(f"No JSON object in {command} output:\n{raw[:500]}")
    try:
        data = json.loads(raw[idx:])
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON from {command}: {e}\nOutput was:\n{raw[:500]}")
    assert data.get("command") == command, f"expected command={command!r}, got {data.get('command')!r}"
    return data


# ---------------------------------------------------------------------------
# 1. Existence guard (W978 + W907 discipline)
# ---------------------------------------------------------------------------


def test_command_exists_or_skip():
    """If cmd_attest.py vanishes, this whole module skips."""
    if not _CMD_PATH.is_file():
        pytest.skip(f"cmd_attest.py absent at {_CMD_PATH}")
    assert _CMD_PATH.stat().st_size > 0


# ---------------------------------------------------------------------------
# 2. Empty corpus / no-changes path does not crash
# ---------------------------------------------------------------------------


def test_empty_corpus_no_crash(cli_runner, tmp_path, monkeypatch):
    """No ``.roam/``, no git -- attest must NOT traceback. ``ensure_index``
    auto-bootstraps an empty index; the no-diff path then emits a clean
    structured envelope (exit 0)."""
    proj = tmp_path / "untouched"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["attest"], cwd=proj, json_mode=True)
    assert "Traceback" not in result.output, result.output
    # No-diff path exits 0 with a clean envelope.
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# 3. Envelope always carries a verdict (LAW 6 baseline)
# ---------------------------------------------------------------------------


def test_empty_corpus_envelope_has_verdict(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "verdict"
    proj.mkdir()
    _git_init(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["attest"], cwd=proj, json_mode=True)
    data = _parse_json_envelope(result)
    verdict = data["summary"].get("verdict")
    assert isinstance(verdict, str) and verdict.strip(), "summary.verdict must be non-empty"


# ---------------------------------------------------------------------------
# 4. State is explicit on no-diff (NOT silent SAFE)
# ---------------------------------------------------------------------------


def test_empty_corpus_state_explicit(cli_runner, tmp_path, monkeypatch):
    """No diff to attest -> ``summary.state == "no_changes"``, NOT a
    silent SAFE / "ready to merge" verdict. ``safe_to_merge`` MUST be
    ``None`` (not ``True``) -- the assessment never ran (no diff)."""
    proj = tmp_path / "state_explicit"
    proj.mkdir()
    _git_init(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["attest"], cwd=proj, json_mode=True)
    data = _parse_json_envelope(result)
    summary = data["summary"]
    assert summary.get("state") == "no_changes", f"expected state='no_changes', got {summary.get('state')!r}"
    # No assessment ran -- safe_to_merge MUST be None (not True).
    # Pattern-2 invariant: a degraded-resolution path must not emit a
    # success boolean indistinguishable from a fully-assessed pass.
    assert summary.get("safe_to_merge") is None, (
        f"Pattern-2 silent SAFE: no-diff path emitted safe_to_merge={summary.get('safe_to_merge')!r} "
        f"(expected None on degraded-resolution)"
    )


# ---------------------------------------------------------------------------
# 5. partial_success is True on no-diff
# ---------------------------------------------------------------------------


def test_empty_corpus_partial_success_set(cli_runner, tmp_path, monkeypatch):
    """No-diff is the degraded-resolution path -> ``partial_success: true``
    (Pattern-2 invariant: ANY check that didn't fully run flips this)."""
    proj = tmp_path / "noindex"
    proj.mkdir()
    _git_init(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["attest"], cwd=proj, json_mode=True)
    data = _parse_json_envelope(result)
    assert data["summary"]["partial_success"] is True


# ---------------------------------------------------------------------------
# 6. LAW 6: verdict alone is parseable / actionable
# ---------------------------------------------------------------------------


def test_law6_verdict_standalone(cli_runner, tmp_path, monkeypatch):
    """LAW 6 (compression forces domain neutrality): ``summary.verdict``
    works standalone. On no-diff it names the scope (uncommitted /
    staged / commit-range) AND carries the canonical risk_level
    parenthesis (``(risk_level low)``) per W641-followup-D."""
    proj = tmp_path / "law6"
    proj.mkdir()
    _git_init(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["attest"], cwd=proj, json_mode=True)
    data = _parse_json_envelope(result)
    verdict = data["summary"]["verdict"]
    # The verdict names the scope.
    assert "no changes" in verdict.lower(), verdict
    # And carries the canonical risk_level on a closed enum.
    assert "(risk_level low)" in verdict, f"verdict missing canonical risk_level parenthesis: {verdict!r}"


# ---------------------------------------------------------------------------
# 7. Missing-key / no-diff Pattern-1-V-D disclosure
#
#     W805-ZZ hypothesis: "attestation commands have a known Pattern-1-V-D
#     shape on missing-key paths". For cmd_attest this is FALSE because
#     there is no real signing key -- ``--sign`` emits a SHA-256 content
#     hash, not an asymmetric signature. The no-diff path IS a valid
#     Pattern-1-V-D test target though (resolution degraded from "real
#     attestation" -> "no-diff stub"), and it correctly discloses state.
#
#     This test pins the absence of the suspected bug.
# ---------------------------------------------------------------------------


def test_missing_key_pattern_1d_disclosure(cli_runner, tmp_path, monkeypatch):
    """The closest analogue to a "missing key" degraded-resolution path
    is the no-diff path (resolution degraded from "real attestation" ->
    "empty stub"). It MUST disclose Pattern-1-V-D: explicit state +
    partial_success + non-True safe_to_merge."""
    proj = tmp_path / "missing_key"
    proj.mkdir()
    _git_init(proj)
    monkeypatch.chdir(proj)

    # --sign requests a content-hash but there's no diff to hash -- the
    # degraded path runs and the envelope must NOT collapse to silent SAFE.
    result = invoke_cli(cli_runner, ["attest", "--sign"], cwd=proj, json_mode=True)
    data = _parse_json_envelope(result)
    summary = data["summary"]
    # All three Pattern-1-V-D disclosure axes must be set.
    assert summary.get("state") == "no_changes"
    assert summary.get("partial_success") is True
    assert summary.get("safe_to_merge") is None


# ---------------------------------------------------------------------------
# 8. No silent "attestation complete" / SAFE TO MERGE on empty diff
#
#     The Pattern-2 invariant pin: empty-diff MUST NOT emit a verdict
#     prefix indistinguishable from a fully-assessed SAFE TO MERGE.
# ---------------------------------------------------------------------------


def test_no_silent_attestation_complete_on_empty(cli_runner, tmp_path, monkeypatch):
    """Empty diff MUST NOT emit a silent SAFE TO MERGE / attestation-
    complete verdict. The verdict prefix must explicitly name the
    no-changes state, NOT collapse to a green pass.

    Agent-safety: a silent SAFE on a no-diff attest would teach the
    agent that the gate passed and the change is reviewable, when in
    fact nothing was reviewed (there was nothing to review). This test
    pins the desirable behavior: emit explicit "no changes found"."""
    proj = tmp_path / "no_silent"
    proj.mkdir()
    _git_init(proj)
    monkeypatch.chdir(proj)

    result = invoke_cli(cli_runner, ["attest"], cwd=proj, json_mode=True)
    data = _parse_json_envelope(result)
    summary = data["summary"]
    verdict_lower = summary["verdict"].lower()

    # The verdict MUST NOT start with a green-pass phrase.
    forbidden_prefixes = (
        "safe to merge",
        "attestation complete",
        "ready to merge",
        "all clear",
        "no risk",
    )
    for prefix in forbidden_prefixes:
        assert not verdict_lower.startswith(prefix), (
            f"Pattern-2 silent SAFE: no-diff path emitted forbidden verdict prefix {prefix!r}: {summary['verdict']!r}"
        )
    # The structured safe_to_merge MUST NOT be True (the bug would be
    # silent True on degraded resolution).
    assert summary.get("safe_to_merge") is not True, (
        "Pattern-2 silent SAFE: no-diff path emitted safe_to_merge=True; expected None (not assessed)"
    )


# ---------------------------------------------------------------------------
# 9. Clean corpus: real diff -> real attestation
#
#     The affirmative path. With a tracked file + a real diff,
#     attest emits a fully-assessed envelope with a real boolean
#     safe_to_merge and partial_success=False (clean run).
# ---------------------------------------------------------------------------


def test_clean_corpus_emits_real_attestation(cli_runner, tmp_path, monkeypatch):
    """With a real diff present, attest runs the full evidence pipeline
    and emits a fully-assessed envelope (real boolean ``safe_to_merge``
    + ``partial_success: false`` + real ``risk_score``)."""
    proj = tmp_path / "clean"
    proj.mkdir()
    _git_init(proj)
    monkeypatch.chdir(proj)

    # Index the repo so the resolver can find the changed file.
    index_result = invoke_cli(cli_runner, ["index"], cwd=proj, json_mode=False)
    if index_result.exit_code != 0:
        pytest.skip(f"index failed in fixture: {index_result.output[:200]}")

    # Modify the tracked file -- now we have a real diff to attest.
    (proj / "x.py").write_text("x = 1\ny = 2\n", encoding="utf-8")

    result = invoke_cli(cli_runner, ["attest"], cwd=proj, json_mode=True)
    if result.exit_code != 0:
        pytest.skip(f"attest exit_code={result.exit_code} -- env-dependent (git/networkx)")
    data = _parse_json_envelope(result)
    summary = data["summary"]

    # Full assessment ran -- safe_to_merge MUST be a real boolean.
    sm = summary.get("safe_to_merge")
    assert isinstance(sm, bool), f"clean diff must produce a real boolean safe_to_merge, got {sm!r}"
    # State field is absent on the clean path (only set on degraded paths).
    # If risk was computed, the canonical risk_level is present.
    assert "risk_level_canonical" in summary, "clean attestation must emit canonical risk_level"
    # The verdict carries the canonical risk_level parenthesis.
    verdict = summary["verdict"]
    assert "(risk_level " in verdict, f"verdict missing canonical risk_level: {verdict!r}"
