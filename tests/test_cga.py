"""Tests for the Code Graph Attestation (E.1 v12.0 scaffold).

Three layers of guarantees:

1. **Determinism** — building the predicate twice on the same DB
   produces identical merkle/edge digests. Sorts and stable hashes
   are the contract.
2. **Verification** — a freshly-emitted statement verifies clean.
   Mutating the DB invalidates the digest.
3. **OpenVEX correctness** — the predicate body advertises the
   spec-legal status / justification sets. Never includes
   ``code_not_reachable``.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from click.testing import CliRunner

from roam.attest.cga import (
    PREDICATE_TYPE,
    STATEMENT_TYPE,
    _edge_bundle_digest,
    _symbol_fingerprints,
    build_cga_predicate,
    build_cga_statement,
    serialize_statement,
    verify_cga_statement,
)
from roam.cli import cli
from roam.security.taint_engine import OPENVEX_JUSTIFICATIONS, OPENVEX_STATUSES
from tests.conftest import make_src_project as _make_project


@pytest.fixture(autouse=True)
def _enforcement_safe(monkeypatch):
    """Pre-elect autonomous_pr so privileged `roam cga` works under future
    `ROAM_MODE_ENFORCEMENT` default-on (W23.3 staged-rollout PR-B). All tests
    in this file invoke `roam cga emit|verify`, which is gated under `safe_edit`."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")


# ---------------------------------------------------------------------------
# In-memory DB for unit tests
# ---------------------------------------------------------------------------


def _make_in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            qualified_name TEXT,
            kind TEXT,
            signature TEXT
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER,
            target_id INTEGER,
            kind TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO files(id, path, language) VALUES (?, ?, ?)",
        [(1, "src/a.py", "python"), (2, "src/b.py", "python"), (3, "x.js", "javascript")],
    )
    conn.executemany(
        "INSERT INTO symbols(id, file_id, name, qualified_name, kind, signature) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (10, 1, "alpha", "src.a.alpha", "function", "alpha(x)"),
            (11, 1, "Alpha", "src.a.Alpha", "class", ""),
            (20, 2, "beta", "src.b.beta", "function", "beta()"),
            (30, 3, "doStuff", "x.doStuff", "function", "doStuff()"),
        ],
    )
    conn.executemany(
        "INSERT INTO edges(source_id, target_id, kind) VALUES (?, ?, ?)",
        [(10, 20, "calls"), (11, 10, "references")],
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_symbol_fingerprints_stable(self):
        conn = _make_in_memory_db()
        m1, n1 = _symbol_fingerprints(conn)
        m2, n2 = _symbol_fingerprints(conn)
        assert m1 == m2
        assert n1 == n2 == 4

    def test_edge_digest_stable(self):
        conn = _make_in_memory_db()
        d1, c1 = _edge_bundle_digest(conn)
        d2, c2 = _edge_bundle_digest(conn)
        assert d1 == d2
        assert c1 == c2 == 2

    def test_predicate_idempotent_against_same_db(self, tmp_path):
        conn = _make_in_memory_db()
        p1 = build_cga_predicate(conn, project_root=tmp_path)
        p2 = build_cga_predicate(conn, project_root=tmp_path)
        # `indexed_at` will differ — strip and compare the rest.
        for d in (p1, p2):
            d.pop("indexed_at", None)
        assert p1 == p2


# ---------------------------------------------------------------------------
# Statement structure + OpenVEX correctness
# ---------------------------------------------------------------------------


class TestStatementStructure:
    def test_in_toto_v1_shape(self, tmp_path):
        conn = _make_in_memory_db()
        stmt = build_cga_statement(conn, project_root=tmp_path)
        assert stmt["_type"] == STATEMENT_TYPE
        assert stmt["predicateType"] == PREDICATE_TYPE
        subjects = stmt["subject"]
        assert isinstance(subjects, list) and subjects
        assert "digest" in subjects[0]
        assert "git_commit_sha1" in subjects[0]["digest"]

    def test_predicate_advertises_legal_openvex_strings(self, tmp_path):
        conn = _make_in_memory_db()
        pred = build_cga_predicate(conn, project_root=tmp_path)
        # Status set must equal the spec
        assert set(pred["openvex_status_set"]) == OPENVEX_STATUSES
        # Justification set must equal the spec — and never include the
        # forbidden v11.x string.
        assert set(pred["openvex_justification_set"]) == OPENVEX_JUSTIFICATIONS
        assert "code_not_reachable" not in pred["openvex_justification_set"]

    def test_canonical_serialisation_is_deterministic(self, tmp_path):
        conn = _make_in_memory_db()
        s1 = build_cga_statement(conn, project_root=tmp_path)
        s2 = build_cga_statement(conn, project_root=tmp_path)
        # Equal except for indexed_at — strip and compare canonical.
        s1["predicate"].pop("indexed_at", None)
        s2["predicate"].pop("indexed_at", None)
        assert serialize_statement(s1) == serialize_statement(s2)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class TestVerification:
    def test_fresh_statement_verifies_clean(self, tmp_path):
        conn = _make_in_memory_db()
        stmt = build_cga_statement(conn, project_root=tmp_path)
        ok, errors = verify_cga_statement(stmt, conn, project_root=tmp_path)
        assert ok, errors
        assert errors == []

    def test_mutated_db_fails_verification(self, tmp_path):
        conn = _make_in_memory_db()
        stmt = build_cga_statement(conn, project_root=tmp_path)
        # Mutate: add a new symbol → live merkle/symbol_count change.
        conn.execute(
            "INSERT INTO symbols(id, file_id, name, qualified_name, kind, signature) "
            "VALUES (99, 1, 'gamma', 'src.a.gamma', 'function', 'gamma()')"
        )
        conn.commit()
        ok, errors = verify_cga_statement(stmt, conn, project_root=tmp_path)
        assert not ok
        assert any("merkle_root" in e or "symbol_count" in e for e in errors)

    def test_wrong_type_rejected(self, tmp_path):
        conn = _make_in_memory_db()
        stmt = build_cga_statement(conn, project_root=tmp_path)
        stmt["_type"] = "https://example.com/wrong"
        ok, errors = verify_cga_statement(stmt, conn, project_root=tmp_path)
        assert not ok
        assert any("_type mismatch" in e for e in errors)

    def test_wrong_predicate_type_rejected(self, tmp_path):
        conn = _make_in_memory_db()
        stmt = build_cga_statement(conn, project_root=tmp_path)
        stmt["predicateType"] = "https://example.com/other/v1"
        ok, errors = verify_cga_statement(stmt, conn, project_root=tmp_path)
        assert not ok
        assert any("predicateType mismatch" in e for e in errors)

    def test_non_dict_rejected(self, tmp_path):
        conn = _make_in_memory_db()
        ok, errors = verify_cga_statement("not a dict", conn, project_root=tmp_path)
        assert not ok
        assert errors

    def test_predicate_carries_git_dirty_hash_field(self, tmp_path):
        """Newly-emitted predicates always include the dirty-hash field —
        ``None`` for a clean tree, sha256 for a dirty one.
        """
        conn = _make_in_memory_db()
        stmt = build_cga_statement(conn, project_root=tmp_path)
        assert "git_dirty_hash" in stmt["predicate"], (
            "predicate must carry git_dirty_hash so the verifier can re-derive "
            "and compare. Pre-bind statements lacked this field; new emits must "
            "always include it."
        )

    def test_dirty_hash_mismatch_fails_verification(self, tmp_path):
        """Predicate asserting clean tree, live tree dirty → refuse.

        Simulated by injecting a non-None dirty-hash into the predicate and
        verifying against a clean (non-git) ``project_root``. The verifier
        sees predicate=dirty, live=None and emits a mismatch error.
        """
        conn = _make_in_memory_db()
        stmt = build_cga_statement(conn, project_root=tmp_path)
        stmt["predicate"]["git_dirty_hash"] = "abc123" * 10  # pretend dirty
        ok, errors = verify_cga_statement(stmt, conn, project_root=tmp_path)
        assert not ok
        assert any("git_dirty_hash" in e for e in errors), f"expected git_dirty_hash mismatch in errors, got: {errors}"

    def test_strip_url_credentials_removes_token_userinfo(self):
        """Personal-access-tokens cloned via HTTPS leak through
        ``remote.origin.url``. The strip helper must remove userinfo so
        the token never lands in a signed attestation's subject.name.
        """
        from roam.attest.cga import _strip_url_credentials

        # Token-bearing HTTPS clone URLs
        assert (
            _strip_url_credentials("https://x:ghp_FAKETOKEN@github.com/owner/repo") == "https://github.com/owner/repo"
        )
        assert (
            _strip_url_credentials("https://oauth2:VERY_SECRET_TOKEN@gitlab.com/group/proj.git")
            == "https://gitlab.com/group/proj.git"
        )
        # Bare-user form (no token) — also stripped, since we treat any
        # userinfo as credential-bearing in the HTTPS-clone context.
        assert _strip_url_credentials("https://alice@github.com/owner/repo") == "https://github.com/owner/repo"

    def test_strip_url_credentials_preserves_at_in_path_or_query(self):
        """R9 security recheck #2 — the previous implementation used
        ``rpartition("@")`` over the whole post-``://`` string, which
        finds the LAST ``@`` anywhere in the URL. URLs that
        legitimately carry ``@`` in the path or query (email addresses
        in reviewer params, content-addressed package paths, etc.)
        got rewritten to the wrong host.
        """
        from roam.attest.cga import _strip_url_credentials

        # Legitimate email in query string — must NOT be treated as userinfo.
        assert (
            _strip_url_credentials("https://github.com/owner/repo?reviewer=a@b.com")
            == "https://github.com/owner/repo?reviewer=a@b.com"
        )
        # Path-segment with ``@`` (npm-style scoped package).
        assert (
            _strip_url_credentials("https://registry.npmjs.org/@scope/pkg") == "https://registry.npmjs.org/@scope/pkg"
        )
        # Combined: real userinfo PLUS ``@`` later in path — strip
        # the userinfo only, leave the path alone.
        assert (
            _strip_url_credentials("https://x:tok@github.com/owner/@scoped/repo")
            == "https://github.com/owner/@scoped/repo"
        )

    def test_legacy_dev_iri_still_verifies(self, tmp_path):
        """Statements signed before the .dev → .com migration must still
        verify cleanly so consumers don't have to rebuild the chain.
        """
        from roam.attest.cga import _LEGACY_PREDICATE_TYPES

        conn = _make_in_memory_db()
        stmt = build_cga_statement(conn, project_root=tmp_path)
        # Spoof an old-style emit by swapping the predicate type back
        # to the legacy ``.dev`` IRI.
        stmt["predicateType"] = _LEGACY_PREDICATE_TYPES[0]
        ok, errors = verify_cga_statement(stmt, conn, project_root=tmp_path)
        assert ok, f"legacy IRI should verify, got errors: {errors}"

    def test_predicate_type_now_uses_owned_domain(self, tmp_path):
        """New emits must use the .com IRI — that's the served domain.
        SLSA / in-toto consumers that dereference the IRI find a real
        page (or at least a 200 from a canonical site).
        """
        conn = _make_in_memory_db()
        stmt = build_cga_statement(conn, project_root=tmp_path)
        assert stmt["predicateType"].startswith("https://roam-code.com/")
        assert "roam-code.dev" not in stmt["predicateType"]

    def test_strip_url_credentials_passes_through_clean_urls(self):
        """No userinfo → return unchanged. SSH form likewise unchanged
        (``git@`` is conventional, not a credential)."""
        from roam.attest.cga import _strip_url_credentials

        assert _strip_url_credentials("https://github.com/owner/repo") == "https://github.com/owner/repo"
        assert _strip_url_credentials("git@github.com:owner/repo.git") == "git@github.com:owner/repo.git"
        # Local file paths and other non-URL strings — pass through.
        assert _strip_url_credentials("/Users/dev/proj") == "/Users/dev/proj"

    def test_subject_sha_mismatch_fails_verification(self, tmp_path, monkeypatch):
        """Statement signed against commit X, live tree at commit Y → refuse."""
        from roam.attest import cga as cga_mod

        conn = _make_in_memory_db()
        stmt = build_cga_statement(conn, project_root=tmp_path)
        # Force statement to claim a different commit SHA.
        stmt["subject"][0]["digest"]["git_commit_sha1"] = "deadbeef" + "0" * 32

        # Make _git_commit_sha return a known different value so verify
        # can compare a real "live" SHA to the spoofed subject SHA.
        monkeypatch.setattr(cga_mod, "_git_commit_sha", lambda root: "feedface" + "0" * 32)
        ok, errors = verify_cga_statement(stmt, conn, project_root=tmp_path)
        assert not ok
        assert any("git_commit_sha1 mismatch" in e for e in errors), (
            f"expected git_commit_sha1 mismatch in errors, got: {errors}"
        )


# ---------------------------------------------------------------------------
# CLI surface (end-to-end against a real indexed project)
# ---------------------------------------------------------------------------


@pytest.fixture
def cga_project(tmp_path):
    proj = _make_src_project(
        tmp_path,
        {
            "auth.py": """
                class UserSession:
                    def refresh(self):
                        return self.token
                def handle_login(user):
                    return UserSession()
            """,
        },
    )
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        yield proj
    finally:
        os.chdir(old_cwd)


_make_src_project = _make_project  # alias for the fixture


class TestCGACLI:
    def test_emit_no_write(self, cga_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["cga", "emit", "--no-write"])
        assert result.exit_code == 0, result.output
        assert "VERDICT:" in result.output
        assert PREDICATE_TYPE in result.output

    def test_emit_json_envelope(self, cga_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "cga", "emit", "--no-write"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["command"] == "cga-emit"
        assert data["summary"]["predicate_type"] == PREDICATE_TYPE
        statement = data["statement"]
        assert statement["_type"] == STATEMENT_TYPE
        assert "openvex_justification_set" in statement["predicate"]
        assert "code_not_reachable" not in statement["predicate"]["openvex_justification_set"]

    def test_emit_then_verify_round_trip(self, cga_project, tmp_path):
        runner = CliRunner()
        out = tmp_path / "cga.json"
        emit_result = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert emit_result.exit_code == 0, emit_result.output
        assert out.exists()

        # Unsigned emit -> no sibling bundle -> verify must fail-closed by
        # default. Pass --no-cosign to acknowledge the predicate-only path.
        verify_result = runner.invoke(cli, ["cga", "verify", str(out), "--no-cosign"])
        assert verify_result.exit_code == 0, verify_result.output
        assert "verified" in verify_result.output.lower()

    def test_verify_rejects_invalid_json(self, cga_project, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["cga", "verify", str(bad)])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CGA × taint integration (the killer compliance chain)
# ---------------------------------------------------------------------------


class TestCGATaintIntegration:
    """Per the v12 brainstorm 05_security_enterprise.md: every PR ships a
    signed CGA whose VEX `not_affected` claims are justified by graph-reach
    taint analysis. ``--include-taint`` wires E.2 into E.1's predicate.

    Status + justification mapping (verified spec-legal):
    * sanitizer in path → status=not_affected, justification=inline_mitigations_already_exist
    * no sanitizer  → status=affected, justification absent
    """

    def test_taint_finding_to_claim_sanitized(self):
        """A sanitized path becomes status=not_affected with the
        inline_mitigations_already_exist justification."""
        from roam.attest.cga import _taint_finding_to_claim
        from roam.security.taint_engine import TaintFinding

        finding = TaintFinding(
            rule_id="python-command-injection",
            severity="error",
            cwe="CWE-78",
            source_symbol={"name": "request.args", "file": "x.py", "line": 1},
            sink_symbol={"name": "os.system", "file": "x.py", "line": 5},
            path_symbols=[{"id": 1}, {"id": 2}, {"id": 3}],
            sanitizer_in_path=True,
        )
        claim = _taint_finding_to_claim(finding)
        assert claim["status"] == "not_affected"
        assert claim["justification"] == "inline_mitigations_already_exist"
        # Verify against the spec-legal set
        assert claim["justification"] in OPENVEX_JUSTIFICATIONS
        assert claim["status"] in OPENVEX_STATUSES
        assert claim["vulnerability"] == "CWE-78"
        assert claim["evidence"]["sanitizer_in_path"] is True

    def test_taint_finding_to_claim_unsanitized(self):
        """An unsanitized reach is status=affected; justification is absent
        (justification only applies to not_affected)."""
        from roam.attest.cga import _taint_finding_to_claim
        from roam.security.taint_engine import TaintFinding

        finding = TaintFinding(
            rule_id="python-sqli",
            severity="error",
            cwe="CWE-89",
            source_symbol={"name": "request.args"},
            sink_symbol={"name": "cursor.execute"},
            path_symbols=[{"id": 1}, {"id": 2}],
            sanitizer_in_path=False,
        )
        claim = _taint_finding_to_claim(finding)
        assert claim["status"] == "affected"
        # justification is absent for affected — only present for not_affected
        assert "justification" not in claim
        assert claim["status"] in OPENVEX_STATUSES

    def test_taint_finding_falls_back_to_rule_id_when_no_cwe(self):
        from roam.attest.cga import _taint_finding_to_claim
        from roam.security.taint_engine import TaintFinding

        finding = TaintFinding(
            rule_id="custom-rule-xyz",
            severity="warning",
            cwe="",
            source_symbol={},
            sink_symbol={},
            path_symbols=[],
            sanitizer_in_path=False,
        )
        claim = _taint_finding_to_claim(finding)
        assert claim["vulnerability"] == "custom-rule-xyz"
        assert claim["rule_id"] == "custom-rule-xyz"

    def test_predicate_with_no_taint_findings_has_empty_claims(self, tmp_path):
        from roam.attest.cga import build_cga_predicate

        conn = _make_in_memory_db()
        pred = build_cga_predicate(conn, project_root=tmp_path)
        assert pred["reachability_claims"] == []

    def test_predicate_with_taint_findings_carries_them(self, tmp_path):
        from roam.attest.cga import build_cga_predicate
        from roam.security.taint_engine import TaintFinding

        conn = _make_in_memory_db()
        findings = [
            TaintFinding(
                rule_id="r1",
                severity="error",
                cwe="CWE-78",
                source_symbol={"name": "src"},
                sink_symbol={"name": "snk"},
                path_symbols=[{"id": 1}, {"id": 2}],
                sanitizer_in_path=True,
            ),
            TaintFinding(
                rule_id="r2",
                severity="error",
                cwe="CWE-89",
                source_symbol={"name": "src2"},
                sink_symbol={"name": "snk2"},
                path_symbols=[{"id": 3}, {"id": 4}],
                sanitizer_in_path=False,
            ),
        ]
        pred = build_cga_predicate(conn, project_root=tmp_path, taint_findings=findings)
        assert len(pred["reachability_claims"]) == 2
        statuses = {c["status"] for c in pred["reachability_claims"]}
        assert statuses == {"not_affected", "affected"}
        # Sanitized one must carry the spec-legal justification
        sanitized = [c for c in pred["reachability_claims"] if c["status"] == "not_affected"]
        assert sanitized[0]["justification"] == "inline_mitigations_already_exist"

    def test_emit_with_include_taint_smoke(self, cga_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "cga", "emit", "--include-taint", "--no-write"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # Predicate must always have the claims slot — empty or not.
        statement = data["statement"]
        assert "reachability_claims" in statement["predicate"]

    def test_no_forbidden_justification_string_in_round_trip(self, cga_project, tmp_path):
        """End-to-end guard: emit with --include-taint, verify the on-disk
        statement never contains the forbidden v11.x 'code_not_reachable'
        string anywhere."""
        out = tmp_path / "cga_with_taint.json"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "cga",
                "emit",
                "--include-taint",
                "--output",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        contents = out.read_text(encoding="utf-8")
        assert "code_not_reachable" not in contents


# ---------------------------------------------------------------------------
# Cosign signing — graceful skip + wiring (cosign optional in CI)
# ---------------------------------------------------------------------------


class TestCosignWiring:
    """Validate the cosign integration without requiring cosign installed.

    Strategy: mock ``subprocess.run`` for the cosign wrapper functions so
    we exercise the full wiring (CLI flag → cosign_sign_statement →
    output path bookkeeping) on every machine. A separate integration
    test hits the real cosign binary when it's available (skipped
    otherwise).
    """

    def test_cosign_available_returns_tuple(self):
        from roam.attest.cga import cosign_available

        ok, version = cosign_available()
        # The shape is the contract — value depends on env.
        assert isinstance(ok, bool)
        assert isinstance(version, str)

    def test_sign_skipped_when_no_mode_chosen(self, tmp_path, monkeypatch):
        """No --key, no --keyless → return a structured skip."""
        import roam.attest.cga as cga_mod
        from roam.attest.cga import cosign_sign_statement

        # Pretend cosign is installed so the function reaches the
        # mode-check branch.
        monkeypatch.setattr(cga_mod, "cosign_available", lambda: (True, "v2.x"))

        statement = tmp_path / "cga.json"
        statement.write_text("{}", encoding="utf-8")
        result = cosign_sign_statement(statement)
        assert result.signed is False
        assert "no signing mode" in result.skipped_reason.lower()

    def test_sign_skipped_when_cosign_missing(self, tmp_path, monkeypatch):
        import roam.attest.cga as cga_mod
        from roam.attest.cga import cosign_sign_statement

        monkeypatch.setattr(cga_mod, "cosign_available", lambda: (False, ""))
        statement = tmp_path / "cga.json"
        statement.write_text("{}", encoding="utf-8")
        result = cosign_sign_statement(statement, key_path=tmp_path / "fake.key")
        assert result.signed is False
        assert "cosign" in result.skipped_reason.lower()
        assert "PATH" in result.skipped_reason or "install" in result.skipped_reason

    def test_sign_with_key_happy_path_mocked(self, tmp_path, monkeypatch):
        """Mock subprocess.run to return success; assert side effects."""
        import roam.attest.cga as cga_mod

        statement = tmp_path / "cga.json"
        statement.write_text("{}", encoding="utf-8")
        sig = tmp_path / "cga.sig"
        bundle = tmp_path / "cga.bundle"
        # Fake cosign by writing both output files when subprocess.run fires.
        calls = {}

        def fake_run(args, *_a, **_kw):
            calls["args"] = args
            sig.write_text("fake-sig\n", encoding="utf-8")
            bundle.write_text("{}", encoding="utf-8")

            class _R:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return _R()

        monkeypatch.setattr(cga_mod, "cosign_available", lambda: (True, "v2.4.0"))
        monkeypatch.setattr(cga_mod.subprocess, "run", fake_run)
        result = cga_mod.cosign_sign_statement(statement, key_path=tmp_path / "fake.key")
        assert result.signed is True
        assert result.signature_path == sig
        assert result.bundle_path == bundle
        assert "--key" in calls["args"]

    def test_sign_propagates_cosign_failure(self, tmp_path, monkeypatch):
        import roam.attest.cga as cga_mod

        statement = tmp_path / "cga.json"
        statement.write_text("{}", encoding="utf-8")

        def fake_run(*_a, **_kw):
            class _R:
                returncode = 7
                stdout = ""
                stderr = "key not found"

            return _R()

        monkeypatch.setattr(cga_mod, "cosign_available", lambda: (True, "v2.4.0"))
        monkeypatch.setattr(cga_mod.subprocess, "run", fake_run)
        result = cga_mod.cosign_sign_statement(statement, key_path=tmp_path / "fake.key")
        assert result.signed is False
        assert "exit 7" in result.skipped_reason
        assert "key not found" in result.skipped_reason

    def test_sign_exit_zero_but_no_outputs_does_not_claim_signed(self, tmp_path, monkeypatch):
        """Pattern-2 discipline: cosign exits 0 but neither the .sig nor
        the .bundle lands on disk (write race, exotic FS, perms). The
        well-behaved path always writes both; only this degraded path
        must refuse to silently report ``signed=True``. Before this
        guard a downstream verifier looking at ``signature_path=None``
        on a ``signed=True`` result would fail confusingly.
        """
        import roam.attest.cga as cga_mod

        statement = tmp_path / "cga.json"
        statement.write_text("{}", encoding="utf-8")

        # Fake cosign that returns 0 but DOES NOT write the sig/bundle.
        def fake_run(args, *_a, **_kw):
            class _R:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return _R()

        monkeypatch.setattr(cga_mod, "cosign_available", lambda: (True, "v2.4.0"))
        monkeypatch.setattr(cga_mod.subprocess, "run", fake_run)
        result = cga_mod.cosign_sign_statement(statement, key_path=tmp_path / "fake.key")
        assert result.signed is False, "Pattern-2: must not claim signed when no outputs landed"
        assert "neither signature nor bundle" in result.skipped_reason

    def test_emit_with_sign_no_write_skips_signing(self, cga_project):
        """--sign + --no-write must not attempt to sign — there's no file
        to point cosign at. Reports a clear skip reason."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "cga", "emit", "--sign", "--no-write"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # Either signed=False (cosign missing) or sign_result indicates
        # the no-write skip — both are acceptable; signed must be False.
        assert data["summary"]["signed"] is False
        if data.get("sign_result"):
            assert data["sign_result"]["signed"] is False

    def test_emit_with_sign_writes_then_attempts_sign(self, cga_project, tmp_path, monkeypatch):
        """End-to-end: --sign with a real output path kicks off the cosign
        wrapper. We mock cosign so the test runs without the binary."""
        out = tmp_path / "cga.json"
        sig = tmp_path / "cga.sig"
        bundle = tmp_path / "cga.bundle"
        import roam.attest.cga as cga_mod

        def fake_available():
            return True, "v2.4.0 (mocked)"

        def fake_run(*_a, **_kw):
            sig.write_text("sig\n", encoding="utf-8")
            bundle.write_text("{}", encoding="utf-8")

            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()

        monkeypatch.setattr(cga_mod, "cosign_available", fake_available)
        monkeypatch.setattr(cga_mod.subprocess, "run", fake_run)

        # The CLI generates a fake key file path so --key validation
        # passes (cosign is mocked anyway).
        fake_key = tmp_path / "fake.key"
        fake_key.write_text("# not a real key", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json",
                "cga",
                "emit",
                "--sign",
                "--key",
                str(fake_key),
                "--output",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["signed"] is True
        assert data["sign_result"]["signature_path"] == str(sig)
        assert data["sign_result"]["bundle_path"] == str(bundle)


class TestCosignVerifyWiring:
    def test_verify_fails_closed_when_no_bundle_and_no_optout(self, cga_project, tmp_path):
        """Load-bearing claim is "tamper-evident". When no sibling bundle is
        present and the user hasn't passed --no-cosign, verify must FAIL —
        otherwise a downloaded statement reads "verified" while the
        cryptographic-trust half is silently null.
        """
        out = tmp_path / "cga.json"
        runner = CliRunner()
        emit = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert emit.exit_code == 0, emit.output
        verify = runner.invoke(cli, ["--json", "cga", "verify", str(out)])
        assert verify.exit_code == 5, f"expected exit 5 (fail-closed), got {verify.exit_code}\n{verify.output}"
        data = json.loads(verify.output)
        assert data["summary"]["ok"] is False
        # Verdict surfaces the actionable hint.
        joined_errors = " ".join(data.get("errors", []))
        assert "no-cosign" in joined_errors or "bundle not found" in joined_errors, (
            f"expected fail-closed error to mention --no-cosign / bundle, got: {joined_errors}"
        )

    def test_verify_with_no_cosign_optout_passes_predicate_only(self, cga_project, tmp_path):
        """Explicit --no-cosign acknowledges predicate-only verification."""
        out = tmp_path / "cga.json"
        runner = CliRunner()
        emit = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert emit.exit_code == 0, emit.output
        verify = runner.invoke(cli, ["--json", "cga", "verify", str(out), "--no-cosign"])
        assert verify.exit_code == 0, verify.output
        data = json.loads(verify.output)
        assert data["summary"]["ok"] is True
        assert data["summary"]["cosign_verified"] is False

    def test_verify_no_cosign_flag_short_circuits(self, cga_project, tmp_path):
        out = tmp_path / "cga.json"
        runner = CliRunner()
        runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        # Even if a bogus bundle exists alongside, --no-cosign skips it.
        bundle = out.with_suffix(".bundle")
        bundle.write_text("not a real bundle", encoding="utf-8")
        result = runner.invoke(cli, ["--json", "cga", "verify", str(out), "--no-cosign"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["summary"]["ok"] is True
        assert data["summary"]["cosign_verified"] is False


class TestDirtyTreeRefusal:
    """Compliance officer expectation: emitting a CGA on a dirty tree
    produces a misleading attestation (commit SHA in subject doesn't
    reflect actual analysed state). Default behaviour refuses;
    --allow-dirty opts in.
    """

    def test_emit_refuses_dirty_tree_by_default(self, cga_project, tmp_path):
        """Adding an untracked file marks the tree dirty → emit refuses."""
        # cga_project has .roam/ in .gitignore so it starts clean.
        # Introduce a new untracked source file to dirty the tree.
        (cga_project / "untracked.py").write_text("def newly_added(): pass\n", encoding="utf-8")
        runner = CliRunner()
        out = tmp_path / "cga.json"
        result = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert result.exit_code != 0, f"Dirty-tree emit should refuse, got exit {result.exit_code}\n{result.output}"
        assert "DIRTY_TREE" in result.output or "dirty" in result.output.lower()

    def test_emit_allow_dirty_proceeds_and_records_hash(self, cga_project, tmp_path):
        """--allow-dirty opts in; predicate carries the dirty-hash."""
        (cga_project / "untracked.py").write_text("def newly_added(): pass\n", encoding="utf-8")
        runner = CliRunner()
        out = tmp_path / "cga.json"
        result = runner.invoke(cli, ["cga", "emit", "--output", str(out), "--allow-dirty"])
        assert result.exit_code == 0, result.output
        statement = json.loads(out.read_text(encoding="utf-8"))
        dirty_hash = statement["predicate"].get("git_dirty_hash")
        assert dirty_hash is not None and len(dirty_hash) == 64, (
            f"--allow-dirty must record the dirty-hash in the predicate, got {dirty_hash!r}"
        )

    def test_clean_tree_emits_with_none_dirty_hash(self, cga_project, tmp_path):
        """Clean tree → predicate's git_dirty_hash is None."""
        runner = CliRunner()
        out = tmp_path / "cga.json"
        result = runner.invoke(cli, ["cga", "emit", "--output", str(out)])
        assert result.exit_code == 0, result.output
        statement = json.loads(out.read_text(encoding="utf-8"))
        assert statement["predicate"]["git_dirty_hash"] is None
