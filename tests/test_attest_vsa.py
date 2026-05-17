"""Tests for the W451 SLSA SRC-L3 VSA wrapper.

Covers:

* :mod:`roam.attest.vsa` predicate + statement builders (pure, no I/O).
* The ``--slsa-l3`` flag on ``roam pr-bundle emit``: end-to-end emit
  path with cosign mocked so the test never invokes the real cosign
  binary.

Hash-stability discipline: these tests MUST NOT touch ChangeEvidence
itself - they consume it read-only. The 31 fixtures in
``tests/test_evidence_schema_migration.py`` stay byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roam.attest.vsa import (
    RUN_LEDGER_ROOT_PREDICATE_TYPE,
    SLSA_VERSION,
    SLSA_VSA_PREDICATE_TYPE,
    STATEMENT_TYPE,
    build_run_ledger_root_predicate,
    build_run_ledger_root_statement,
    build_vsa_predicate,
    build_vsa_statement,
)
from roam.evidence.change_evidence import ChangeEvidence
from roam.evidence.refs import ActorRef, AuthorityRef
from roam.evidence.subject import EvidenceSubject

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_evidence(**overrides) -> ChangeEvidence:
    """Build a small but structurally valid ChangeEvidence for VSA tests."""
    kwargs: dict = {
        "evidence_id": "ev_test_w451",
        "repo_id": "https://github.com/example/example",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "diff_hash": "deadbeef" * 8,
        "verdict": "PASS",
        "risk_level": "low",
        "agent_id": "claude",
        "actor_refs": (ActorRef(actor_kind="agent", actor_id="claude", trust_tier="self_reported_agent"),),
        "authority_refs": (AuthorityRef(authority_kind="mode", authority_id="safe_edit"),),
        "changed_subjects": (EvidenceSubject(kind="file", qualified_name="src/foo.py"),),
        "findings": ({"rule_id": "test-rule", "severity": "low", "subject_kind": "file"},),
        "tests_run": ({"test_id": "test_foo", "passed": True},),
        "policy_decisions": ({"rule_id": "policy-x", "decision": "pass"},),
        "constitution_hash": "c0" * 32,
        "rules_config_hash": "ab" * 32,
        "roam_version": "12.4.0",
    }
    kwargs.update(overrides)
    return ChangeEvidence(**kwargs).with_content_hash()


# ---------------------------------------------------------------------------
# Predicate builder tests
# ---------------------------------------------------------------------------


class TestBuildVsaPredicate:
    def test_predicate_has_required_slsa_fields(self):
        ev = _minimal_evidence()
        pred = build_vsa_predicate(ev)
        # SLSA VSA v1 mandatory fields per
        # https://slsa.dev/spec/v1.0/verification_summary
        for field in (
            "verifier",
            "timeVerified",
            "resourceUri",
            "policy",
            "inputAttestations",
            "verificationResult",
            "verifiedLevels",
            "slsaVersion",
        ):
            assert field in pred, f"missing required SLSA VSA field: {field}"

    def test_slsa_version_matches_constant(self):
        pred = build_vsa_predicate(_minimal_evidence())
        assert pred["slsaVersion"] == SLSA_VERSION

    def test_resource_uri_carries_git_plus_commit(self):
        ev = _minimal_evidence()
        pred = build_vsa_predicate(ev)
        assert pred["resourceUri"].startswith("git+")
        assert ev.commit_sha in pred["resourceUri"]

    def test_resource_uri_falls_back_to_urn_without_repo(self):
        ev = _minimal_evidence(repo_id=None, commit_sha=None)
        pred = build_vsa_predicate(ev)
        assert pred["resourceUri"].startswith("urn:roam:evidence:")

    def test_full_evidence_passes_at_level_3(self):
        ev = _minimal_evidence()
        pred = build_vsa_predicate(ev)
        # The minimal-evidence fixture populates all 6 assurance axes,
        # so the floor passes and we map to SLSA_SOURCE_LEVEL_3.
        assert pred["verifiedLevels"] == ["SLSA_SOURCE_LEVEL_3"]
        assert pred["verificationResult"] == "PASSED"

    def test_high_risk_forces_failed_verification(self):
        ev = _minimal_evidence(risk_level="critical")
        pred = build_vsa_predicate(ev)
        assert pred["verificationResult"] == "FAILED"

    def test_missing_axes_demote_to_level_1(self):
        ev = ChangeEvidence(
            evidence_id="ev_thin",
            commit_sha="aa" * 20,
        ).with_content_hash()
        pred = build_vsa_predicate(ev)
        assert pred["verifiedLevels"] == ["SLSA_SOURCE_LEVEL_1"]
        assert pred["verificationResult"] == "FAILED"

    def test_input_attestations_reference_change_evidence(self):
        ev = _minimal_evidence()
        pred = build_vsa_predicate(ev)
        inputs = pred["inputAttestations"]
        assert inputs, "inputAttestations should never be empty"
        # First entry references the ChangeEvidence packet itself.
        assert inputs[0]["uri"].startswith("urn:roam:evidence:")
        # The digest must carry the packet's content_hash.
        assert inputs[0]["digest"].get("sha256") == ev.content_hash

    def test_policy_uri_synthesised_from_hashes(self):
        ev = _minimal_evidence()
        pred = build_vsa_predicate(ev)
        uri = pred["policy"]["uri"]
        # Synthesised URI contains both hash prefixes.
        assert ev.constitution_hash[:12] in uri
        assert ev.rules_config_hash[:12] in uri

    def test_explicit_policy_uri_wins_over_synthesis(self):
        ev = _minimal_evidence()
        custom = "https://example.com/policies/v1"
        pred = build_vsa_predicate(ev, policy_uri=custom)
        assert pred["policy"]["uri"] == custom

    def test_verifier_carries_roam_version(self):
        ev = _minimal_evidence(roam_version="9.9.9")
        pred = build_vsa_predicate(ev)
        assert pred["verifier"]["version"]["roam-code"] == "9.9.9"


# ---------------------------------------------------------------------------
# Statement (in-toto v1) wrapper tests
# ---------------------------------------------------------------------------


class TestBuildVsaStatement:
    def test_statement_shape_matches_in_toto_v1(self):
        ev = _minimal_evidence()
        stmt = build_vsa_statement(ev)
        assert stmt["_type"] == STATEMENT_TYPE
        assert stmt["predicateType"] == SLSA_VSA_PREDICATE_TYPE
        assert isinstance(stmt["subject"], list) and len(stmt["subject"]) == 1
        assert isinstance(stmt["predicate"], dict)

    def test_subject_digest_carries_sha1_and_sha256(self):
        ev = _minimal_evidence()
        stmt = build_vsa_statement(ev)
        digest = stmt["subject"][0]["digest"]
        assert digest["sha1"] == ev.commit_sha
        assert digest["sha256"] == ev.content_hash

    def test_statement_serialises_to_deterministic_json(self):
        ev = _minimal_evidence()
        s1 = json.dumps(build_vsa_statement(ev), sort_keys=True, separators=(",", ":"))
        s2 = json.dumps(build_vsa_statement(ev), sort_keys=True, separators=(",", ":"))
        # timeVerified is sourced from ev.completed_at (None here), so
        # the only floating field is the synthesised _utc_now_iso() at
        # second granularity. Same-second invocations should match;
        # if this proves flaky, freeze completed_at on the fixture.
        if s1 != s2:
            # Allow at most one field to drift (timeVerified). Strip it
            # and compare; the rest must be identical.
            j1 = json.loads(s1)
            j2 = json.loads(s2)
            j1["predicate"].pop("timeVerified", None)
            j2["predicate"].pop("timeVerified", None)
            assert json.dumps(j1, sort_keys=True) == json.dumps(j2, sort_keys=True)

    def test_subject_name_matches_resource_uri(self):
        ev = _minimal_evidence()
        stmt = build_vsa_statement(ev)
        assert stmt["subject"][0]["name"] == stmt["predicate"]["resourceUri"]


# ---------------------------------------------------------------------------
# Run-ledger HMAC root attestation
# ---------------------------------------------------------------------------


class TestRunLedgerRootStatement:
    def test_predicate_has_required_fields(self):
        pred = build_run_ledger_root_predicate(
            run_id="run_abc",
            final_signature="ff" * 32,
            event_count=42,
            agent="claude",
        )
        for field in ("schema_version", "run_id", "final_signature", "event_count", "signature_algorithm"):
            assert field in pred
        assert pred["signature_algorithm"] == "hmac-sha256"

    def test_optional_fields_omitted_when_none(self):
        pred = build_run_ledger_root_predicate(run_id="r1", final_signature="aa" * 32, event_count=1)
        # No agent / status / etc. fields when not supplied.
        assert "agent" not in pred
        assert "status" not in pred
        assert "started_at" not in pred

    def test_build_statement_returns_none_when_run_unknown(self, tmp_path):
        result = build_run_ledger_root_statement(tmp_path, "nonexistent_run")
        assert result is None

    def test_build_statement_returns_none_when_chain_unsigned(self, tmp_path, monkeypatch):
        """Empty / unsigned chain (no final_signature on meta.json) returns
        None - we refuse to emit a fake root attestation."""

        class _StubMeta:
            run_id = "r_unsigned"
            agent = "claude"
            started_at = "2026-05-14T00:00:00+00:00"
            ended_at = None
            status = "in_progress"
            final_signature = None  # unsigned
            event_count = 0

        monkeypatch.setattr("roam.runs.ledger.read_run_meta", lambda root, run_id: _StubMeta())
        result = build_run_ledger_root_statement(tmp_path, "r_unsigned")
        assert result is None

    def test_build_statement_succeeds_with_signed_chain(self, tmp_path, monkeypatch):
        class _StubMeta:
            run_id = "r_signed"
            agent = "claude"
            started_at = "2026-05-14T00:00:00+00:00"
            ended_at = "2026-05-14T00:05:00+00:00"
            status = "completed"
            final_signature = "ab" * 32
            event_count = 7

        monkeypatch.setattr("roam.runs.ledger.read_run_meta", lambda root, run_id: _StubMeta())
        stmt = build_run_ledger_root_statement(tmp_path, "r_signed")
        assert stmt is not None
        assert stmt["_type"] == STATEMENT_TYPE
        assert stmt["predicateType"] == RUN_LEDGER_ROOT_PREDICATE_TYPE
        subject = stmt["subject"][0]
        assert subject["name"] == "urn:roam:run:r_signed"
        assert subject["digest"]["sha256"] == "ab" * 32
        assert stmt["predicate"]["final_signature"] == "ab" * 32
        assert stmt["predicate"]["event_count"] == 7
        assert stmt["predicate"]["agent"] == "claude"
        assert stmt["predicate"]["status"] == "completed"


# ---------------------------------------------------------------------------
# Hash-stability guard: importing the VSA module must not perturb
# ChangeEvidence content hashes.
# ---------------------------------------------------------------------------


class TestHashStability:
    def test_change_evidence_round_trips_after_vsa_import(self):
        """W451 contract: VSA is additive. Building a VSA from a
        ChangeEvidence MUST NOT mutate the packet's canonical JSON
        or content hash."""
        ev = _minimal_evidence()
        original_hash = ev.content_hash
        original_canonical = ev.to_canonical_json()
        _ = build_vsa_statement(ev)
        # Nothing should have changed about the source packet.
        assert ev.content_hash == original_hash
        assert ev.to_canonical_json() == original_canonical
        # And re-computing the hash must reproduce it.
        assert ev.compute_content_hash() == original_hash


# ---------------------------------------------------------------------------
# pr-bundle emit --slsa-l3 (cosign mocked)
# ---------------------------------------------------------------------------


class TestPrBundleEmitSlsaL3:
    """End-to-end test of the --slsa-l3 flag on pr-bundle emit.

    Strategy: stub :func:`cosign_sign_statement` so we exercise the
    wiring without needing cosign on PATH. The pr-bundle envelope is
    built by hand via the actual CLI (init + add intent + add affected +
    add context-cmd + add tests + emit), then we inspect the on-disk
    VSA file the emit path wrote.
    """

    @pytest.fixture(autouse=True)
    def _enforcement_safe(self, monkeypatch):
        monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")

    def test_emit_writes_vsa_statement(self, tmp_path, monkeypatch):
        # Initialise a minimal git repo so the pr-bundle producer
        # captures commit metadata.
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(tmp_path),
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(tmp_path), check=True)
        (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "init"],
            cwd=str(tmp_path),
            check=True,
        )

        # Create a minimal bundle on disk so emit has something to
        # finalise. We hand-craft it to avoid the full init->emit
        # sequence (the producer wiring is exercised in test_pr_bundle.py).
        bundle_dir = tmp_path / ".roam" / "pr-bundles"
        bundle_dir.mkdir(parents=True)
        # Find the current branch (Git defaults differ: main / master).
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        bundle = {
            "intent": "demo intent",
            "context_cmd": "roam preflight f",
            "affected_symbols": [{"symbol": "f", "kind": "function", "file": "a.py"}],
            "tests": [{"test_id": "t1", "passed": True}],
            "verdict": "PASS",
            "risk_level": "low",
        }
        (bundle_dir / f"{branch}.json").write_text(json.dumps(bundle), encoding="utf-8")

        # Patch cosign so the signing path is invocation-free.
        from roam.attest import cga as cga_mod

        monkeypatch.setattr(cga_mod, "cosign_available", lambda: (False, ""))

        # Run pr-bundle emit --slsa-l3 (no --sign because we want the
        # additive emit-only path to also work).
        from click.testing import CliRunner

        from roam.cli import cli

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "pr-bundle", "emit", "--no-auto-collect", "--slsa-l3"],
        )
        # The emit path returns exit 0 (we did not pass --strict).
        assert result.exit_code == 0, result.output

        # Parse the emitted envelope and inspect the slsa_l3 block.
        payload = json.loads(result.output)
        assert "slsa_l3" in payload, (
            f"pr-bundle emit --slsa-l3 must surface a slsa_l3 block on the envelope; got keys: {sorted(payload.keys())}"
        )
        slsa_block = payload["slsa_l3"]
        assert slsa_block["predicate_type"] == SLSA_VSA_PREDICATE_TYPE
        assert slsa_block["vsa_path"], slsa_block
        vsa_path = Path(slsa_block["vsa_path"])
        assert vsa_path.exists(), f"VSA file not written: {vsa_path}"

        # The on-disk VSA must be a structurally valid in-toto v1
        # SLSA VSA Statement.
        vsa = json.loads(vsa_path.read_text(encoding="utf-8"))
        assert vsa["_type"] == STATEMENT_TYPE
        assert vsa["predicateType"] == SLSA_VSA_PREDICATE_TYPE
        assert vsa["predicate"]["slsaVersion"] == SLSA_VERSION
        assert "verificationResult" in vsa["predicate"]
        assert "verifiedLevels" in vsa["predicate"]

    def test_emit_signs_when_sign_flag_set(self, tmp_path, monkeypatch):
        """With --slsa-l3 --sign --keyless, the cosign wrapper must be
        invoked (mocked) and the signed=True path land on the envelope."""
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(tmp_path),
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(tmp_path), check=True)
        (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)

        bundle_dir = tmp_path / ".roam" / "pr-bundles"
        bundle_dir.mkdir(parents=True)
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        bundle = {
            "intent": "demo",
            "context_cmd": "roam preflight f",
            "affected_symbols": [{"symbol": "f", "kind": "function", "file": "a.py"}],
            "tests": [{"test_id": "t1", "passed": True}],
            "verdict": "PASS",
            "risk_level": "low",
        }
        (bundle_dir / f"{branch}.json").write_text(json.dumps(bundle), encoding="utf-8")

        # Mock cosign to "succeed" by faking the result object.
        from roam.attest import cga as cga_mod

        class _FakeResult:
            signed = True
            statement_path = bundle_dir / "stub"
            signature_path = bundle_dir / "stub.sig"
            certificate_path = bundle_dir / "stub.cert"
            bundle_path = bundle_dir / "stub.bundle"
            skipped_reason = ""
            cosign_version = "v2.4.0-mock"

        monkeypatch.setattr(
            cga_mod,
            "cosign_sign_statement",
            lambda statement_path, **kw: _FakeResult(),
        )

        from click.testing import CliRunner

        from roam.cli import cli

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "pr-bundle",
                "emit",
                "--no-auto-collect",
                "--slsa-l3",
                "--sign",
                "--keyless",
            ],
        )
        assert result.exit_code == 0, result.output

        payload = json.loads(result.output)
        slsa_block = payload["slsa_l3"]
        assert slsa_block["signed"] is True
        assert any(sig["target"] == "vsa" and sig["signed"] for sig in slsa_block["signatures"]), slsa_block[
            "signatures"
        ]

    def test_emit_no_auto_collect_falls_back_to_git_rev_parse_head(self, tmp_path, monkeypatch):
        """W509 — when the bundle envelope omits ``commit_sha`` (typical on
        ``--no-auto-collect`` runs that hand-craft a minimal bundle), the
        VSA emit path must fall back to ``git rev-parse HEAD`` so
        ``subject[0].digest.sha1`` anchors the SLSA VSA to the actual
        commit. Before W509 sha1 was dropped silently, partially breaking
        the SRC-L3 "commit-anchored provenance" claim.
        """
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(tmp_path),
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(tmp_path),
            check=True,
        )
        (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)

        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert len(head_sha) == 40, head_sha

        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        # Minimal bundle WITHOUT commit_sha — the bug condition.
        bundle_dir = tmp_path / ".roam" / "pr-bundles"
        bundle_dir.mkdir(parents=True)
        bundle = {
            "intent": "w509 demo",
            "context_cmd": "roam preflight f",
            "affected_symbols": [{"symbol": "f", "kind": "function", "file": "a.py"}],
            "tests": [{"test_id": "t1", "passed": True}],
            "verdict": "PASS",
            "risk_level": "low",
        }
        (bundle_dir / f"{branch}.json").write_text(json.dumps(bundle), encoding="utf-8")
        # Sanity: the envelope source has no commit_sha.
        assert "commit_sha" not in bundle

        from roam.attest import cga as cga_mod

        monkeypatch.setattr(cga_mod, "cosign_available", lambda: (False, ""))

        from click.testing import CliRunner

        from roam.cli import cli

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "pr-bundle", "emit", "--no-auto-collect", "--slsa-l3"],
        )
        assert result.exit_code == 0, result.output

        payload = json.loads(result.output)
        slsa_block = payload["slsa_l3"]
        vsa = json.loads(Path(slsa_block["vsa_path"]).read_text(encoding="utf-8"))
        subj_digest = vsa["subject"][0]["digest"]
        assert subj_digest.get("sha1") == head_sha, (
            "W509: pr-bundle emit --slsa-l3 --no-auto-collect must fall "
            "back to `git rev-parse HEAD` for subject[0].digest.sha1; "
            f"got {subj_digest!r}, expected sha1={head_sha!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 regression: ROAM_RUN_ID="   " must NOT reach read_run_meta
# (whitespace normalisation parity between pr-bundle and cga sibling paths)
# ---------------------------------------------------------------------------


class TestEmitPrBundleSlsaL3RunIdNormalisation:
    """Pattern-2 silent-fallback guard for ``emit_pr_bundle_slsa_l3``.

    A whitespace-only ``ROAM_RUN_ID`` (``"   "``) is malformed; before
    the normalisation it passed the ``if run_id:`` truthy check and
    reached ``read_run_meta``, which returned ``None`` because the
    bogus run-id maps to no directory. The emit path then surfaced a
    misleading ``"run-ledger HMAC chain not signed"`` skip reason —
    the chain isn't unsigned, the env var is just malformed.

    The cga sibling path always normalised via ``.strip() or None`` on
    its hash-kwargs path; this test pins the parity for the pr-bundle
    path too.
    """

    def test_whitespace_run_id_routes_to_not_set_reason(self, tmp_path, monkeypatch):
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(tmp_path),
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(tmp_path), check=True)
        (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)

        from roam.attest.emit_vsa import emit_pr_bundle_slsa_l3

        monkeypatch.setenv("ROAM_RUN_ID", "   ")  # whitespace only

        # Sentinel: if normalisation ever regresses and the whitespace
        # reaches read_run_meta, this monkeypatch surfaces it.
        seen_run_ids: list[str] = []
        import roam.runs.ledger as ledger_mod

        original = ledger_mod.read_run_meta

        def _spy(repo_root, run_id):
            seen_run_ids.append(run_id)
            return original(repo_root, run_id)

        monkeypatch.setattr(ledger_mod, "read_run_meta", _spy)

        envelope = {
            "intent": "demo",
            "context_cmd": "roam preflight f",
            "affected_symbols": [],
            "tests": [],
            "verdict": "PASS",
            "risk_level": "low",
        }
        result = emit_pr_bundle_slsa_l3(
            root=tmp_path,
            envelope=envelope,
            sign=False,
            sign_key=None,
            sign_keyless=False,
        )

        # Normalised whitespace MUST NOT reach read_run_meta. Other
        # callers (e.g. W1279 ``gather_hash_kwargs``) may legitimately
        # enumerate stored runs in this process, so filter the spy
        # records to whitespace-only entries — those are the only ones
        # that prove the regression.
        whitespace_only = [r for r in seen_run_ids if r != r.strip()]
        assert whitespace_only == [], (
            f"ROAM_RUN_ID='   ' leaked to read_run_meta: {whitespace_only!r}"
        )
        # The "not set" skip reason wins over the misleading
        # "chain not signed" one.
        assert any("ROAM_RUN_ID not set" in r for r in result["skipped_reasons"]), result[
            "skipped_reasons"
        ]
        assert not any(
            "HMAC chain not signed" in r for r in result["skipped_reasons"]
        ), f"Pattern-2 regression: whitespace ROAM_RUN_ID surfaced misleading chain-not-signed reason: {result['skipped_reasons']!r}"


# ---------------------------------------------------------------------------
# W472 - `roam cga emit --also-vsa` flag (follow-up to W451)
# ---------------------------------------------------------------------------


class TestCgaEmitAlsoVsa:
    """`roam cga emit --also-vsa` writes a sibling SLSA VSA next to the CGA.

    Teams that use `roam cga emit` directly (not `pr-bundle emit
    --slsa-l3`) get parity with the SLSA-shaped output by reusing the
    same `build_vsa_statement()` projection from W451.
    """

    @pytest.fixture(autouse=True)
    def _enforcement_safe(self, monkeypatch):
        monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")

    def _setup_repo(self, tmp_path):
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(tmp_path),
            check=True,
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(tmp_path), check=True)
        (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)

    def test_emit_also_vsa_produces_sibling_file(self, tmp_path, monkeypatch):
        """`cga emit --also-vsa` writes <stem>.vsa.json next to the CGA."""
        from click.testing import CliRunner

        from roam.cli import cli

        self._setup_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        # Index first so `cga emit` has something to attest over.
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        cga_out = tmp_path / ".roam" / "attestations" / "test.intoto.json"
        cga_out.parent.mkdir(parents=True, exist_ok=True)
        result = runner.invoke(
            cli,
            ["--json", "cga", "emit", "--output", str(cga_out), "--also-vsa", "--allow-dirty"],
        )
        assert result.exit_code == 0, result.output

        payload = json.loads(result.output)
        assert payload["summary"]["vsa_emitted"] is True
        vsa_result = payload["vsa_result"]
        assert vsa_result["predicate_type"] == SLSA_VSA_PREDICATE_TYPE
        vsa_path = Path(vsa_result["vsa_path"])
        # Sibling naming: <stem>.vsa.json (NOT <stem>.intoto.vsa.json).
        assert vsa_path.name == "test.vsa.json", vsa_path
        assert vsa_path.parent == cga_out.parent
        assert vsa_path.exists()

        vsa = json.loads(vsa_path.read_text(encoding="utf-8"))
        assert vsa["_type"] == STATEMENT_TYPE
        assert vsa["predicateType"] == SLSA_VSA_PREDICATE_TYPE
        assert vsa["predicate"]["slsaVersion"] == SLSA_VERSION
        assert "verificationResult" in vsa["predicate"]
        assert "verifiedLevels" in vsa["predicate"]

    def test_emit_also_vsa_matches_pr_bundle_slsa_l3_on_same_evidence(self):
        """Byte-identical VSA shape: same ChangeEvidence -> same VSA.

        We don't drive both CLIs end-to-end (they collect from different
        envelope shapes); instead we assert the underlying primitive
        both code paths share - :func:`build_vsa_statement` - is
        deterministic on the same ChangeEvidence packet. The cmd_cga
        and cmd_pr_bundle wire-ups both call it via the same
        :func:`collect_change_evidence` -> :func:`build_vsa_statement`
        flow, so this guards the parity contract.
        """
        from roam.attest.cga import serialize_statement

        ev = _minimal_evidence()
        a = build_vsa_statement(ev)
        b = build_vsa_statement(ev)
        assert serialize_statement(a) == serialize_statement(b)
        # Statement subjects, predicate type, slsaVersion, and the
        # verified-levels mapping are all derived from ChangeEvidence
        # alone, so re-projecting the same evidence yields identical
        # canonical JSON regardless of which CLI emitted it.
        assert a == b

    def test_emit_also_vsa_with_sign_keyless_signs_both(self, tmp_path, monkeypatch):
        """`cga emit --also-vsa --sign --keyless` cosign-signs BOTH the
        CGA AND the VSA (mocked cosign)."""
        from click.testing import CliRunner

        from roam.attest import cga as cga_mod
        from roam.cli import cli

        self._setup_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        # Mock cosign_sign_statement to record per-target invocations
        # and return a success-shaped result for both.
        signed_targets: list[Path] = []

        class _FakeResult:
            def __init__(self, target_path: Path):
                self.signed = True
                self.statement_path = target_path
                self.signature_path = target_path.with_suffix(target_path.suffix + ".sig")
                self.certificate_path = target_path.with_suffix(target_path.suffix + ".cert")
                self.bundle_path = target_path.with_suffix(target_path.suffix + ".bundle")
                self.skipped_reason = ""
                self.cosign_version = "v2.4.0-mock"

        def _fake_sign(statement_path, **kw):
            signed_targets.append(Path(statement_path))
            return _FakeResult(Path(statement_path))

        # Patch BOTH module bindings - cmd_cga imports cosign_sign_statement
        # at the top of the module, AND _emit_vsa_sibling closes over
        # the same name. Patching the source module covers _emit_vsa_sibling
        # only because the helper imports via the top-level cmd_cga
        # binding (`from roam.attest.cga import cosign_sign_statement`),
        # so we patch the cmd_cga rebinding too.
        from roam.commands import cmd_cga as cmd_cga_mod

        monkeypatch.setattr(cga_mod, "cosign_sign_statement", _fake_sign)
        monkeypatch.setattr(cmd_cga_mod, "cosign_sign_statement", _fake_sign)

        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0

        cga_out = tmp_path / ".roam" / "attestations" / "signed.intoto.json"
        cga_out.parent.mkdir(parents=True, exist_ok=True)
        result = runner.invoke(
            cli,
            [
                "--json",
                "cga",
                "emit",
                "--output",
                str(cga_out),
                "--also-vsa",
                "--sign",
                "--keyless",
                "--allow-dirty",
            ],
        )
        assert result.exit_code == 0, result.output

        payload = json.loads(result.output)
        # CGA sign result -> signed.
        assert payload["sign_result"]["signed"] is True
        # VSA sign result -> signed.
        vsa_sign = payload["vsa_result"]["sign_result"]
        assert vsa_sign is not None
        assert vsa_sign["signed"] is True
        # And cosign was called TWICE (once per target).
        assert len(signed_targets) == 2, signed_targets
        target_names = sorted(p.name for p in signed_targets)
        assert target_names == ["signed.intoto.json", "signed.vsa.json"]

    def test_emit_cga_vsa_sibling_falls_back_to_git_rev_parse_head(self, tmp_path, monkeypatch):
        """W520 — parallel to W509 but for the cga-side helper.

        Direct-API callers of ``emit_cga_vsa_sibling`` can hand-craft a
        statement whose ``subject[0].digest`` lacks ``git_commit_sha1``
        (rare but possible via direct API use that bypasses
        ``roam cga emit``). Before W520 the resulting sibling VSA dropped
        ``subject[0].digest.sha1`` with no fallback. After W520 the helper
        falls back to ``git rev-parse HEAD`` against ``project_root``
        so commit-anchored provenance survives.
        """
        import subprocess

        from roam.attest.emit_vsa import emit_cga_vsa_sibling

        self._setup_repo(tmp_path)

        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert len(head_sha) == 40, head_sha

        # Hand-crafted CGA statement WITHOUT git_commit_sha1 in the
        # subject digest — the W520 bug condition.
        cga_dir = tmp_path / ".roam" / "attestations"
        cga_dir.mkdir(parents=True, exist_ok=True)
        cga_path = cga_dir / "w520.intoto.json"
        statement = {
            "_type": STATEMENT_TYPE,
            "predicateType": "https://roam.dev/cga/v1",
            "subject": [
                {
                    "name": "https://github.com/example/example",
                    "digest": {},  # no git_commit_sha1
                }
            ],
            "predicate": {"verdict": "PASS"},
        }
        cga_path.write_text(json.dumps(statement), encoding="utf-8")

        result = emit_cga_vsa_sibling(
            statement=statement,
            written_path=cga_path,
            written_to="file",
            no_write=False,
            project_root=tmp_path,
            sign=False,
            key_path=None,
            keyless=False,
        )

        assert result["vsa_path"] is not None, result
        vsa = json.loads(Path(result["vsa_path"]).read_text(encoding="utf-8"))
        subj_digest = vsa["subject"][0]["digest"]
        assert subj_digest.get("sha1") == head_sha, (
            "W520: emit_cga_vsa_sibling must fall back to "
            "`git rev-parse HEAD` for subject[0].digest.sha1 when the "
            "parent CGA subject lacks git_commit_sha1; "
            f"got {subj_digest!r}, expected sha1={head_sha!r}"
        )


# ---------------------------------------------------------------------------
# W498 - end-to-end VSA CLI parity (both producers, same workspace)
# ---------------------------------------------------------------------------


class TestVsaCliParity:
    """End-to-end W486 parity: drive BOTH ``pr-bundle emit --slsa-l3`` AND
    ``cga emit --also-vsa`` against the same workspace, then diff the
    on-disk ``.vsa.json`` files.

    The existing W472 parity test (``test_emit_also_vsa_matches_pr_bundle_slsa_l3_on_same_evidence``)
    only re-derives ``build_vsa_statement`` twice from the *same*
    ChangeEvidence. That guards the pure projection but not the wire-up.
    This test exercises the full CLI surface on both paths so a future
    drift in either ``cmd_cga._emit_vsa_sibling`` or
    ``cmd_pr_bundle._emit_slsa_l3_attestations`` produces a visible
    mismatch.

    Known legitimate divergences (documented in the assertions below):

    * ``predicate.timeVerified`` - each emit stamps its own
      ``_utc_now_iso()`` (second-granularity wall clock).
    * ``predicate.inputAttestations[0]`` - the ``urn:roam:evidence:<id>``
      and ``digest.sha256`` reflect each path's distinct
      ``ChangeEvidence.content_hash``. The two paths feed DIFFERENT
      envelope sources to ``collect_change_evidence`` (pr-bundle feeds a
      bundle dict; cga feeds the just-emitted CGA statement), so the
      packets carry different findings/tests/etc. and hash differently
      by design.
    * ``predicate.verificationResult`` / ``predicate.verifiedLevels`` -
      these are functions of ``assurance_floor()``, which is a function
      of the populated axes on the ChangeEvidence. Same axis-divergence
      as above.
    * ``subject[0].digest.sha256`` - same content_hash divergence.
    * ``subject[0].digest.sha1`` - W509 sealed the prior divergence:
      both paths now resolve commit identity via ``git rev-parse HEAD``
      when the upstream envelope omits ``commit_sha``. The cga path
      derives it directly inside ``build_cga_statement``; the pr-bundle
      path falls back to the same git probe inside
      ``emit_pr_bundle_slsa_l3`` when the bundle envelope lacks a
      ``commit_sha`` (e.g. ``--no-auto-collect`` runs). Inside a git
      repo the two sha1 values MUST agree byte-for-byte. Outside a git
      repo BOTH sides degrade to absent (no false-positive sha1).
    * ``subject[0].name`` / ``predicate.resourceUri`` - AGREE when both
      collectors resolve the same ``repo_id`` + ``commit_sha``. In an
      ephemeral ``git init`` workspace neither collector knows a remote
      repo_id; the cga path uses the workspace-root URI as repo_id while
      the pr-bundle path falls back to ``urn:roam:evidence:<id>``.

    Fields that MUST agree on the same workspace + same roam version:

    * ``_type`` (in-toto v1 statement type)
    * ``predicateType`` (SLSA VSA v1 predicate type URI)
    * ``predicate.slsaVersion``
    * ``predicate.verifier.id`` (constant ``https://roam-code.com``)
    * ``predicate.verifier.version.roam-code`` (read from the same
      ``importlib.metadata`` lookup)
    * ``predicate.policy.uri`` - synthesised from
      ``constitution_hash`` + ``rules_config_hash``; both collectors
      resolve those from the same on-disk substrate, so the URIs MUST
      match.
    """

    @pytest.fixture(autouse=True)
    def _enforcement_safe(self, monkeypatch):
        monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")

    def _setup_repo(self, tmp_path: Path) -> str:
        """Initialise a git repo with one committed Python file. Returns
        the current branch name (Git defaults differ: main / master).
        """
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(tmp_path),
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(tmp_path),
            check=True,
        )
        (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "init"],
            cwd=str(tmp_path),
            check=True,
        )
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return branch

    def test_pr_bundle_and_cga_emit_byte_identical_invariants(self, tmp_path, monkeypatch):
        """Run both CLIs against the same workspace, assert invariant
        fields are byte-identical and document the divergent fields.
        """
        from click.testing import CliRunner

        from roam.attest import cga as cga_mod
        from roam.cli import cli

        branch = self._setup_repo(tmp_path)
        monkeypatch.chdir(tmp_path)

        # Hand-craft a pr-bundle envelope so pr-bundle emit has something
        # to finalise. Mirrors the fixture in TestPrBundleEmitSlsaL3 above.
        bundle_dir = tmp_path / ".roam" / "pr-bundles"
        bundle_dir.mkdir(parents=True)
        bundle = {
            "intent": "parity demo",
            "context_cmd": "roam preflight f",
            "affected_symbols": [{"symbol": "f", "kind": "function", "file": "a.py"}],
            "tests": [{"test_id": "t1", "passed": True}],
            "verdict": "PASS",
            "risk_level": "low",
        }
        (bundle_dir / f"{branch}.json").write_text(json.dumps(bundle), encoding="utf-8")

        # Mock cosign so neither path tries to sign.
        monkeypatch.setattr(cga_mod, "cosign_available", lambda: (False, ""))

        runner = CliRunner()

        # 1. pr-bundle emit --slsa-l3 (no --sign).
        result_pb = runner.invoke(
            cli,
            ["--json", "pr-bundle", "emit", "--no-auto-collect", "--slsa-l3"],
        )
        assert result_pb.exit_code == 0, result_pb.output
        payload_pb = json.loads(result_pb.output)
        pb_vsa_path = Path(payload_pb["slsa_l3"]["vsa_path"])
        assert pb_vsa_path.exists(), pb_vsa_path

        # 2. cga emit --also-vsa (no --sign). Needs an index first.
        assert runner.invoke(cli, ["index"]).exit_code == 0
        cga_out = tmp_path / ".roam" / "attestations" / "parity.intoto.json"
        cga_out.parent.mkdir(parents=True, exist_ok=True)
        result_cga = runner.invoke(
            cli,
            [
                "--json",
                "cga",
                "emit",
                "--output",
                str(cga_out),
                "--also-vsa",
                "--allow-dirty",
            ],
        )
        assert result_cga.exit_code == 0, result_cga.output
        payload_cga = json.loads(result_cga.output)
        cga_vsa_path = Path(payload_cga["vsa_result"]["vsa_path"])
        assert cga_vsa_path.exists(), cga_vsa_path

        # 3. Read both files from disk.
        pb_vsa = json.loads(pb_vsa_path.read_text(encoding="utf-8"))
        cga_vsa = json.loads(cga_vsa_path.read_text(encoding="utf-8"))

        # 4a. Invariant fields - MUST match byte-for-byte.
        assert pb_vsa["_type"] == cga_vsa["_type"]
        assert pb_vsa["predicateType"] == cga_vsa["predicateType"]

        pb_pred = pb_vsa["predicate"]
        cga_pred = cga_vsa["predicate"]
        assert pb_pred["slsaVersion"] == cga_pred["slsaVersion"]
        assert pb_pred["verifier"] == cga_pred["verifier"], "verifier block (id + version) MUST agree across CLIs"

        # Both collectors read the same constitution + rules state, so
        # the synthesised policy URI is identical.
        assert pb_pred["policy"] == cga_pred["policy"]

        # Subject digest.sha1 - W509 made this an INVARIANT across both
        # paths. Inside a git repo, both paths must resolve commit
        # identity from ``git rev-parse HEAD`` (cga directly inside
        # ``build_cga_statement``; pr-bundle via the W509 fallback in
        # ``emit_pr_bundle_slsa_l3`` when the bundle envelope omits
        # ``commit_sha``). They MUST agree byte-for-byte. This fixture
        # uses ``--no-auto-collect`` with a hand-crafted bundle (no
        # ``commit_sha`` field) — the prior contract documented this
        # as a legitimate divergence; W509 made it invariant.
        pb_subj_digest = pb_vsa["subject"][0]["digest"]
        cga_subj_digest = cga_vsa["subject"][0]["digest"]
        assert cga_subj_digest.get("sha1"), f"cga path should always carry commit sha1; got {cga_subj_digest}"
        assert pb_subj_digest.get("sha1"), (
            "W509: pr-bundle path must fall back to `git rev-parse HEAD` "
            "when the bundle envelope omits commit_sha; "
            f"got {pb_subj_digest}"
        )
        assert pb_subj_digest["sha1"] == cga_subj_digest["sha1"], (
            "W509 invariant: both VSA paths must resolve identical "
            "commit sha1 inside a git repo; "
            f"pr-bundle={pb_subj_digest['sha1']!r} "
            f"cga={cga_subj_digest['sha1']!r}"
        )

        # 4b. Documented divergences - these fields MAY differ; we only
        # check that each side independently emits a structurally valid
        # value.
        for pred in (pb_pred, cga_pred):
            assert "timeVerified" in pred
            assert pred["verificationResult"] in ("PASSED", "FAILED")
            assert isinstance(pred["verifiedLevels"], list)
            assert pred["verifiedLevels"], "verifiedLevels must be non-empty"
            assert isinstance(pred["inputAttestations"], list)
            assert pred["inputAttestations"], "inputAttestations must be non-empty"

        # 4c. Byte-identical claim on a normalised projection. Strip the
        # documented-divergent fields and assert the rest of the JSON is
        # byte-equal under canonical serialisation. This is the strongest
        # end-to-end guard: if a future refactor changes ANY non-divergent
        # field in only one of the two emit paths, this assertion trips.
        DIVERGENT_PRED_FIELDS = (
            "timeVerified",
            "verificationResult",
            "verifiedLevels",
            "inputAttestations",
            "resourceUri",
        )

        def _normalise(vsa: dict) -> str:
            v = json.loads(json.dumps(vsa))  # deep copy
            for field in DIVERGENT_PRED_FIELDS:
                v["predicate"].pop(field, None)
            # subject[0].name is resourceUri-shaped; both paths fall back
            # to urn:roam:evidence:<id> in this fixture (no remote
            # repo_id), so the name carries the per-path evidence_id.
            # Strip it; we already asserted commit sha1 matches above.
            v["subject"][0].pop("name", None)
            # Drop the entire digest dict - both sha1 (commit identity,
            # may be present only on cga path) and sha256 (content_hash,
            # diverges by design) are documented-divergent. Asserted
            # individually above.
            v["subject"][0].pop("digest", None)
            return json.dumps(v, sort_keys=True, separators=(",", ":"))

        pb_norm = _normalise(pb_vsa)
        cga_norm = _normalise(cga_vsa)
        assert pb_norm == cga_norm, (
            f"Normalised VSA projections diverge across CLIs.\npr-bundle: {pb_norm}\ncga:       {cga_norm}"
        )
