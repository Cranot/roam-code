"""IRI migration regression tests — StaleRefs + SPDX paths.

The CGA predicate IRIs migrated from ``roam-code.dev`` (dead domain,
DNS NXDOMAIN) to ``roam-code.com`` (owned, dereferenceable). The
StaleRefs in-toto predicate and the SPDX document namespace had not
been migrated yet. These tests pin the new ``.com`` emission AND keep
the verifier permissive of the legacy ``.dev`` IRI so attestations
signed before the migration still verify.
"""

from __future__ import annotations

import re
import uuid

# ── StaleRefs predicate-type emission + back-compat ──────────────────


def test_stale_refs_emits_com_iri(tmp_path):
    """``build_stale_refs_attestation`` must emit the .com predicate IRI."""
    from roam.commands.cmd_stale_refs import build_stale_refs_attestation

    project_root = tmp_path
    project_root.mkdir(exist_ok=True)
    statement = build_stale_refs_attestation(
        project_root=project_root,
        summary={"verdict": "ok", "dangling": 0},
        targets=[],
        findings=[],
    )
    assert statement["predicateType"] == "https://roam-code.com/StaleRefs/v1"
    # Embedded tool block carries the same IRI.
    assert statement["predicate"]["tool"]["predicate_type"] == "https://roam-code.com/StaleRefs/v1"
    # No legacy .dev leakage in the emitted payload.
    assert "roam-code.dev" not in statement["predicateType"]
    assert "roam-code.dev" not in statement["predicate"]["tool"]["predicate_type"]


def test_stale_refs_verifier_accepts_legacy_dev_iri():
    """The verifier must keep accepting the pre-migration .dev IRI.

    Old statements signed before the IRI migration land in CI; the
    verifier-side compatibility tuple keeps them green.
    """
    from roam.commands.cmd_stale_refs import verify_stale_refs_attestation

    legacy = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://roam-code.dev/StaleRefs/v1",
        "subject": [{"name": "x", "digest": {"git_commit_sha1": "deadbeef"}}],
        "predicate": {"scan_summary": {}, "targets": []},
    }
    ok, reason = verify_stale_refs_attestation(legacy)
    assert ok, f"verifier rejected legacy .dev IRI: {reason}"


def test_stale_refs_verifier_accepts_current_com_iri():
    from roam.commands.cmd_stale_refs import verify_stale_refs_attestation

    current = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://roam-code.com/StaleRefs/v1",
        "subject": [{"name": "x", "digest": {"git_commit_sha1": "deadbeef"}}],
        "predicate": {"scan_summary": {}, "targets": []},
    }
    ok, reason = verify_stale_refs_attestation(current)
    assert ok, f"verifier rejected current .com IRI: {reason}"


def test_stale_refs_verifier_rejects_unknown_iri():
    from roam.commands.cmd_stale_refs import verify_stale_refs_attestation

    bad = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://example.com/SomethingElse/v1",
        "subject": [{"name": "x", "digest": {"git_commit_sha1": "deadbeef"}}],
        "predicate": {"scan_summary": {}, "targets": []},
    }
    ok, reason = verify_stale_refs_attestation(bad)
    assert not ok
    assert "predicateType" in reason


# ── SPDX document namespace emission ─────────────────────────────────


def test_sbom_emits_com_namespace():
    """``_generate_spdx`` must emit a roam-code.com document namespace."""
    from roam.commands.cmd_sbom import _generate_spdx

    sbom = _generate_spdx("proj-x", [], None)
    namespace = sbom["documentNamespace"]
    assert namespace.startswith("https://roam-code.com/spdx/proj-x/")
    assert "roam-code.dev" not in namespace
    # The trailing component should be a UUID for global uniqueness.
    trailing = namespace.rsplit("/", 1)[-1]
    # Round-trip: passing the trailing token to uuid.UUID must succeed.
    uuid.UUID(trailing)
    # Belt-and-braces sanity on the full shape.
    assert re.fullmatch(
        r"https://roam-code\.com/spdx/proj-x/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        namespace,
    )
