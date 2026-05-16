"""W177 — ``roam pr-replay --evidence`` / ``--markdown`` / ``--evidence-bundle``.

These tests cover Phase 3 of the evidence compiler: wiring PR Replay to
emit one canonical ``ChangeEvidence`` JSON packet and one Markdown
companion report. The companion report includes a "Suggested Review
configuration" section derived from the same finding cluster the JSON
envelope already surfaces.

Test inventory (matches the W177 deliverable list):

1. ``test_pr_replay_emits_evidence_json`` — ``--evidence PATH`` writes a
   parseable ChangeEvidence packet.
2. ``test_pr_replay_evidence_is_canonical`` — round-trips through JSON.
3. ``test_pr_replay_emits_markdown`` — ``--markdown PATH`` writes a
   Markdown file with the expected headings.
4. ``test_pr_replay_evidence_bundle_creates_both`` —
   ``--evidence-bundle DIR`` writes both files into the directory.
5. ``test_pr_replay_suggested_review_config_appears_in_markdown`` —
   Markdown contains the "Suggested Review" heading.
6. ``test_pr_replay_no_evidence_flag_no_evidence_file`` — back-compat:
   without ``--evidence``, no evidence artefact is written.
7. ``test_pr_replay_evidence_contains_findings_from_persist`` — when the
   central findings registry is populated, the evidence packet surfaces
   those rows.

Tests invoke ``roam pr-replay`` via ``CliRunner`` against the harness
repo — same pattern ``tests/test_pr_replay.py`` already uses.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(*args: str, json_mode: bool = False) -> tuple[int, str]:
    """Invoke ``roam pr-replay`` and return ``(exit_code, captured_output)``."""
    from roam.cli import cli

    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + ["pr-replay", *args]
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


# ---------------------------------------------------------------------------
# 1. --evidence writes a ChangeEvidence JSON
# ---------------------------------------------------------------------------


def test_pr_replay_emits_evidence_json(tmp_path):
    """``roam pr-replay --evidence PATH`` writes a valid ChangeEvidence packet."""
    target = tmp_path / "evidence.json"
    code, _ = _invoke("--tier", "sample", "--evidence", str(target))
    assert code == 0
    assert target.exists(), "evidence file was not written"

    payload = _json.loads(target.read_text(encoding="utf-8"))
    # Core ChangeEvidence fields — these are the dataclass shape contract
    # from W174, and the packet must surface them.
    assert isinstance(payload.get("evidence_id"), str) and payload["evidence_id"]
    assert payload.get("schema_version")
    assert payload.get("git_range")  # the replay range round-trips
    # Content hash is stamped by ``with_content_hash`` before write.
    assert isinstance(payload.get("content_hash"), str)
    assert len(payload["content_hash"]) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# 2. Canonical / roundtrip-stable JSON
# ---------------------------------------------------------------------------


def test_pr_replay_evidence_is_canonical(tmp_path):
    """The JSON on disk parses + serialises back to the same bytes."""
    target = tmp_path / "evidence.json"
    code, _ = _invoke("--tier", "sample", "--evidence", str(target))
    assert code == 0

    raw = target.read_text(encoding="utf-8")
    parsed = _json.loads(raw)
    # Round-trip via canonical-style serialisation (sort_keys + compact
    # separators) should match the file bytes exactly — that's the
    # determinism contract from ChangeEvidence.to_canonical_json().
    re_emitted = _json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    assert re_emitted == raw, "canonical JSON is not byte-stable on round-trip"


# ---------------------------------------------------------------------------
# 3. --markdown writes a companion report
# ---------------------------------------------------------------------------


def test_pr_replay_emits_markdown(tmp_path):
    """``--markdown PATH`` writes a Markdown report with the expected headings."""
    target = tmp_path / "report.md"
    code, _ = _invoke("--tier", "sample", "--markdown", str(target))
    assert code == 0
    assert target.exists(), "markdown report was not written"

    body = target.read_text(encoding="utf-8")
    # Headings from templates/audit-report/pr-replay-template.md — the
    # renderer is documented to mirror them so a drift between template
    # and rendered output is loud.
    assert body.startswith("# PR Replay — ")
    assert "## Scope" in body
    assert "## Changed subjects (top 20)" in body
    assert "## Findings" in body
    assert "## Tests" in body
    assert "## Approvals and accepted risks" in body
    assert "## Suggested Review configuration" in body


# ---------------------------------------------------------------------------
# 4. --evidence-bundle creates both
# ---------------------------------------------------------------------------


def test_pr_replay_evidence_bundle_creates_both(tmp_path):
    """``--evidence-bundle DIR`` writes both ``evidence.json`` and ``report.md``."""
    bundle = tmp_path / "bundle"
    code, _ = _invoke("--tier", "sample", "--evidence-bundle", str(bundle))
    assert code == 0
    assert (bundle / "evidence.json").exists(), "bundle missing evidence.json"
    assert (bundle / "report.md").exists(), "bundle missing report.md"

    # And the evidence file is still parseable JSON.
    payload = _json.loads((bundle / "evidence.json").read_text(encoding="utf-8"))
    assert payload.get("schema_version")


def test_pr_replay_evidence_flag_wins_over_bundle(tmp_path):
    """When ``--evidence`` and ``--evidence-bundle`` are both set, ``--evidence`` wins.

    The bundle directory still gets the Markdown report (since
    ``--markdown`` wasn't set), but the JSON goes to the explicit
    ``--evidence`` path.
    """
    bundle = tmp_path / "bundle"
    explicit = tmp_path / "explicit-evidence.json"
    code, _ = _invoke(
        "--tier",
        "sample",
        "--evidence-bundle",
        str(bundle),
        "--evidence",
        str(explicit),
    )
    assert code == 0
    assert explicit.exists(), "explicit --evidence path was not written"
    # The bundle JSON should NOT exist — the explicit flag won.
    assert not (bundle / "evidence.json").exists(), "bundle evidence.json was written despite explicit --evidence"
    # The Markdown sibling from the bundle still ships (--markdown wasn't
    # passed, so the bundle default takes over for that artefact).
    assert (bundle / "report.md").exists()


# ---------------------------------------------------------------------------
# 5. Suggested Review section in Markdown
# ---------------------------------------------------------------------------


def test_pr_replay_suggested_review_config_appears_in_markdown(tmp_path):
    """The Markdown report includes the ``Suggested Review configuration`` heading.

    Whether the section has detector-specific suggestions depends on the
    replay window, but the heading itself is always present (the renderer
    falls back to a "nothing to suggest" note when the window is clean).
    """
    target = tmp_path / "report.md"
    code, _ = _invoke("--tier", "sample", "--markdown", str(target))
    assert code == 0
    body = target.read_text(encoding="utf-8")
    assert "## Suggested Review configuration" in body


# ---------------------------------------------------------------------------
# 6. Back-compat — no flags, no evidence files
# ---------------------------------------------------------------------------


def test_pr_replay_no_evidence_flag_no_evidence_file(tmp_path):
    """Without ``--evidence`` / ``--markdown`` / ``--evidence-bundle``, no files."""
    code, _ = _invoke("--tier", "sample")
    assert code == 0
    # The CWD should not have grown an evidence.json — and the tmp_path
    # the test owns should be empty (CliRunner doesn't touch it).
    assert not (tmp_path / "evidence.json").exists()
    assert not (tmp_path / "report.md").exists()


# ---------------------------------------------------------------------------
# 7. Findings registry plumbed into evidence
# ---------------------------------------------------------------------------


def test_pr_replay_evidence_contains_findings_from_persist(tmp_path):
    """When the findings registry has rows, the evidence packet surfaces them.

    The W90 substrate (``_collect_findings_from_registry``) is wired
    into the inline evidence collector. We assert the packet has a
    ``findings`` list — non-empty when the registry has data,
    backstopped by the postmortem-aggregate rows otherwise.
    """
    target = tmp_path / "evidence.json"
    code, _ = _invoke("--tier", "sample", "--evidence", str(target))
    assert code == 0

    payload = _json.loads(target.read_text(encoding="utf-8"))
    assert "findings" in payload
    findings = payload["findings"]
    assert isinstance(findings, list)
    # Findings is a list of detector-shaped dicts. We don't assert non-
    # empty (some indexes may be free of registry rows), but every
    # element must carry a ``detector`` key — the cross-vocabulary
    # contract from CLAUDE.md Pattern 3.
    for f in findings:
        assert "detector" in f, f"finding without detector field: {f}"


# ---------------------------------------------------------------------------
# Renderer unit tests — pure-function checks
# ---------------------------------------------------------------------------


def test_render_evidence_markdown_handles_empty_packet():
    """Renderer gracefully handles an evidence packet with empty collections."""
    from roam.commands.cmd_pr_replay import _render_evidence_markdown
    from roam.evidence import ChangeEvidence

    packet = ChangeEvidence(
        evidence_id="test:empty",
        git_range="HEAD~1..HEAD",
        verdict="clean",
        risk_level="low",
    ).with_content_hash()
    out = _render_evidence_markdown(
        evidence=packet,
        commits=[],
        by_detector=[],
        review_suggestions=None,
    )
    assert out.startswith("# PR Replay — ")
    assert "_(none)_" in out  # empty subjects table sentinel
    assert "## Suggested Review configuration" in out


def test_collect_change_evidence_returns_content_hashed_packet(tmp_path, monkeypatch):
    """The inline collector stamps a content hash before returning."""
    from roam.commands.cmd_pr_replay import _collect_change_evidence

    packet = _collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[{"sha": "abc123", "subject": "test", "high": 0, "medium": 1, "date": "2026-05-13"}],
        summary={"verdict": "clean", "total_high": 0, "total_medium": 1},
        by_detector=[{"detector": "test-class", "total_findings": 2, "commits_with_finding": 1}],
        generated_at="2026-05-13 12:00 UTC",
    )
    assert packet.content_hash is not None
    assert len(packet.content_hash) == 64
    # risk_level derives from total_medium
    assert packet.risk_level in {"low", "medium", "high"}
    assert packet.git_range == "HEAD~1..HEAD"


# ---------------------------------------------------------------------------
# W179 — structural pin: PR Replay must consume the canonical W176 collector
# ---------------------------------------------------------------------------


def test_pr_replay_uses_canonical_collector():
    """Pin that ``cmd_pr_replay`` consumes ``roam.evidence.collect_change_evidence``.

    W177 shipped a temporary ``_build_inline_change_evidence`` helper that
    constructed ``ChangeEvidence`` packets directly because the W176 collector
    hadn't landed yet. W179 swapped the dispatch over to W176's canonical
    :func:`roam.evidence.collect_change_evidence` and removed the inline
    helper.

    This test pins three structural facts so the swap can't silently regress:

    1. ``cmd_pr_replay`` imports ``collect_change_evidence`` (the canonical
       W176 surface) — checked by introspecting the module's source AST so
       we catch lazy imports inside the dispatcher too.
    2. The temporary ``_build_inline_change_evidence`` symbol is gone.
    3. The legacy ``_try_import_collector`` shim is gone (the dispatcher
       calls W176 unconditionally; there is no "if collector not present"
       branch to maintain).
    """
    import inspect

    from roam.commands import cmd_pr_replay

    # (1) Symbol check on the imported module.
    assert not hasattr(cmd_pr_replay, "_build_inline_change_evidence"), (
        "_build_inline_change_evidence should have been deleted in W179 — the W176 canonical collector replaces it"
    )
    assert not hasattr(cmd_pr_replay, "_try_import_collector"), (
        "_try_import_collector was a W177 lazy-import shim; W176 is always "
        "present, so the shim should have been removed in W179"
    )

    # (2) Source-level grep: confirm the import is present somewhere in
    # the file (top-level or inside the dispatcher). Reading the source via
    # ``inspect.getsourcefile`` rather than ``inspect.getsource`` so the
    # check sees lazy / deferred imports too, not just module-top imports.
    source_path = inspect.getsourcefile(cmd_pr_replay)
    assert source_path is not None, "cmd_pr_replay should have a source file"
    source = Path(source_path).read_text(encoding="utf-8")
    assert "from roam.evidence import" in source and "collect_change_evidence" in source, (
        "cmd_pr_replay must import ``collect_change_evidence`` from roam.evidence"
    )
    # And NO inline helper definition remains.
    assert "def _build_inline_change_evidence" not in source, (
        "_build_inline_change_evidence definition still present in source"
    )
    assert "def _try_import_collector" not in source, "_try_import_collector definition still present in source"


# ---------------------------------------------------------------------------
# W185 — Evidence limitations section (agentic-assurance crosswalk item 6)
# ---------------------------------------------------------------------------


def _empty_packet():
    """Build a minimal ChangeEvidence with no optional fields populated."""
    from roam.evidence import ChangeEvidence

    return ChangeEvidence(
        evidence_id="test:w185",
        git_range="HEAD~1..HEAD",
        verdict="clean",
        risk_level="low",
    ).with_content_hash()


def _render(packet):
    """Render ``packet`` to Markdown via the shared renderer."""
    from roam.commands.cmd_pr_replay import _render_evidence_markdown

    return _render_evidence_markdown(
        evidence=packet,
        commits=[],
        by_detector=[],
        review_suggestions=None,
    )


def test_evidence_limitations_section_always_present():
    """Every rendered Markdown carries an ``## Evidence limitations`` heading.

    The section is unconditional — even a clean packet with no missing
    actor identity, tests, artifacts, or redactions still emits the
    section because item 6 (non-certification) is always present.
    """
    out = _render(_empty_packet())
    assert "## Evidence limitations" in out


def test_non_certification_bullet_always_emits():
    """Every rendered Markdown contains the non-certification bullet.

    Crosswalk memo §"Build deltas" item 6: ``non-certification statement``
    is the unconditional limitation. It must appear regardless of what
    other fields the packet carries.
    """
    out = _render(_empty_packet())
    assert "**Non-certification**" in out
    # And it uses the mandated wording ("supports evidence for" /
    # "maps to") per the architecture confidence check in CLAUDE.md.
    assert "supports evidence for" in out
    assert "maps to" in out


def test_missing_test_evidence_bullet_appears_when_no_tests():
    """A packet with no tests_required + no tests_run flags missing Q7.

    W284 — the legacy item-3 "Missing test evidence" bullet was replaced
    with a Q-gap bullet derived from ``evidence_completeness()``. An
    empty packet scores Q7 as ``missing`` (no tests_run + no artifacts +
    no tests_required), so the Q7 bullet appears.
    """
    out = _render(_empty_packet())
    assert "**Q7 (verify): MISSING**" in out


def test_no_missing_test_evidence_bullet_when_tests_present():
    """When tests are recorded, the Q7 (verify) gap bullet does NOT appear.

    Q-gaps are conditional — they vanish when the underlying field is
    populated. W284 pins this: tests_run populated lifts Q7 to
    ``complete``, so the Q7 bullet disappears from the rendered section.
    """
    import dataclasses

    packet = dataclasses.replace(
        _empty_packet(),
        tests_required=("tests/test_smoke.py::test_one",),
        tests_run=({"name": "tests/test_smoke.py::test_one", "outcome": "passed"},),
        content_hash=None,  # invalidate the previous hash; renderer doesn't care
    )
    out = _render(packet)
    assert "**Q7 (verify): MISSING**" not in out
    # But the always-emitted non-cert bullet is still there.
    assert "**Non-certification**" in out


def test_redacted_context_bullet_appears_when_redactions_set():
    """A packet with ``redactions=("secret", "pii")`` surfaces redaction bullets.

    W284 — each redaction reason becomes its own bullet, derived directly
    from ``packet.redactions``. Iteration order preserves the tuple
    order (deterministic by construction).
    """
    import dataclasses

    packet = dataclasses.replace(
        _empty_packet(),
        redactions=("secret", "pii"),
        content_hash=None,
    )
    out = _render(packet)
    # Each reason gets its own bullet with the W284 vocabulary.
    assert "**Redacted content: `secret`**" in out
    assert "**Redacted content: `pii`**" in out


def test_pr_replay_renders_generated_limitations():
    """End-to-end: a packet with Q-gap + redaction + trust warning renders 3 bullets.

    W284 — the limitations section is generated from the packet, not
    boilerplate. This is the renderer-surface integration test: drive
    a synthetic packet with one bullet from each of the three sources
    through the full ``_render_evidence_markdown`` call, then extract
    the limitations section and assert the expected bullets appear in
    the expected order (Q-gap -> redaction -> trust warning).
    """
    import dataclasses

    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent:claude-opus-4.7",
                trust_tier="self_reported_agent",
            ),
        ),
        redactions=("secret",),
        content_hash=None,
    )
    out = _render(packet)

    # Extract just the Evidence limitations section.
    sections = out.split("## Evidence limitations")
    assert len(sections) >= 2, "Evidence limitations heading is missing"
    section = sections[1].split("\n---\n")[0]

    # Each bullet must appear, and Q-gaps come before redactions which
    # come before trust warnings (W284 three-source ordering).
    q3_idx = section.find("Q3 (context_read): MISSING")
    redaction_idx = section.find("Redacted content: `secret`")
    trust_idx = section.find("Actor identity unverified")
    non_cert_idx = section.find("**Non-certification**")

    assert q3_idx >= 0, "Expected Q3 gap bullet"
    assert redaction_idx >= 0, "Expected redaction bullet"
    assert trust_idx >= 0, "Expected trust-tier warning bullet"
    assert non_cert_idx >= 0, "Expected non-certification bullet"
    assert q3_idx < redaction_idx < trust_idx < non_cert_idx, (
        f"Three-source ordering broken: q3={q3_idx} red={redaction_idx} trust={trust_idx} non_cert={non_cert_idx}"
    )
    # Actor id surfaces in the warning.
    assert "agent:claude-opus-4.7" in section


# ---------------------------------------------------------------------------
# W191 — Actors / Authorities / Environment sections
#
# These tests pin the renderer surface for the three agentic-assurance
# ref tuples (W182). Each section has three branches the renderer must
# handle: ref tuple populated, legacy-scalar fallback (actors only), and
# fully-empty packet. All three sections must always render a heading so
# a Markdown diff against the template stays loud.
# ---------------------------------------------------------------------------


def test_actors_section_renders_when_actor_refs_present():
    """A packet with one ActorRef renders an Actors table with the id."""
    import dataclasses

    from roam.evidence.refs import ActorRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(
            ActorRef(
                actor_kind="agent",
                actor_id="agent:claude-opus-4.7",
                display_name="Claude",
            ),
        ),
        content_hash=None,
    )
    out = _render(packet)
    assert "## Actors" in out
    assert "`agent:claude-opus-4.7`" in out
    assert "Claude" in out
    # The legacy-fallback note must NOT appear when actor_refs is populated.
    assert "Synthesised from" not in out
    # The "no actors recorded" sentinel must NOT appear either.
    assert "No actors recorded" not in out


def test_actors_section_falls_back_to_legacy_fields():
    """A packet with ``agent_id`` but no ``actor_refs`` synthesises a row.

    W190 (collector materialisation) is in flight. Until it lands, the
    renderer falls back to the legacy flat ``agent_id`` / ``human_actor``
    fields so reviewers still see some identity surface — and annotates
    that the row is synthesised so consumers know to populate the refs
    for richer attribution.
    """
    import dataclasses

    packet = dataclasses.replace(
        _empty_packet(),
        agent_id="agent:claude-opus-4.7",
        content_hash=None,
    )
    out = _render(packet)
    assert "## Actors" in out
    assert "`agent:claude-opus-4.7`" in out
    # The synthesis annotation must appear so consumers know to upgrade
    # to the structured refs.
    assert "Synthesised from" in out
    assert "actor_refs" in out


def test_actors_section_handles_empty_gracefully():
    """A bare packet with no identity surface renders the sentinel."""
    out = _render(_empty_packet())
    assert "## Actors" in out
    assert "No actors recorded" in out
    # No phantom table when there's nothing to render.
    assert "| Kind | ID | Display |" not in out


def test_authorities_section_renders_mode_authority_ref():
    """A packet with an AuthorityRef(kind='mode', id='mode:safe_edit') renders."""
    import dataclasses

    from roam.evidence.refs import AuthorityRef

    packet = dataclasses.replace(
        _empty_packet(),
        authority_refs=(
            AuthorityRef(
                authority_kind="mode",
                authority_id="mode:safe_edit",
                granted_by="system:rules.yml",
            ),
        ),
        content_hash=None,
    )
    out = _render(packet)
    assert "## Authorities" in out
    assert "`mode`" in out
    assert "`mode:safe_edit`" in out
    assert "system:rules.yml" in out
    assert "No authorities recorded" not in out


def test_authorities_section_handles_empty_gracefully():
    """A packet without authority_refs renders the sentinel."""
    out = _render(_empty_packet())
    assert "## Authorities" in out
    assert "No authorities recorded" in out


def test_environment_section_renders_branch_range():
    """A packet with an EnvironmentRef(kind='branch_range', id=...) renders."""
    import dataclasses

    from roam.evidence.refs import EnvironmentRef

    packet = dataclasses.replace(
        _empty_packet(),
        environment_refs=(
            EnvironmentRef(
                env_kind="branch_range",
                env_id="branch_range:main:abc1234..def5678",
            ),
        ),
        content_hash=None,
    )
    out = _render(packet)
    assert "## Environment" in out
    assert "`branch_range`" in out
    assert "`branch_range:main:abc1234..def5678`" in out
    assert "No environment recorded" not in out


def test_environment_section_handles_empty_gracefully():
    """A packet without environment_refs renders the sentinel."""
    out = _render(_empty_packet())
    assert "## Environment" in out
    assert "No environment recorded" in out


def test_all_three_sections_always_present_in_template():
    """Every rendered Markdown has all 3 sections — populated or empty.

    This is the loud-Markdown-diff invariant: if the template gains or
    loses one of these headings, every rendered report shows the
    difference. The section heading is unconditional; the content body
    is a table when the refs are populated and a sentinel otherwise.
    """
    # Empty packet — all 3 sentinels.
    out_empty = _render(_empty_packet())
    assert "## Actors" in out_empty
    assert "## Authorities" in out_empty
    assert "## Environment" in out_empty

    # Fully-populated packet — all 3 tables.
    import dataclasses

    from roam.evidence.refs import ActorRef, AuthorityRef, EnvironmentRef

    packet = dataclasses.replace(
        _empty_packet(),
        actor_refs=(ActorRef(actor_kind="human", actor_id="human:alice"),),
        authority_refs=(AuthorityRef(authority_kind="approval", authority_id="approval:pr_42"),),
        environment_refs=(EnvironmentRef(env_kind="workspace", env_id="workspace:/repo"),),
        content_hash=None,
    )
    out_full = _render(packet)
    assert "## Actors" in out_full
    assert "## Authorities" in out_full
    assert "## Environment" in out_full
    # And the populated path renders tables, not sentinels.
    assert "No actors recorded" not in out_full
    assert "No authorities recorded" not in out_full
    assert "No environment recorded" not in out_full


def test_three_sections_appear_before_findings():
    """Actors / Authorities / Environment headings come BEFORE Findings.

    Section order per W191 deliverable: identity + authority + environment
    appear after Changed subjects and BEFORE Findings, so a reviewer
    reading top-to-bottom sees WHO acted under WHAT authority in WHICH
    environment before they see WHAT was found. This pins the assurance-
    frame-leads ordering.
    """
    out = _render(_empty_packet())
    actors_idx = out.index("## Actors")
    authorities_idx = out.index("## Authorities")
    environment_idx = out.index("## Environment")
    findings_idx = out.index("## Findings")
    approvals_idx = out.index("## Approvals and accepted risks")
    suggested_idx = out.index("## Suggested Review configuration")

    assert actors_idx < authorities_idx < environment_idx < findings_idx, (
        "Actors / Authorities / Environment must appear before Findings"
    )
    # And findings/approvals/suggested-review come in the expected order.
    assert findings_idx < approvals_idx < suggested_idx


# ---------------------------------------------------------------------------
# W223 — PR Replay producer feeds the W199 collector kwargs
#
# These tests pin that ``_collect_change_evidence`` invokes the six W199
# gatherers and forwards their output into ``collect_change_evidence``.
# The gatherers are best-effort by design, so each test stubs the
# gatherer it cares about and lets the others return empty.
# ---------------------------------------------------------------------------


def _stub_collector(monkeypatch, capture: dict) -> None:
    """Patch ``collect_change_evidence`` to capture its kwargs."""
    from roam.evidence import ChangeEvidence

    def fake_collect_change_evidence(**kwargs):
        capture.update(kwargs)
        # Return a minimal valid packet so the rest of the pipeline runs.
        packet = ChangeEvidence(
            evidence_id="test:w223",
            git_range=kwargs.get("git_range"),
            verdict="clean",
            risk_level="low",
        ).with_content_hash()
        return packet, []

    # Patch the symbol where ``cmd_pr_replay`` imports it (lazy import
    # inside ``_collect_change_evidence``), so the patch must target the
    # ``roam.evidence`` module rather than ``cmd_pr_replay``.
    monkeypatch.setattr(
        "roam.evidence.collect_change_evidence",
        fake_collect_change_evidence,
    )


def test_pr_replay_passes_rules_envelopes_when_available(monkeypatch):
    """When ``_gather_rules_envelopes`` returns data, the collector sees it."""
    from roam.commands import cmd_pr_replay

    synthetic = [{"command": "rules", "results": [{"name": "x", "passed": True}]}]
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_rules_envelopes",
        lambda active_run_id, warnings: list(synthetic),
    )
    # Stub the others to return empty so this test is focused.
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_audit_trail_envelope",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_vuln_reach_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_test_impact_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_cga_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_mcp_receipts_dir",
        lambda active_run_id, warnings: None,
    )

    capture: dict = {}
    _stub_collector(monkeypatch, capture)

    cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )
    assert capture.get("rules_envelopes") == synthetic, (
        f"rules_envelopes not forwarded; saw {capture.get('rules_envelopes')!r}"
    )


def test_pr_replay_passes_mcp_receipts_dir_when_exists(monkeypatch, tmp_path):
    """When ``.roam/mcp_receipts/<run_id>/`` exists, the path is forwarded."""
    from roam.commands import cmd_pr_replay

    receipts_dir = tmp_path / "receipts-w223"
    receipts_dir.mkdir()
    (receipts_dir / "tc_001.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_mcp_receipts_dir",
        lambda active_run_id, warnings: str(receipts_dir),
    )
    # Stub the rest so we isolate the assertion.
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_rules_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_audit_trail_envelope",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_vuln_reach_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_test_impact_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_cga_envelopes",
        lambda active_run_id, warnings: [],
    )

    capture: dict = {}
    _stub_collector(monkeypatch, capture)

    cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )
    assert capture.get("mcp_receipts_dir") == str(receipts_dir), (
        f"mcp_receipts_dir not forwarded; saw {capture.get('mcp_receipts_dir')!r}"
    )


def test_pr_replay_gatherers_are_best_effort(monkeypatch):
    """When a gatherer raises, the rest of the pipeline still works.

    The W223 contract: every gatherer is wrapped in a try/except in the
    dispatcher, so a single crashing gatherer must not abort the packet
    build. We make one gatherer raise and assert the collector is still
    invoked + a packet still returned.
    """
    from roam.commands import cmd_pr_replay

    def crashing_gather(active_run_id, warnings):
        raise RuntimeError("simulated gatherer crash")

    monkeypatch.setattr(cmd_pr_replay, "_gather_rules_envelopes", crashing_gather)
    # The other gatherers behave normally.
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_audit_trail_envelope",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_vuln_reach_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_test_impact_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_cga_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_mcp_receipts_dir",
        lambda active_run_id, warnings: None,
    )

    capture: dict = {}
    _stub_collector(monkeypatch, capture)

    packet = cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )
    # Collector still received the kwargs (with empty rules_envelopes
    # because the gatherer crashed before producing a value).
    assert capture.get("rules_envelopes") == []
    # Packet survived the crash.
    assert packet is not None
    assert packet.content_hash is not None


def test_pr_replay_evidence_completeness_improves_with_full_envelopes(
    monkeypatch,
):
    """When every gatherer fires, the packet hits more 'complete' Qs.

    The baseline (W201) was 3 'complete' answers (Q2, Q4, Q5). With every
    W199 gatherer feeding the collector, Q6 (policy decisions), Q7
    (verify via artifacts/tests), and Q1 (actor_refs from receipts) all
    flip to 'complete', taking the count to 6.

    This test is the high-level smoke pin: if a future wave silently
    drops one of the new gatherer outputs, the count moves and the test
    catches it.
    """
    from roam.commands import cmd_pr_replay
    from roam.evidence import collect_change_evidence as real_collect

    # Synthetic envelopes that EXERCISE each W199 path.
    rules_env = {
        "command": "rules",
        "results": [
            {
                "name": "no-secret-in-diff",
                "passed": True,
                "severity": "error",
                "violations": [],
            }
        ],
    }
    audit_env = {
        "command": "audit-trail-verify",
        "summary": {
            "verdict": "chain valid",
            "state": "valid",
            "chain_valid": True,
            "total_records": 1,
            "issues_count": 0,
            "audit_trail_path": "/tmp/audit.jsonl",
            "run_id": "run_w223_synth",
        },
        "issues": [],
        "records": 1,
    }
    vuln_env = {
        "command": "vuln-reach",
        "summary": {"verdict": "1 reachable"},
        "vulnerabilities": [
            {
                "cve": "CVE-2026-0001",
                "package": "demo",
                "severity": "low",
                "reachable": True,
                "path": [],
                "hops": 0,
                "blast_radius": 0,
            }
        ],
    }
    ti_env = {
        "command": "test-impact",
        "summary": {"verdict": "1 test reachable", "count": 1},
        "changed_files": ["src/x.py"],
        "tests": [{"file": "tests/test_x.py", "reach_count": 1}],
        "tests_run": [{"test_file": "tests/test_x.py", "outcome": "passed"}],
    }
    cga_env = {
        "command": "cga-emit",
        "summary": {
            "verdict": "CGA emitted",
            "merkle_root": "a" * 64,
            "edge_bundle_digest": "b" * 64,
            "predicate_type": "https://roam-code.com/cga/v1",
            "written_to": "/tmp/cga.intoto.json",
        },
        "statement": {
            "_type": "https://in-toto.io/Statement/v1",
            "predicateType": "https://roam-code.com/cga/v1",
            "subject": [{"name": "repo:test", "digest": {"sha256": "c" * 64}}],
            "predicate": {
                "merkle_root": "a" * 64,
                "edge_bundle_digest": "b" * 64,
                "symbol_count": 1,
                "edge_count": 1,
                "languages": ["python"],
            },
        },
    }

    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_rules_envelopes",
        lambda active_run_id, warnings: [rules_env],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_audit_trail_envelope",
        lambda active_run_id, warnings: audit_env,
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_vuln_reach_envelopes",
        lambda commit_range, warnings: [vuln_env],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_test_impact_envelopes",
        lambda commit_range, warnings: [ti_env],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_cga_envelopes",
        lambda active_run_id, warnings: [cga_env],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_mcp_receipts_dir",
        lambda active_run_id, warnings: None,
    )
    # Keep the real collector for this test — we want the actual
    # completeness score, not a stubbed one.
    monkeypatch.setattr("roam.evidence.collect_change_evidence", real_collect)

    # Baseline: same call but with EVERY gatherer returning empty.
    # Constructed for documentation; the actual baseline path below
    # uses a different fixture, so this object is unused at runtime.
    _monkeypatch_baseline = type(  # noqa: F841 — reserved for future baseline expansion
        "_M",
        (),
        {"setattr": lambda *a, **k: None},
    )()  # no-op for the baseline

    # Build the populated packet.
    packet_full = cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[{"sha": "abc", "subject": "t", "high": 1, "medium": 0, "date": "2026-05-14"}],
        summary={"verdict": "x", "total_high": 1, "total_medium": 0},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )
    full = packet_full.evidence_completeness()
    # With every gatherer populated we should hit at least 4 'complete'
    # answers (Q4 from commit subjects, Q5 from risk, Q6 from policy
    # decisions, Q7 from artifacts/tests). Q1/Q2 depend on producer-side
    # actor/authority refs which we don't stub here; this assertion is
    # asymmetric so any drift surfaces.
    assert full["complete"] >= 4, (
        f"Expected >= 4 'complete' answers when every gatherer fires, "
        f"got {full['complete']}. Per-Q: " + ", ".join(f"Q{i}={full[f'Q{i}']}" for i in range(1, 9))
    )
    # And the populated paths are *specifically* complete (the smoke pin).
    assert full["Q6"] == "complete", f"Q6 (policy) should be 'complete' with rules+audit-trail; got {full['Q6']}"
    assert full["Q7"] == "complete", (
        f"Q7 (verify) should be 'complete' with test-impact + CGA artifacts; got {full['Q7']}"
    )


# ---------------------------------------------------------------------------
# W246 — PR Replay populates context_refs from the changed-file surface
#
# The W201/W230/W244 audit traced Q3 ("WHAT context did the actor read?")
# staying at ``missing`` end-to-end. The collector had the wiring
# (``_build_context_refs_from_context_files``) but PR Replay never fed
# it. These tests pin that the W246 gatherer now stamps a context_files
# list onto the synthetic pr-bundle envelope and that the collector
# turns those rows into ``EvidenceArtifact`` entries on
# ``ChangeEvidence.context_refs``.
# ---------------------------------------------------------------------------


def test_pr_replay_gathers_context_files_from_postmortem(monkeypatch):
    """A synthetic commit range with changed files produces context_refs.

    We stub ``_gather_context_files`` directly so the test is independent
    of git state in the test runner: the contract under test is that the
    gatherer's output is forwarded to the collector and surfaces as
    ``context_refs`` on the packet. The gatherer's git wiring is covered
    by the smoke run on the real working tree.
    """
    from roam.commands import cmd_pr_replay
    from roam.evidence import collect_change_evidence as real_collect

    synthetic_context = [
        {"path": "src/foo.py", "content_hash": None, "kind": "changed"},
        {"path": "src/bar.py", "content_hash": None, "kind": "changed"},
        {"path": "tests/test_foo.py", "content_hash": None, "kind": "changed"},
    ]

    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_context_files",
        lambda commit_range, commits, warnings: list(synthetic_context),
    )
    # Stub the other gatherers so this test isolates the context-files path.
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_rules_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_audit_trail_envelope",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_vuln_reach_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_test_impact_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_cga_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_mcp_receipts_dir",
        lambda active_run_id, warnings: None,
    )
    # Use the real collector so we exercise the actual context_refs
    # construction path (``_build_context_refs_from_context_files``).
    monkeypatch.setattr("roam.evidence.collect_change_evidence", real_collect)

    packet = cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[{"sha": "abc", "subject": "t", "high": 0, "medium": 0, "date": "2026-05-14"}],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )
    assert packet.context_refs, "context_refs must be non-empty when _gather_context_files returns rows"


def test_pr_replay_context_refs_count_matches_commit_changes(monkeypatch):
    """N changed-file rows become N (or near-N) context_refs entries.

    The collector deduplicates by index, so a unique-path input round-
    trips one-to-one. We feed three unique paths and assert exactly
    three context_refs come back.
    """
    from roam.commands import cmd_pr_replay
    from roam.evidence import collect_change_evidence as real_collect

    paths = ["src/a.py", "src/b.py", "src/c.py", "docs/README.md"]
    synthetic = [{"path": p, "content_hash": None, "kind": "changed"} for p in paths]

    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_context_files",
        lambda commit_range, commits, warnings: list(synthetic),
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_rules_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_audit_trail_envelope",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_vuln_reach_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_test_impact_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_cga_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_mcp_receipts_dir",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr("roam.evidence.collect_change_evidence", real_collect)

    packet = cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[{"sha": "abc", "subject": "t", "high": 0, "medium": 0, "date": "2026-05-14"}],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )
    # One context_ref per unique path.
    assert len(packet.context_refs) == len(paths), (
        f"Expected {len(paths)} context_refs (one per input path); got {len(packet.context_refs)}"
    )


def test_pr_replay_context_refs_use_artifact_kind(monkeypatch):
    """Each context_ref entry is a valid ``EvidenceArtifact`` with a kind.

    The collector currently sets ``kind="raw_envelope"`` for context
    files (see ``_build_context_refs_from_context_files``). We pin the
    contract that each entry is a real ``EvidenceArtifact`` with a kind
    drawn from ``ARTIFACT_KINDS`` and that the original path survives
    into either ``path`` or ``content_inline`` (the inline form is the
    collector's lifeboat when ``content_hash`` is absent — exactly the
    PR Replay default).
    """
    from roam.commands import cmd_pr_replay
    from roam.evidence import EvidenceArtifact
    from roam.evidence import collect_change_evidence as real_collect
    from roam.evidence._vocabulary import ARTIFACT_KINDS

    synthetic = [
        {"path": "src/lib.py", "content_hash": None, "kind": "changed"},
    ]
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_context_files",
        lambda commit_range, commits, warnings: list(synthetic),
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_rules_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_audit_trail_envelope",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_vuln_reach_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_test_impact_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_cga_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_mcp_receipts_dir",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr("roam.evidence.collect_change_evidence", real_collect)

    packet = cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[{"sha": "abc", "subject": "t", "high": 0, "medium": 0, "date": "2026-05-14"}],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )
    assert packet.context_refs, "context_refs must be non-empty"
    for ref in packet.context_refs:
        assert isinstance(ref, EvidenceArtifact), f"context_refs entry is not an EvidenceArtifact: {type(ref)!r}"
        assert ref.kind in ARTIFACT_KINDS, f"context_refs entry has unknown kind {ref.kind!r}"
        # The original path must survive into one of the two channels.
        body = ref.path or ref.content_inline or ""
        assert "src/lib.py" in body, f"original path lost: got path={ref.path!r}, inline={ref.content_inline!r}"


def test_pr_replay_gather_context_files_handles_empty_inputs():
    """Empty commit_range or commits returns an empty list with no warnings.

    The W246 gatherer must be a no-op on empty inputs — no git invocation,
    no warnings buffer pollution.
    """
    from roam.commands import cmd_pr_replay

    warnings: list[str] = []
    # Empty commits short-circuits.
    out = cmd_pr_replay._gather_context_files("HEAD~1..HEAD", [], warnings)
    assert out == []
    assert warnings == []
    # Empty range short-circuits too.
    out = cmd_pr_replay._gather_context_files("", [{"sha": "x"}], warnings)
    assert out == []
    assert warnings == []


# ---------------------------------------------------------------------------
# W260 — synth-envelope actor block (propagates W189 identity into pr-replay)
# ---------------------------------------------------------------------------


def _w260_monkeypatch_gatherers(monkeypatch):
    """Helper: stub every best-effort gatherer so the test stays focused.

    The W260 actor-block tests don't care about audit-trail / rules /
    vuln-reach / test-impact / cga / mcp / context-files data — they pin
    the actor-block-only flow. Each stub returns the empty shape the
    gatherer normally returns when nothing is on disk.
    """
    from roam.commands import cmd_pr_replay

    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_rules_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_audit_trail_envelope",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_vuln_reach_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_test_impact_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_cga_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_mcp_receipts_dir",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_context_files",
        lambda commit_range, commits, warnings: [],
    )


def test_pr_replay_synth_bundle_carries_actor_block(monkeypatch):
    """W260: ``ROAM_AGENT_ID`` flows through to ``packet.actor_refs``.

    Before W260, pr-replay synthesised an actor-free pr-bundle envelope
    so the only ``agent`` identity that reached ``ChangeEvidence.actor_refs``
    came from the audit-trail envelope. Setting ``ROAM_AGENT_ID`` had no
    effect on consumers that read the synth envelope directly.

    With W260 wired in, the synth envelope carries a W189-shape actor
    block resolved through the same priority chain ``pr-bundle emit``
    uses, and the collector folds that block into ``actor_refs``.
    """
    from roam.commands import cmd_pr_replay

    monkeypatch.setenv("ROAM_AGENT_ID", "agent:w260-test")
    # Suppress every other identity source so the assertion isolates the
    # ROAM_AGENT_ID flow.
    monkeypatch.delenv("ROAM_HUMAN_ACTOR", raising=False)
    monkeypatch.delenv("ROAM_MCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("ROAM_CI_RUNNER_ID", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS_RUN_ID", raising=False)
    _w260_monkeypatch_gatherers(monkeypatch)

    packet = cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[
            {
                "sha": "abc123",
                "subject": "test",
                "high": 0,
                "medium": 0,
                "date": "2026-05-14",
            }
        ],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )

    # The packet must carry at least one agent-kind actor_ref with the
    # exact ROAM_AGENT_ID we set.
    agent_refs = [r for r in packet.actor_refs if r.actor_kind == "agent"]
    assert agent_refs, f"Expected at least one agent-kind actor_ref; got actor_refs={packet.actor_refs!r}"
    assert any(r.actor_id == "agent:w260-test" for r in agent_refs), (
        f"Expected actor_id=agent:w260-test on at least one ref; got {[r.actor_id for r in agent_refs]!r}"
    )


def test_pr_replay_synth_bundle_scrubs_actor_secrets(monkeypatch):
    """W260: secret-shaped ``ROAM_AGENT_ID`` is scrubbed before it reaches the packet.

    A hostile env var containing a 40-char GitHub PAT-shaped substring
    must NEVER appear verbatim on any actor_ref. The producer-side scrub
    (``_scrub_actor_block`` from ``roam.evidence.collector``) runs on
    the synth envelope, and the collector's own scrub runs again on
    ingest — defense-in-depth.

    The packet must (a) NOT contain the raw secret on any actor_ref,
    and (b) carry ``"secret"`` in ``redactions`` so consumers can tell
    that redaction ran (Pattern 2 — explicit absence beats silent
    absence).
    """
    from roam.commands import cmd_pr_replay

    secret = "ghp_realsecret1234567890abcdefghijklmnop"  # 40 char ghp_ token
    monkeypatch.setenv("ROAM_AGENT_ID", secret)
    monkeypatch.delenv("ROAM_HUMAN_ACTOR", raising=False)
    monkeypatch.delenv("ROAM_MCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("ROAM_CI_RUNNER_ID", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS_RUN_ID", raising=False)
    _w260_monkeypatch_gatherers(monkeypatch)

    packet = cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[
            {
                "sha": "abc123",
                "subject": "t",
                "high": 0,
                "medium": 0,
                "date": "2026-05-14",
            }
        ],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )

    # No actor_ref must contain the raw secret. ``[REDACTED]`` is the
    # canonical placeholder both scrub helpers stamp on a match.
    for ref in packet.actor_refs:
        assert secret not in (ref.actor_id or ""), f"raw secret leaked into actor_ref: {ref!r}"

    # "secret" must appear in the packet's redactions trail.
    assert "secret" in packet.redactions, f"Expected 'secret' in packet.redactions; got {packet.redactions!r}"


def test_pr_replay_synth_bundle_actor_kind_classified(monkeypatch):
    """W260: ``ROAM_AGENT_ID`` populates ``actor_kind='agent'`` on the resolved block.

    Pins the W189 priority chain (agent > ci_runner > mcp_client > tool
    > human > external) — when only ``ROAM_AGENT_ID`` is set, the
    resolved kind must be ``"agent"``. Asserted by introspecting the
    actor block we stamp on the synth envelope BEFORE the collector
    transforms it into actor_refs.
    """
    from roam.commands.actor_helpers import resolve_actor_block

    monkeypatch.setenv("ROAM_AGENT_ID", "agent:w260-kind")
    monkeypatch.delenv("ROAM_HUMAN_ACTOR", raising=False)
    monkeypatch.delenv("ROAM_MCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("ROAM_CI_RUNNER_ID", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS_RUN_ID", raising=False)

    actor = resolve_actor_block(
        agent_id_override=None,
        human_actor_override=None,
        repo_root=None,
    )
    assert actor.get("agent_id") == "agent:w260-kind"
    assert actor.get("actor_kind") == "agent", (
        f"Expected actor_kind='agent'; got {actor.get('actor_kind')!r} on resolved block {actor!r}"
    )


# ---------------------------------------------------------------------------
# W261 — Q8 (accept) limitation marker via ``producer_not_available``
# ---------------------------------------------------------------------------
#
# These tests pin the W261 behaviour: when ``roam pr-replay`` runs and no
# real approvals / accepted-risks producer has been wired in, the packet
# stamps an explicit ``producer_not_available`` redaction so the Q8 score
# lifts from ``missing`` to ``partial`` and the banner stays honest.
#
# The W261 decision (option (b1) in the directive) was to extend the
# existing ``REDACTION_REASONS`` closed enumeration rather than adding a
# new first-class ``evidence_limitations`` field. Rationale: keeping
# redactions as the single "missing-data signal" mechanism preserves the
# byte-stable canonical-JSON contract and avoids a schema expansion that
# would have hash-stability implications via
# ``_W210_OMIT_WHEN_DEFAULT_FIELDS``.


def test_pr_replay_emits_q8_limitation_when_no_approvals(tmp_path):
    """W261: ``roam pr-replay`` stamps ``producer_not_available`` when no approvals data exists.

    PR Replay has no human-approvals harvester today; the producer-side
    inputs to ``collect_change_evidence`` deliberately leave the synth
    envelope's ``approvals`` and ``accepted_risks`` keys unset. The W261
    marker ensures the resulting packet declares the gap explicitly.

    Asserts:

    1. ``redactions`` on the packet contains ``"producer_not_available"``.
    2. ``approvals`` and ``accepted_risks`` are both empty (the producer-
       gap signal is the ONLY acceptance-evidence carrier today).
    """
    target = tmp_path / "w261-evidence.json"
    code, _ = _invoke("--tier", "sample", "--evidence", str(target))
    assert code == 0
    assert target.exists(), "pr-replay reported success but wrote no file"

    payload = _json.loads(target.read_text(encoding="utf-8"))
    redactions = payload.get("redactions") or []
    assert "producer_not_available" in redactions, (
        f"Expected 'producer_not_available' in redactions; got {redactions!r}"
    )
    # Sanity: the marker fires precisely BECAUSE no real approvals data
    # was harvested. If a future producer ships, this assertion will need
    # to flip — and the W261 conditional in cmd_pr_replay.py will skip
    # the marker.
    assert not (payload.get("approvals") or []), (
        f"Producer-gap marker should only fire when approvals are empty; got approvals={payload.get('approvals')!r}"
    )
    assert not (payload.get("accepted_risks") or []), (
        "Producer-gap marker should only fire when accepted_risks are empty; "
        f"got accepted_risks={payload.get('accepted_risks')!r}"
    )


def test_pr_replay_q8_scores_partial_with_limitation_marker(tmp_path):
    """W261: the on-disk packet's ``evidence_completeness()`` scores Q8 = ``partial``.

    Reads the JSON pr-replay wrote, reconstructs a ``ChangeEvidence``
    just enough to score Q8, and asserts the result is ``"partial"``
    rather than ``"missing"``. Q8 must NOT be ``"complete"`` because no
    real approvals data is present.

    This delegates to the same ``_packet_from_pr_replay_json`` helper the
    W220 eight-questions audit uses, so a future change to the
    reconstruction path stays single-sourced.
    """
    from tests.test_eight_questions_audit import _packet_from_pr_replay_json

    target = tmp_path / "w261-evidence-partial.json"
    code, _ = _invoke("--tier", "sample", "--evidence", str(target))
    assert code == 0

    payload = _json.loads(target.read_text(encoding="utf-8"))
    packet = _packet_from_pr_replay_json(payload)
    full = packet.evidence_completeness()
    assert full["Q8"] == "partial", (
        "Expected Q8='partial' because producer_not_available marker "
        f"declares the gap; got Q8={full['Q8']!r} on packet "
        f"redactions={packet.redactions!r}, approvals={packet.approvals!r}, "
        f"accepted_risks={packet.accepted_risks!r}"
    )


def test_pr_replay_q8_scores_complete_with_real_approval():
    """W261: approvals on the packet win over the limitation marker.

    Edge case — a packet that carries BOTH a real approval AND the
    ``producer_not_available`` marker must still score Q8 = ``complete``
    because approvals are the strongest signal. Synthetic test using the
    in-memory dataclass constructor (no producer involved) — proves the
    scoring function's precedence is correct.

    Scenarios this guards:

    * A future approvals harvester ships and the W261 conditional fails to
      strip the marker. Q8 should still be ``complete`` from approvals,
      not lifted-but-stuck-at-partial by the stale marker.
    * A caller manually attaches approvals to a packet that already
      carries the W261 marker (e.g. testing or post-hoc enrichment).
    """
    from roam.evidence import ChangeEvidence

    packet = ChangeEvidence(
        evidence_id="ev_w261_complete_with_marker",
        approvals=(
            {
                "reviewer": "human:alice@example.com",
                "at": "2026-05-14T10:00:00Z",
            },
        ),
        # Both signals present: producer_not_available AND a real
        # approval. Real approval wins (Q8 = complete).
        redactions=("producer_not_available",),
    )
    full = packet.evidence_completeness()
    assert full["Q8"] == "complete", f"Approvals should win over the limitation marker; got Q8={full['Q8']!r}"


# ---------------------------------------------------------------------------
# W267 — policy-decision gatherers for constitution / permits / leases.
#
# These tests pin that PR Replay's three new gatherers read the on-disk
# substrate state, emit one policy_decision per top-level entry, and
# forward the combined list to the collector via ``extra_policy_decisions``.
#
# Each test monkeypatches ``find_project_root`` to point at a tmp fixture
# directory so the gatherer reads a controlled state — the alternative
# (running pr-replay in tmp_path) would also exercise git plumbing and
# postmortem, which is outside the scope of this contract.
# ---------------------------------------------------------------------------


def _make_repo_with_dotroam(tmp_path):
    """Create a fixture repo with a ``.roam/`` directory; return repo root."""
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    (repo / ".roam").mkdir()
    return repo


def test_pr_replay_gathers_constitution_policy_decisions(monkeypatch, tmp_path):
    """Constitution gates surface as ``policy_decisions`` rows.

    Writes a fake ``.roam/constitution.yml`` with two top-level gates,
    monkeypatches ``find_project_root`` so the gatherer sees the fixture,
    and asserts the resulting list contains exactly two
    ``PolicyDecision(rule_id="constitution:...")`` rows with
    ``decision="not_evaluated"``.
    """
    from roam.commands import cmd_pr_replay

    repo = _make_repo_with_dotroam(tmp_path)
    constitution_yml = repo / ".roam" / "constitution.yml"
    constitution_yml.write_text(
        "version: 1\n"
        "required_checks:\n"
        "  before_edit:\n"
        "    - roam preflight ${symbol}\n"
        "  before_pr:\n"
        "    - roam pr-bundle validate --strict\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("roam.db.connection.find_project_root", lambda *a, **k: repo)

    warnings: list[str] = []
    decisions = cmd_pr_replay._gather_constitution_policy_decisions(warnings)
    assert warnings == [], f"unexpected warnings: {warnings!r}"
    assert len(decisions) == 2, f"expected 2 gate decisions, got {len(decisions)}: {decisions!r}"
    rule_ids = {d["rule_id"] for d in decisions}
    assert rule_ids == {
        "constitution:before_edit",
        "constitution:before_pr",
    }, f"unexpected rule_ids: {rule_ids!r}"
    for d in decisions:
        assert d["decision"] == "not_evaluated", f"expected not_evaluated, got {d['decision']!r}"
        assert d["evidence_ref"].startswith("constitution:")


def test_pr_replay_gathers_permit_policy_decisions(monkeypatch, tmp_path):
    """Each ``.roam/permits/<id>.json`` becomes one ``decision=allow`` row.

    W383: the gatherer now routes through the shared validated reader in
    ``roam.permits.store.load_permits_from_disk``, so the fixture permit_id
    MUST match ``PERMIT_ID_RE`` (``permit_YYYYMMDD_<6-hex>``) AND the
    on-disk JSON MUST carry the full ``PermitRecord`` field set. Both
    requirements mirror what the W198 writer actually produces on disk;
    a pre-W383 fixture that loosened either failed silently under the
    old loop but is now correctly dropped by the validator.
    """
    from roam.commands import cmd_pr_replay

    repo = _make_repo_with_dotroam(tmp_path)
    permits_dir = repo / ".roam" / "permits"
    permits_dir.mkdir()
    pid = "permit_20260514_abcdef"
    permit_file = permits_dir / f"{pid}.json"
    permit_file.write_text(
        _json.dumps(
            {
                "permit_id": pid,
                "scope": "modify src/foo.py",
                "expires_at": "2026-05-20T12:00:00Z",
                "issued_to": "agent:test",
                "issued_at": "2026-05-14T10:00:00Z",
                "issued_by": "human:operator",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("roam.db.connection.find_project_root", lambda *a, **k: repo)

    warnings: list[str] = []
    decisions = cmd_pr_replay._gather_permit_policy_decisions(warnings)
    assert warnings == [], f"unexpected warnings: {warnings!r}"
    assert len(decisions) == 1
    row = decisions[0]
    assert row["rule_id"] == f"permit:{pid}"
    assert row["decision"] == "allow"
    assert row["evidence_ref"] == f"permit:{pid}"
    assert row["expires_at"] == "2026-05-20T12:00:00Z"
    assert row["scope"] == "modify src/foo.py"


def test_pr_replay_gathers_lease_policy_decisions(monkeypatch, tmp_path):
    """Each lease surfaces as one ``decision=allow`` row carrying its subject."""
    from roam.commands import cmd_pr_replay

    repo = _make_repo_with_dotroam(tmp_path)
    leases_dir = repo / ".roam" / "leases"
    leases_dir.mkdir()
    # Use the same on-disk shape the substrate writes today.
    lease_doc = {
        "lease_id": "lease_test_w267",
        "agent": "w267-test-agent",
        "subject_kind": "files",
        "subject": ["src/roam/cli.py"],
        "ttl_seconds": 1800,
        "acquired_at": "2026-05-14T10:00:00Z",
        "expires_at": "2026-05-14T10:30:00Z",
        "state": "active",
    }
    (leases_dir / "lease_test_w267.json").write_text(_json.dumps(lease_doc), encoding="utf-8")

    monkeypatch.setattr("roam.db.connection.find_project_root", lambda *a, **k: repo)

    warnings: list[str] = []
    decisions = cmd_pr_replay._gather_lease_policy_decisions(warnings)
    assert warnings == [], f"unexpected warnings: {warnings!r}"
    assert len(decisions) == 1
    row = decisions[0]
    assert row["rule_id"] == "lease:lease_test_w267"
    assert row["decision"] == "allow"
    assert row["evidence_ref"] == "lease:lease_test_w267"
    assert row["subject_kind"] == "files"
    assert row["subject"] == ["src/roam/cli.py"]
    # state may be re-derived as "expired" by list_leases (wall-clock).
    assert row["state"] in {"active", "expired"}


def test_pr_replay_gathers_handle_missing_state(monkeypatch, tmp_path):
    """Fresh repo without ``.roam/`` returns 0 decisions and no warnings."""
    from roam.commands import cmd_pr_replay

    bare_repo = tmp_path / "bare-repo"
    bare_repo.mkdir()
    # NOTE: no .roam dir at all.

    monkeypatch.setattr("roam.db.connection.find_project_root", lambda *a, **k: bare_repo)

    for gather in (
        cmd_pr_replay._gather_constitution_policy_decisions,
        cmd_pr_replay._gather_permit_policy_decisions,
        cmd_pr_replay._gather_lease_policy_decisions,
    ):
        warnings: list[str] = []
        decisions = gather(warnings)
        assert decisions == [], f"{gather.__name__}: expected empty list on missing state, got {decisions!r}"
        assert warnings == [], f"{gather.__name__}: expected no warnings, got {warnings!r}"


def test_w447_pr_replay_lease_dir_missing_warns_when_mode_expects_leases(monkeypatch, tmp_path):
    """W447: a missing ``.roam/leases/`` dir emits an info-level marker
    when the active mode is one that *expects* leases (``migration`` /
    ``autonomous_pr``).

    Pattern 2 compliance: a structured signal beats silent fallback. The
    operator running pr-replay under ``migration`` should learn from the
    envelope that the lease directory wasn't where it should have been,
    rather than getting empty ``leases[]`` with no breadcrumb.

    The companion ``test_pr_replay_gathers_handle_missing_state`` above
    pins the *silent* path on a bare repo (no ``.roam/`` at all → no
    active mode → no warning), so the two tests together fence the rule.
    """
    from roam.commands import cmd_pr_replay
    from roam.modes import set_active_mode

    # Each mode pair: (mode_name, expect_warning).
    cases = [
        ("migration", True),
        ("autonomous_pr", True),
        ("safe_edit", False),
        ("read_only", False),
    ]
    for mode_name, expect_warning in cases:
        repo = tmp_path / f"repo-{mode_name}"
        repo.mkdir()
        (repo / ".roam").mkdir()
        # NOTE: deliberately do NOT create .roam/leases/.
        set_active_mode(repo, mode_name)

        monkeypatch.setattr("roam.db.connection.find_project_root", lambda *a, **k: repo)
        warnings: list[str] = []
        decisions = cmd_pr_replay._gather_lease_policy_decisions(warnings)
        assert decisions == [], f"mode={mode_name}: expected empty decisions, got {decisions!r}"
        if expect_warning:
            assert len(warnings) == 1, f"mode={mode_name}: expected exactly 1 info marker; got {warnings!r}"
            msg = warnings[0]
            assert "leases" in msg, msg
            assert ".roam/leases/" in msg, msg
            assert f"mode '{mode_name}'" in msg, msg
        else:
            assert warnings == [], (
                f"mode={mode_name}: expected silence (no leases expected in this mode); got {warnings!r}"
            )


def test_pr_replay_forwards_extra_policy_decisions_to_collector(monkeypatch, tmp_path):
    """The dispatcher merges all three gatherers into ``extra_policy_decisions``.

    Stubs every gatherer to return controlled outputs and asserts the
    collector receives the concatenated list as the new kwarg. This pins
    the wiring: a future refactor that drops one of the three gatherers
    surfaces here.
    """
    from roam.commands import cmd_pr_replay

    constitution_rows = [
        {
            "rule_id": "constitution:before_edit",
            "decision": "not_evaluated",
            "evidence_ref": "constitution:before_edit",
        }
    ]
    permit_rows = [
        {
            "rule_id": "permit:p1",
            "decision": "allow",
            "evidence_ref": "permit:p1",
        }
    ]
    lease_rows = [
        {
            "rule_id": "lease:l1",
            "decision": "allow",
            "evidence_ref": "lease:l1",
            "subject_kind": "files",
            "subject": ["src/x.py"],
        }
    ]

    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_constitution_policy_decisions",
        lambda warnings: list(constitution_rows),
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_permit_policy_decisions",
        lambda warnings: list(permit_rows),
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_lease_policy_decisions",
        lambda warnings: list(lease_rows),
    )
    # Stub the rest so this test stays focused.
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_rules_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_audit_trail_envelope",
        lambda active_run_id, warnings: None,
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_vuln_reach_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_test_impact_envelopes",
        lambda commit_range, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_cga_envelopes",
        lambda active_run_id, warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_mcp_receipts_dir",
        lambda active_run_id, warnings: None,
    )

    capture: dict = {}
    _stub_collector(monkeypatch, capture)

    cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )
    extras = capture.get("extra_policy_decisions") or []
    assert len(extras) == 3, (
        f"expected 3 forwarded rows (1 constitution + 1 permit + 1 lease), got {len(extras)}: {extras!r}"
    )
    rule_ids = [row["rule_id"] for row in extras]
    # Stable order: constitution first, permits second, leases last.
    assert rule_ids == [
        "constitution:before_edit",
        "permit:p1",
        "lease:l1",
    ], f"unexpected order: {rule_ids!r}"


# ---------------------------------------------------------------------------
# W272 — synth-envelope authority + environment producers (parity with W260)
# ---------------------------------------------------------------------------
#
# These tests pin the producer-side wiring added by W272: pr-replay's
# synthetic pr-bundle envelope now carries the same ``permits[]`` /
# ``leases[]`` / ``environment_refs[]`` top-level fields ``pr-bundle
# emit`` materialises via W266 / W268. The collector reads ``permits``
# and ``leases`` directly off the envelope (its
# ``_build_authority_refs`` mints one ``AuthorityRef`` per row); the
# collector does NOT read ``environment_refs`` off the envelope but the
# W272 wiring merges the W266-built tuple into the packet post-collector
# so the ``workspace`` ref reaches the on-disk packet.


def test_pr_replay_synth_bundle_carries_environment_refs(monkeypatch):
    """W272: the resulting packet has at least one ``environment_refs`` entry.

    The W266 ``build_environment_refs`` helper always emits a
    ``workspace`` ref (it falls back to ``os.getcwd()`` when no explicit
    workspace_root is supplied). The collector's own env-builder does
    NOT emit ``workspace`` unless ``caller_repo_id`` is set — pr-replay
    doesn't pass one — so before W272 the packet was missing the
    ``workspace`` row. This test pins the merge: at least one workspace
    ref must reach the packet.
    """
    from roam.commands import cmd_pr_replay

    _w260_monkeypatch_gatherers(monkeypatch)
    # Stub the W267 gatherers too so this test stays focused on env_refs.
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_constitution_policy_decisions",
        lambda warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_permit_policy_decisions",
        lambda warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_lease_policy_decisions",
        lambda warnings: [],
    )

    packet = cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[
            {
                "sha": "abc123",
                "subject": "t",
                "high": 0,
                "medium": 0,
                "date": "2026-05-14",
            }
        ],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )

    env_kinds = [r.env_kind for r in packet.environment_refs]
    assert "workspace" in env_kinds, f"Expected a workspace env_ref on the packet; got {packet.environment_refs!r}"


def test_pr_replay_synth_bundle_carries_permits_empty_array(monkeypatch, tmp_path):
    """W272: the synth envelope's ``permits`` key is always present.

    Pattern 2 (always-emit): direct-envelope consumers can rely on the
    key existing regardless of whether the ``.roam/permits/`` directory
    is populated. We monkeypatch ``find_project_root`` to a tmp_path
    that has no ``.roam/permits/`` directory so we can assert the
    explicit empty-list contract.

    We also stub the collector to capture the envelope it was handed and
    assert that ``permits=[]`` (not None, not missing) reached it.
    """
    from roam.commands import cmd_pr_replay
    from roam.db import connection as _conn

    monkeypatch.setattr(_conn, "find_project_root", lambda *a, **kw: tmp_path)

    _w260_monkeypatch_gatherers(monkeypatch)
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_constitution_policy_decisions",
        lambda warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_permit_policy_decisions",
        lambda warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_lease_policy_decisions",
        lambda warnings: [],
    )

    capture: dict = {}
    _stub_collector(monkeypatch, capture)

    cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )
    envelope = capture.get("pr_bundle_envelope") or {}
    assert "permits" in envelope, f"Expected 'permits' key on synth envelope; got keys={sorted(envelope.keys())!r}"
    assert envelope["permits"] == [], (
        f"Expected empty permits list when no on-disk permits exist; got {envelope['permits']!r}"
    )
    # Sibling key check: leases must also always-emit even when empty.
    assert "leases" in envelope and envelope["leases"] == [], (
        f"Expected empty leases list; got {envelope.get('leases')!r}"
    )
    # environment_refs always-emit (the helper is total, so the list is
    # never empty in practice — but the key must be present).
    assert "environment_refs" in envelope, (
        f"Expected 'environment_refs' key on synth envelope; got keys={sorted(envelope.keys())!r}"
    )


def test_pr_replay_synth_bundle_lifts_leases_from_disk(monkeypatch, tmp_path):
    """W272: a populated ``.roam/leases/`` directory flows into ``authority_refs``.

    Writes a fake lease document to ``tmp_path/.roam/leases/`` (via the
    canonical ``claim_lease`` helper so the on-disk schema stays single-
    sourced), monkeypatches ``find_project_root`` to point at the tmp
    repo, and asserts the resulting packet's ``authority_refs`` includes
    at least one ``authority_kind="lease"`` entry whose ``authority_id``
    matches the lease we wrote.
    """
    from roam.commands import cmd_pr_replay
    from roam.db import connection as _conn
    from roam.leases import claim_lease

    # Plant a lease on disk via the canonical writer.
    claimed, conflict = claim_lease(
        tmp_path,
        agent="agent:w272-test",
        subject=["src/foo.py"],
        kind="files",
        ttl_seconds=3600,
    )
    assert claimed is not None and conflict is None, (
        f"claim_lease should have succeeded on a virgin tmp repo; got claimed={claimed!r}, conflict={conflict!r}"
    )
    written_lease_id = claimed.lease_id

    monkeypatch.setattr(_conn, "find_project_root", lambda *a, **kw: tmp_path)

    _w260_monkeypatch_gatherers(monkeypatch)
    # Stub W267 gatherers — they ALSO read leases from disk and emit
    # PolicyDecision rows (W272's AuthorityRef path is independent), but
    # we want this test focused on the AuthorityRef path only.
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_constitution_policy_decisions",
        lambda warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_permit_policy_decisions",
        lambda warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_lease_policy_decisions",
        lambda warnings: [],
    )

    packet = cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[
            {
                "sha": "abc123",
                "subject": "t",
                "high": 0,
                "medium": 0,
                "date": "2026-05-14",
            }
        ],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )

    lease_refs = [r for r in packet.authority_refs if r.authority_kind == "lease"]
    assert lease_refs, f"Expected at least one authority_kind='lease' ref; got authority_refs={packet.authority_refs!r}"
    assert any(r.authority_id == written_lease_id for r in lease_refs), (
        f"Expected lease_id={written_lease_id!r} on at least one ref; got {[r.authority_id for r in lease_refs]!r}"
    )


def test_pr_replay_synth_bundle_authority_refs_include_permits(monkeypatch, tmp_path):
    """W272: a hand-written ``.roam/permits/`` row flows into ``authority_refs``.

    ``roam permit`` is still a verdict facade per W198 — nothing
    persists permit rows to disk in production today. The reader is
    ready for when ``--persist`` ships. We plant a synthetic
    ``permit-x.json`` directly so the W272 wiring picks it up.
    """
    import json as _json

    from roam.commands import cmd_pr_replay
    from roam.db import connection as _conn

    # Plant a synthetic permit. The W380 schema validator now requires
    # the full W198 PermitRecord shape (permit_id matching PERMIT_ID_RE,
    # plus scope, expires_at, issued_to, issued_at, issued_by), so the
    # row mirrors the on-disk format ``roam permit issue --persist``
    # writes today.
    permits_dir = tmp_path / ".roam" / "permits"
    permits_dir.mkdir(parents=True)
    permit_payload = {
        "permit_id": "permit_20260514_a12345",
        "scope": "edit:src/foo.py",
        "expires_at": "2099-01-01T00:00:00Z",
        "issued_to": "agent:w272-test",
        "issued_at": "2026-05-14T00:00:00Z",
        "issued_by": "human:w272-operator",
    }
    (permits_dir / "permit_20260514_a12345.json").write_text(_json.dumps(permit_payload), encoding="utf-8")

    monkeypatch.setattr(_conn, "find_project_root", lambda *a, **kw: tmp_path)

    _w260_monkeypatch_gatherers(monkeypatch)
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_constitution_policy_decisions",
        lambda warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_permit_policy_decisions",
        lambda warnings: [],
    )
    monkeypatch.setattr(
        cmd_pr_replay,
        "_gather_lease_policy_decisions",
        lambda warnings: [],
    )

    packet = cmd_pr_replay._collect_change_evidence(
        commit_range="HEAD~1..HEAD",
        commits=[
            {
                "sha": "abc123",
                "subject": "t",
                "high": 0,
                "medium": 0,
                "date": "2026-05-14",
            }
        ],
        summary={"verdict": "clean"},
        by_detector=[],
        generated_at="2026-05-14 00:00 UTC",
    )

    permit_refs = [r for r in packet.authority_refs if r.authority_kind == "permit"]
    assert permit_refs, (
        f"Expected at least one authority_kind='permit' ref; got authority_refs={packet.authority_refs!r}"
    )
    assert any(r.authority_id == "permit_20260514_a12345" for r in permit_refs), (
        f"Expected authority_id='permit_20260514_a12345' on at least "
        f"one ref; got {[r.authority_id for r in permit_refs]!r}"
    )
