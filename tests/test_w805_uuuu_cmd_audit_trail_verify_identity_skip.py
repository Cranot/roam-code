"""W805-UUUU -- verifier-side identity-skip pin for ``cmd_audit_trail_verify``.

Ninety-ninth-in-batch W805 sweep, ``cmd_audit_trail_verify.py``. SECOND
member of the *verifier-side* identity-skip slice of the lineage-disclosure
family, alongside W805-PPPP (cmd_cga verify subject.name skip). The wider
family is now 7-STRONG:

- Producer-side gap:
    - W805-BBBB cmd_simulate    (counterfactual TARGET-side resolution)
    - W805-DDDD cmd_orchestrate (partition output vacuous)
    - W805-GGGG cmd_capsule     (snapshot freshness disclosure)
    - W805-IIII cmd_fingerprint (cross-repo fingerprint compare lineage)
    - W805-LLLL cmd_runs        (replay artefact-resolution lineage)
- Verifier-side identity-skip:
    - W805-PPPP cmd_cga         (predicate.subject[0].name never checked)
    - W805-UUUU cmd_audit_trail_verify (THIS file: actor/repo/git_sha
      never cross-checked against live identity)

Hypothesis from W805-PPPP agent (verified live below): ``cmd_audit_trail_verify``
walks the SHA-256 chain over ``.roam/audit-trail.jsonl`` and surfaces
``previous_record_hash`` mismatches, but NEVER cross-checks the recorded
``actor`` / ``repo`` / ``git_sha`` fields against the live repo's
``git_actor()`` / ``git_origin_url()`` / ``git_head_sha()``. An audit
trail produced by repo A (or by attacker A masquerading as developer B)
can be physically copied into repo B's ``.roam/`` directory and the
verifier returns ``state: "valid"`` + exit 0 with no disclosure that the
recorded identity is foreign.

W978 first-hypothesis discipline (re-run BEFORE writing any test)
=================================================================

1. **Module surface probe.** Read ``cmd_audit_trail_verify.py`` in full
   (403 lines). The only verification primitive is ``_verify_chain`` ->
   ``hashlib.sha256(line.encode("utf-8")).hexdigest()`` against the next
   record's ``previous_record_hash``. There is no call to ``git_actor``,
   ``git_origin_url``, ``git_head_sha``, or any other identity helper.

2. **Producer surface probe.** Read ``commands/pr_analyze/audit_trail.py``
   (``_emit_audit_trail_record``). Records carry ``actor`` (from
   ``git_actor()``), ``repo`` (from ``git_origin_url()``), and ``git_sha``
   (from ``git_head_sha()``). These ARE captured at emit time but NEVER
   re-derived at verify time. Identity claim is therefore load-bearing on
   the producer side and silently trusted on the verifier side -- the
   classic "make fallback chains loud" gap (CP45/CP46).

3. **Distinctness from W805-LLLL (cmd_runs replay).** ``cmd_runs verify``
   uses HMAC over the per-repo ``.ledger_key`` (``src/roam/runs/signing.py``
   docstring lines 19-23 say explicitly: "it does NOT prove **who** wrote
   the events"). HMAC is by-design byte-only; runs-verify CANNOT be
   identity-aware without a separate identity layer. ``cmd_audit_trail_verify``
   has the OPPOSITE shape -- the records already carry identity fields,
   the verifier just never looks at them. AXIS CONFIRMED DISTINCT.

4. **Distinctness from W805-PPPP (cmd_cga).** cmd_cga's bug is on the
   predicate's ``subject.name`` -- a single in-toto subject identifier
   never cross-checked against ``project_root`` / git remote. cmd_audit_
   trail_verify's bug is on THREE per-record fields (``actor`` / ``repo`` /
   ``git_sha``) emitted at every entry-write time, never re-derived. cmd_cga
   carries WHEN-signed lineage (``indexed_at``); audit-trail records carry
   WHO-emitted lineage (``actor``) and HISTORY-anchored lineage (``git_sha``).
   Different identity axes, same verifier-side blind-spot pattern.

5. **Distinctness from W826/W829 (empty-corpus / 3-state matrix).** Those
   exercise the ``trail_missing`` / ``has_records`` / ``has_real_issues``
   state matrix on a clean uninitialized vs broken-chain corpus. The
   identity-skip axis is orthogonal: the chain can be 100% valid AND the
   identity can be foreign. THIS test runs entirely inside the chain_valid
   state and probes a DIFFERENT verifier responsibility.

6. **Reproducibility.** Any audit-trail file produced by another developer,
   another machine, or another repo can be copy-pasted into a new repo's
   ``.roam/audit-trail.jsonl`` and pass verify cleanly. In a multi-tenant
   CI / fleet scenario, this is exactly the failure mode the verifier
   exists to prevent.

W907 verify-cycle check
=======================

``grep -i 'avoid.*cycle|circular import|kept local|would create a cycle|
duplicated.*here'`` on ``src/roam/commands/cmd_audit_trail_verify.py`` +
``src/roam/commands/audit_trail_helpers.py`` + ``src/roam/commands/
pr_analyze/audit_trail.py`` == NO MATCHES. Lazy imports
(``from roam.db.findings import FindingRecord, emit_finding`` at line 122
inside ``_emit_audit_trail_verify_findings``; ``from roam.db.connection
import open_db`` at line 290 inside the ``if persist:`` branch) are
flag-conditional benign deferred imports, not cargo-cult false cycles.
W907 clean.

Pinned via ``xfail(strict=True)`` so a future fix is detected (xpass ->
test failure -> unwrap and seal). The non-xfail tests pin today-good
behaviours (chain verification, hash mismatch detection, empty-trail
disclosure) so the fix has to be additive.

Run isolation:
    python -m pytest tests/test_w805_uuuu_cmd_audit_trail_verify_identity_skip.py -x -n 0

Regression baseline:
    python -m pytest tests/test_audit_trail_verify.py tests/test_audit_trail_chain.py \
        tests/test_audit_trail_conformance.py -x -n 0

Sister parity:
    python -m pytest tests/test_w805_pppp_cmd_cga_attestation_lineage.py \
        tests/test_w829_audit_trail_verify_empty_corpus.py -x -n 0
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 -- verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_VERIFY_SPEC = importlib.util.find_spec("roam.commands.cmd_audit_trail_verify")
_HELPERS_SPEC = importlib.util.find_spec("roam.commands.audit_trail_helpers")
_EMITTER_SPEC = importlib.util.find_spec("roam.commands.pr_analyze.audit_trail")


def test_command_and_substrate_exist():
    """W978/W907 gate: cmd_audit_trail_verify + helpers + emitter import cleanly."""
    if _CMD_VERIFY_SPEC is None:
        pytest.skip("roam.commands.cmd_audit_trail_verify not installed")
    assert _HELPERS_SPEC is not None, "roam.commands.audit_trail_helpers missing"
    assert _EMITTER_SPEC is not None, "roam.commands.pr_analyze.audit_trail missing"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _make_repo(
    tmp_path: Path,
    name: str,
    files: dict,
    *,
    actor_email: str = "t@t.com",
    actor_name: str = "Test",
    origin_url: str | None = None,
) -> Path:
    """Create a git repo with a configurable actor identity + optional origin URL."""
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    for rel, content in files.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.email", actor_email], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "user.name", actor_name], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(proj), capture_output=True)
    if origin_url is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", origin_url],
            cwd=str(proj),
            capture_output=True,
        )
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=str(proj), capture_output=True)
    return proj


def _write_chain(audit_trail_path: Path, records: list[dict]) -> None:
    """Write a SHA-256-chained audit trail to *audit_trail_path*.

    Each record's ``previous_record_hash`` is set to the SHA-256 of the
    canonical JSON of the prior record (genesis = ""), matching the
    contract enforced by ``cmd_audit_trail_verify._verify_chain``.
    """
    import hashlib

    audit_trail_path.parent.mkdir(parents=True, exist_ok=True)
    prev_hash = ""
    lines = []
    for rec in records:
        rec_with_prev = {**rec, "previous_record_hash": prev_hash}
        line = json.dumps(rec_with_prev, separators=(",", ":"), sort_keys=True)
        lines.append(line)
        prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
    audit_trail_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def clean_repo(tmp_path):
    """Empty git repo with a known actor identity and origin URL."""
    return _make_repo(
        tmp_path,
        "verify_uuuu_clean",
        {"app.py": "def alpha():\n    return 1\n"},
        actor_email="alice@example.com",
        actor_name="Alice",
        origin_url="https://example.com/alice/repo.git",
    )


@pytest.fixture
def foreign_audit_trail(tmp_path):
    """A valid SHA-256-chained audit trail whose records claim a DIFFERENT
    actor + repo than any live repo. Returns the path to the chain file.

    The chain is internally consistent (every previous_record_hash matches)
    so ``_verify_chain`` reports ``state: "valid"`` regardless of where it
    is mounted.
    """
    chain_path = tmp_path / "foreign_chain" / "audit-trail.jsonl"
    _write_chain(
        chain_path,
        [
            {
                "schema": "roam-audit-trail-v1",
                "sequence_number": 1,
                "timestamp": "2026-01-01T00:00:00Z",
                "tool": "roam-code",
                "tool_version": "x.y.z",
                "actor": "mallory@evil.example",
                "repo": "https://evil.example/mallory/forked.git",
                "git_sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                "diff_sha256": "0" * 64,
                "verdict": "SAFE",
            },
            {
                "schema": "roam-audit-trail-v1",
                "sequence_number": 2,
                "timestamp": "2026-01-01T00:00:01Z",
                "tool": "roam-code",
                "tool_version": "x.y.z",
                "actor": "mallory@evil.example",
                "repo": "https://evil.example/mallory/forked.git",
                "git_sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                "diff_sha256": "1" * 64,
                "verdict": "SAFE",
            },
        ],
    )
    return chain_path


# ---------------------------------------------------------------------------
# Invoke helpers
# ---------------------------------------------------------------------------


def _invoke(runner, cwd: Path, args: list[str]):
    """Invoke ``roam ...`` from inside *cwd* via the top-level group."""
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def _parse_json(result):
    # Accept 0 (clean), 2 (usage), and 5 (gate failure / chain broken).
    assert result.exit_code in (0, 2, 5), f"unexpected exit={result.exit_code}:\n{result.output}"
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


# ---------------------------------------------------------------------------
# POSITIVE shape pins -- today-good behaviours. The fix must stay additive.
# ---------------------------------------------------------------------------


class TestAuditTrailVerifyHmacChainOrthogonalFromIdentityClaim:
    """The SHA-256 chain integrity check is orthogonal to identity claims.
    Today these tests pin the chain checks as load-bearing (mirror of the
    W805-LLLL invariant: HMAC byte-only by design, identity is a separate
    layer). The fix lands the identity layer on top -- it MUST NOT break
    the chain checks.
    """

    def test_hash_mismatch_still_detected_after_fix(self, clean_repo, cli_runner, tmp_path):
        """Tamper the middle record's content -> chain breaks -> state=broken.
        The identity-skip fix must not regress this load-bearing check."""
        chain_path = clean_repo / ".roam" / "audit-trail.jsonl"
        _write_chain(
            chain_path,
            [
                {
                    "schema": "roam-audit-trail-v1",
                    "sequence_number": 1,
                    "timestamp": "2026-01-01T00:00:00Z",
                    "actor": "alice@example.com",
                    "verdict": "SAFE",
                },
                {
                    "schema": "roam-audit-trail-v1",
                    "sequence_number": 2,
                    "timestamp": "2026-01-01T00:00:01Z",
                    "actor": "alice@example.com",
                    "verdict": "SAFE",
                },
            ],
        )
        # Tamper -- rewrite line 2 with a fresh record that doesn't link back.
        lines = chain_path.read_text(encoding="utf-8").splitlines()
        lines[1] = json.dumps(
            {
                "previous_record_hash": "0" * 64,
                "schema": "roam-audit-trail-v1",
                "sequence_number": 2,
                "verdict": "TAMPERED",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = _invoke(cli_runner, clean_repo, ["--json", "audit-trail-verify"])
        data = _parse_json(r)
        assert data["summary"]["state"] == "broken", data["summary"]
        assert data["summary"]["chain_valid"] is False

    def test_clean_chain_state_valid_baseline(self, clean_repo, cli_runner):
        """A locally-emitted clean chain verifies state=valid.

        This pins the today-good positive verdict so a fix landing identity
        cross-checks does not regress the chain-integrity-only success path
        for chains emitted in-place by the same actor.
        """
        chain_path = clean_repo / ".roam" / "audit-trail.jsonl"
        # Build the chain with the SAME actor/repo identity that the live
        # repo carries, so even after the fix lands the identity check
        # will pass.
        _write_chain(
            chain_path,
            [
                {
                    "schema": "roam-audit-trail-v1",
                    "sequence_number": 1,
                    "timestamp": "2026-01-01T00:00:00Z",
                    "actor": "alice@example.com",
                    "repo": "https://example.com/alice/repo.git",
                    "verdict": "SAFE",
                },
            ],
        )
        r = _invoke(cli_runner, clean_repo, ["--json", "audit-trail-verify"])
        data = _parse_json(r)
        assert data["summary"]["state"] == "valid", data["summary"]
        assert data["summary"]["chain_valid"] is True


# ---------------------------------------------------------------------------
# Sister-family invariant cross-checks (must stay green; do NOT re-assert
# the sister files' xfail-strict claims to avoid collision).
# ---------------------------------------------------------------------------


class TestW805PpppInvariantsPreserved:
    """W805-PPPP (cmd_cga verify subject.name skip) sister cross-check.

    Baseline: ``roam cga emit --no-write`` produces a parseable envelope
    with a predicate. We do NOT re-assert W805-PPPP's xfail-strict pins.
    """

    def test_cga_emit_baseline_parseable(self, clean_repo, cli_runner):
        # Indexing is required for CGA emit; do it in-process.
        from tests.conftest import index_in_process

        out, rc = index_in_process(clean_repo, "--force")
        assert rc == 0, out
        r = _invoke(cli_runner, clean_repo, ["--json", "cga", "emit", "--no-write"])
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        statement = data.get("statement") or {}
        assert statement.get("predicate"), f"predicate missing in {data}"


class TestW829InvariantsPreserved:
    """W829 (audit-trail-verify empty corpus, 3-state matrix) sister.

    Baseline: empty corpus -> state=uninitialized + partial_success=True
    + chain_valid=False + total_records=0. We do NOT re-assert W829's
    full set; just that the uninitialized branch is still wired so the
    identity-skip fix doesn't accidentally collapse the 3-state matrix
    into a 2-state one.
    """

    def test_uninitialized_state_still_disclosed(self, clean_repo, cli_runner):
        # No audit trail at all.
        assert not (clean_repo / ".roam" / "audit-trail.jsonl").exists()
        r = _invoke(cli_runner, clean_repo, ["--json", "audit-trail-verify"])
        # No --gate -> exit 0 regardless of state.
        assert r.exit_code == 0, r.output
        data = _parse_json(r)
        summary = data["summary"]
        assert summary["state"] == "uninitialized", summary
        assert summary["partial_success"] is True
        assert summary["chain_valid"] is False
        assert summary["total_records"] == 0


# ---------------------------------------------------------------------------
# REAL BUG -- Pattern-1-V-D + CP45/CP46 verifier-side identity-skip
# Pinned xfail(strict=True): fix will flip to xpass -> test failure -> unwrap.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-UUUU Pattern-1-V-D bug: src/roam/commands/cmd_audit_trail_verify.py:163-212 "
        "(_verify_chain) walks the SHA-256 hash chain and surfaces "
        "previous_record_hash mismatches + parse errors -- but NEVER "
        "cross-checks the per-record `actor` / `repo` / `git_sha` "
        "identity fields against the live repo's git_actor() / "
        "git_origin_url() / git_head_sha() helpers (defined in "
        "src/roam/commands/git_helpers.py:59-79 and used at emit time in "
        "src/roam/commands/pr_analyze/audit_trail.py:72-74). A foreign "
        "audit trail (different actor email, different remote URL, "
        "different HEAD SHA) physically copied into "
        ".roam/audit-trail.jsonl verifies state='valid' + chain_valid=True "
        "+ exit 0 with no disclosure that the recorded identity is "
        "foreign. cmd_audit_trail_verify is STRUCTURALLY DISTINCT from "
        "W805-LLLL (cmd_runs replay HMAC byte-only by design per "
        "src/roam/runs/signing.py:19-23) and from W805-PPPP (cmd_cga "
        "single subject.name vs three per-record identity fields) -- "
        "this is the second verifier-side member of the lineage-"
        "disclosure family. Fix: extend _verify_chain (or wrap it) so "
        "the per-record actor / repo / git_sha values are compared to "
        "git_actor() / git_origin_url() / git_head_sha() at verify time; "
        "emit a closed-enum 'identity_mismatch' issue when they disagree; "
        "surface the (claimed, live) pair in the issues[] entry so the "
        "envelope discloses both sides of the comparison. See CLAUDE.md "
        "Pattern-1-V-D + 'Make fallback chains loud' (CP45/CP46) + "
        "W805-PPPP sister pin. Family 7-STRONG (5 producer-side + 2 "
        "verifier-side) confirmed."
    ),
)
class TestAuditTrailVerifyForeignChainIdentityDisclosureBug:
    def test_verify_with_bogus_signer_distinct_from_clean(self, clean_repo, foreign_audit_trail, cli_runner):
        """Pattern-1-V-D core probe: drop a SHA-256-valid audit trail whose
        records claim ``mallory@evil.example`` + ``evil.example/mallory/
        forked.git`` + ``deadbeef...`` into ``clean_repo/.roam/audit-trail.
        jsonl`` (where the live repo is owned by ``alice@example.com``
        with origin ``example.com/alice/repo.git``). Verify must flag the
        identity mismatch -- either via an explicit issue, an explicit
        state, or by failing the gate."""
        target_chain = clean_repo / ".roam" / "audit-trail.jsonl"
        target_chain.parent.mkdir(parents=True, exist_ok=True)
        target_chain.write_text(
            foreign_audit_trail.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        r = _invoke(cli_runner, clean_repo, ["--json", "audit-trail-verify"])
        data = _parse_json(r)
        summary = data.get("summary") or {}
        issues = data.get("issues") or []
        joined = (" | ".join(str(i.get("issue", "")) for i in issues)).lower()
        # The fix would produce ANY of these signals:
        identity_signal = (
            summary.get("state") != "valid"
            or summary.get("chain_valid") is False
            or summary.get("partial_success") is True
            or "identity" in joined
            or "actor" in joined
            or "repo" in joined
            or "foreign" in joined
            or "mismatch" in joined
        )
        assert identity_signal, (
            f"Pattern-1-V-D: cmd_audit_trail_verify silently accepts a "
            f"foreign-actor chain under a different repo. summary={summary}, "
            f"issues={issues}. The recorded actor "
            f"('mallory@evil.example') and origin "
            f"('evil.example/mallory/forked.git') do not match the live "
            f"repo's git_actor() / git_origin_url() -- but the verifier "
            f"never compares them."
        )

    def test_cross_repo_audit_trail_identity_check(self, clean_repo, foreign_audit_trail, cli_runner):
        """CP45 lineage rule: the verify envelope should disclose BOTH
        sides of the identity check -- the recorded identity (from the
        chain) AND the live identity (from the live git config). Today
        no such field appears in the envelope, so an agent reading the
        envelope cannot tell whether identity was checked at all."""
        target_chain = clean_repo / ".roam" / "audit-trail.jsonl"
        target_chain.parent.mkdir(parents=True, exist_ok=True)
        target_chain.write_text(
            foreign_audit_trail.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        r = _invoke(cli_runner, clean_repo, ["--json", "audit-trail-verify"])
        data = _parse_json(r)
        keys = set(data.keys()) | set((data.get("summary") or {}).keys())
        identity_keys = {
            "live_actor",
            "live_repo",
            "live_git_sha",
            "recorded_actor",
            "recorded_repo",
            "recorded_git_sha",
            "actor_match",
            "repo_match",
            "identity_lineage",
            "identity_mismatch",
            "identity_check",
        }
        overlap = identity_keys & keys
        assert overlap, (
            f"CP45 lineage: cmd_audit_trail_verify envelope discloses NO "
            f"recorded-vs-live identity field. Looked for one of "
            f"{sorted(identity_keys)}; envelope/summary had {sorted(keys)}."
        )


# ---------------------------------------------------------------------------
# Advisory probe (passing today) -- documents the current foreign-chain-
# pass semantics so the fix is verifiably additive (the failure modes the
# fix preserves are the SHA-256 chain checks above).
# ---------------------------------------------------------------------------


def test_foreign_chain_today_verifies_valid_when_hash_chain_intact(clean_repo, foreign_audit_trail, cli_runner):
    """Documents the today-shape that the bug pin above asserts a fix
    would flip. When the bug is fixed, this test will need updating (or
    removal) -- the fix must produce ``state != 'valid'`` here. For now
    it pins the current silent-acceptance failure mode, providing a
    positive-test baseline for the xfail-strict pin's complementary
    assertion.
    """
    target_chain = clean_repo / ".roam" / "audit-trail.jsonl"
    target_chain.parent.mkdir(parents=True, exist_ok=True)
    target_chain.write_text(
        foreign_audit_trail.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    r = _invoke(cli_runner, clean_repo, ["--json", "audit-trail-verify"])
    assert r.exit_code in (0, 5), r.output
    data = _parse_json(r)
    if data["summary"]["state"] == "valid":
        # Today's shape -- foreign identity silently accepted because the
        # SHA-256 chain is internally consistent.
        assert data["summary"]["chain_valid"] is True
    else:
        # The fix has landed -- emit a clear marker. The xfail-strict
        # test above will flip to xpass and force test-failure handling.
        pytest.skip(
            "foreign-chain verify no longer silently passes -- the W805-UUUU "
            "bug appears to be fixed. Unwrap the xfail above."
        )
