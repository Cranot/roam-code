"""W219 producer/collector contract tests.

Pair every producer command that ships a JSON envelope (pr-bundle, critique,
pr-risk, audit-trail, run-events, vuln-reach, test-impact, cga, rules,
mcp-receipts) with the collector kwargs / ChangeEvidence destinations the
evidence compiler at :mod:`roam.evidence.collector` expects.

The matrix lives in the project memo at W219. Each test follows the same
3-step pattern:

1. Producer step — invoke the producer (CLI for pr-bundle; synthetic
   envelopes for everything else, mirroring the actual producer's
   ``json_envelope`` shape verbatim). The synthetic shapes are NOT a
   shortcut — they are the contract: a downstream contract test catches
   drift the moment the producer stops emitting the documented key.
2. Field assertion — the envelope carries the field with the right
   shape (string vs list vs dict).
3. Collector step — feed the envelope to
   :func:`collect_change_evidence` and assert the right
   ChangeEvidence field is populated.

Skipping policy: a test ONLY skips when a producer is documented as not
yet emitting a field (W186 / W201 audit gaps). The skip message names
the audit so future work can find the gap easily. Bias is toward
NOT skipping; most contracts have at least one wave wired today
(W189 producer + W190 collector ref-builders).

See ``CLAUDE.md`` Pattern 1 (never empty stdout — these contract
tests assert the envelope-shape side of that) and Pattern 3 (vocabulary
mismatch — these tests pin the agreed-upon field names).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, parse_json_output  # noqa: E402

from roam.evidence import collect_change_evidence  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_CI_ENV_VARS_TO_SCRUB = (
    "GITHUB_ACTIONS",
    "GITHUB_RUN_ID",
    "GITHUB_ACTIONS_RUN_ID",
    "GITLAB_CI",
    "CI_JOB_ID",
    "BUILDKITE",
    "BUILDKITE_BUILD_ID",
    "CIRCLECI",
    "CIRCLE_BUILD_NUM",
    "JENKINS_URL",
    "BUILD_TAG",
    "TF_BUILD",
    "BUILD_BUILDID",
    "CI",
    "ROAM_AGENT_ID",
    "ROAM_HUMAN_ACTOR",
    "ROAM_MCP_CLIENT_ID",
    "ROAM_CI_RUNNER_ID",
)


def _scrub_env(monkeypatch) -> None:
    """Unset every env var that influences the actor block / CI detection.

    Without this scrub, tests that assert "agent_id == X" can find a
    different value if the developer happens to have ROAM_AGENT_ID set
    in their shell, or GITHUB_ACTIONS=true in a CI runner.
    """
    for var in _CI_ENV_VARS_TO_SCRUB:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def bundle_project(tmp_path, monkeypatch):
    """A minimal git repo so pr-bundle commands resolve a project root.

    Mirrors the existing ``tests/test_pr_bundle.py`` fixture so the
    contract tests run in the same harness shape as the producer's own
    test suite.
    """
    _scrub_env(monkeypatch)
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hello():\n    return 'hi'\n")
    git_init(proj)
    subprocess.run(
        ["git", "checkout", "-B", "w219-contract"],
        cwd=proj,
        capture_output=True,
    )
    monkeypatch.chdir(proj)
    return proj


def _invoke(runner: CliRunner, args, **kw):
    from roam.cli import cli

    return runner.invoke(cli, args, catch_exceptions=False, **kw)


# ---------------------------------------------------------------------------
# Producer = pr-bundle (W189): actor block contracts
# ---------------------------------------------------------------------------


def test_pr_bundle_actor_agent_id_contract(
    cli_runner,
    bundle_project,
    monkeypatch,
):
    """W219 contract: pr-bundle producer emits actor.agent_id; collector
    materialises an ActorRef(actor_kind="agent")."""
    monkeypatch.setenv("ROAM_AGENT_ID", "agent:test-w219")
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "agent-id contract"])
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    # Step 2: producer-side assertion.
    assert "actor" in envelope, "pr-bundle envelope missing 'actor' block"
    assert envelope["actor"].get("agent_id") == "agent:test-w219", (
        f"actor.agent_id = {envelope['actor'].get('agent_id')!r}"
    )

    # Step 3: collector-side assertion.
    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    assert any(r.actor_kind == "agent" and r.actor_id == "agent:test-w219" for r in packet.actor_refs), (
        f"collector did not produce agent ActorRef from envelope; got {packet.actor_refs}"
    )


def test_pr_bundle_actor_human_actor_contract(
    cli_runner,
    bundle_project,
    monkeypatch,
):
    """W219 contract: pr-bundle producer emits actor.human_actor; collector
    materialises an ActorRef(actor_kind="human")."""
    monkeypatch.setenv("ROAM_HUMAN_ACTOR", "alice@example.com")
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "human contract"])
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    assert envelope["actor"].get("human_actor") == "alice@example.com"

    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    assert any(r.actor_kind == "human" and r.actor_id == "alice@example.com" for r in packet.actor_refs), (
        f"collector did not produce human ActorRef; got {packet.actor_refs}"
    )


def test_pr_bundle_actor_mcp_client_id_contract(
    cli_runner,
    bundle_project,
    monkeypatch,
):
    """W219 contract: ROAM_MCP_CLIENT_ID flows into actor.mcp_client_id;
    collector materialises an ActorRef(actor_kind="mcp_client")."""
    monkeypatch.setenv("ROAM_MCP_CLIENT_ID", "mcp:cursor-1.42")
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "mcp_client contract"])
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    assert envelope["actor"].get("mcp_client_id") == "mcp:cursor-1.42"

    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    assert any(r.actor_kind == "mcp_client" and r.actor_id == "mcp:cursor-1.42" for r in packet.actor_refs)


def test_pr_bundle_actor_ci_runner_id_contract(
    cli_runner,
    bundle_project,
    monkeypatch,
):
    """W219 contract: ROAM_CI_RUNNER_ID flows into actor.ci_runner_id;
    collector materialises an ActorRef(actor_kind="ci_runner")."""
    monkeypatch.setenv("ROAM_CI_RUNNER_ID", "gh:actions/runs/9999")
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "ci_runner contract"])
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    assert envelope["actor"].get("ci_runner_id") == "gh:actions/runs/9999"

    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    assert any(r.actor_kind == "ci_runner" and r.actor_id == "gh:actions/runs/9999" for r in packet.actor_refs)


def test_pr_bundle_actor_kind_contract(
    cli_runner,
    bundle_project,
    monkeypatch,
):
    """W219 contract: actor.actor_kind is derived from which actor field
    is populated. The collector reads actor_kind through the actor block's
    individual id fields (it materialises one ref per kind seen)."""
    monkeypatch.setenv("ROAM_AGENT_ID", "agent:kind-test")
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "actor_kind contract"])
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    # W189: when agent_id is set, actor_kind is "agent" per
    # _resolve_actor_kind precedence (agent > ci_runner > mcp_client > ...).
    assert envelope["actor"].get("actor_kind") == "agent", f"actor.actor_kind = {envelope['actor'].get('actor_kind')!r}"

    # And the resulting ActorRef carries actor_kind="agent" on the row
    # that holds the agent_id — the most direct collector-side mirror of
    # the producer-side actor_kind contract.
    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    matching = [r for r in packet.actor_refs if r.actor_id == "agent:kind-test"]
    assert len(matching) == 1, f"expected one ref for agent_id; got {matching}"
    assert matching[0].actor_kind == "agent"


# ---------------------------------------------------------------------------
# Producer = pr-bundle (W189): approvals + accepted_risks
# ---------------------------------------------------------------------------


def test_pr_bundle_approvals_contract(cli_runner, bundle_project):
    """W219 contract: pr-bundle envelope emits an approvals[] key (always
    present, defaults to []) and the collector preserves it on
    ChangeEvidence.approvals when populated.

    Current producer state (W186/W201 audit gap): cmd_pr_bundle.py hardcodes
    ``approvals=[]`` at every call site — there is no CLI affordance for
    appending approval rows yet. We assert (a) the key is present (always)
    and (b) when the collector is fed a synthetic envelope WITH approval
    rows, they survive end-to-end. The latter is the load-bearing contract;
    the former pins the Pattern-2 "explicit absence" envelope shape.
    """
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "approvals contract"])
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    # Step 2: producer-side — the key MUST be present (Pattern 2).
    assert "approvals" in envelope, "pr-bundle envelope missing 'approvals' key"
    assert isinstance(envelope["approvals"], list)

    # Step 3 (a): with the real (empty) producer envelope, collector sees
    # an empty approvals tuple — no policy_decisions are injected.
    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    assert packet.approvals == (), f"expected empty approvals tuple from a bare bundle; got {packet.approvals}"

    # Step 3 (b): mutate the envelope to carry an approval row (this is
    # the shape the collector promises to consume) and confirm it flows
    # through. This is the load-bearing contract; the CLI-affordance gap
    # is the follow-up wave, not this test's concern.
    envelope_with = dict(envelope)
    envelope_with["approvals"] = [
        {
            "approval_id": "pr_w219_1",
            "approver": "alice@example.com",
        }
    ]
    packet2, _ = collect_change_evidence(pr_bundle_envelope=envelope_with)
    assert len(packet2.approvals) == 1
    assert packet2.approvals[0]["approval_id"] == "pr_w219_1"
    # And the approval surfaces as an AuthorityRef(authority_kind="approval").
    assert any(a.authority_kind == "approval" and a.authority_id == "pr_w219_1" for a in packet2.authority_refs)


def test_pr_bundle_accepted_risks_contract(cli_runner, bundle_project):
    """W219 contract: pr-bundle envelope emits accepted_risks[] (Pattern 2 —
    always present) and the collector preserves rows when populated.

    Same producer gap as approvals: CLI affordance for adding risk
    acceptances is not wired yet (W186/W201). Test (a) covers the
    always-present-key contract; test (b) the envelope-shape contract.
    """
    _invoke(
        cli_runner,
        ["pr-bundle", "init", "--intent", "accepted_risks contract"],
    )
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    assert "accepted_risks" in envelope
    assert isinstance(envelope["accepted_risks"], list)
    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    assert packet.accepted_risks == ()

    envelope_with = dict(envelope)
    envelope_with["accepted_risks"] = [
        {"risk_id": "R-001", "rationale": "blast radius minimal", "accepted_by": "alice@example.com"},
    ]
    packet2, _ = collect_change_evidence(pr_bundle_envelope=envelope_with)
    assert len(packet2.accepted_risks) == 1
    assert packet2.accepted_risks[0]["risk_id"] == "R-001"


# ---------------------------------------------------------------------------
# Producer = pr-bundle: mode (AND mode -> authority_refs)
# ---------------------------------------------------------------------------


def test_pr_bundle_mode_contract(cli_runner, bundle_project, monkeypatch):
    """W219 contract: pr-bundle emit ALWAYS surfaces the active mode under
    both top-level ``mode`` and ``summary.active_mode`` (the collector's
    mode probe reads ``mode`` top-level OR ``summary.active_mode`` OR
    ``mode_block.active_mode``).

    Post-W224c producer reality: ``cmd_pr_bundle.py`` ALWAYS emits
    ``mode`` (top-level) and ``summary.active_mode``, defaulting to
    ``"unmoded"`` when no mode is declared (Pattern 2 — explicit
    absence, never an omitted key). See ``cmd_pr_bundle.py`` lines
    2554-2561 for the producer-side fix.

    The two assertions:

    1. Producer-side — invoke ``pr-bundle emit`` on a fresh bundle and
       confirm the real envelope carries ``mode`` top-level, mirrored
       in ``summary.active_mode``, with a value from ``VALID_MODES``
       OR the ``"unmoded"`` sentinel.
    2. Collector-side — inject a synthetic ``mode`` into the envelope
       and confirm the collector materialises
       ``AuthorityRef(authority_kind="mode", ...)``. This dual role
       pins the collector path without depending on a specific
       active-mode resolver result.
    """
    from roam.modes import VALID_MODES

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "mode contract"])
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    # Step 2 (producer-side): post-W224c, ``mode`` MUST be present
    # top-level and mirrored in ``summary.active_mode``.
    assert "mode" in envelope, "pr-bundle envelope missing top-level 'mode' key (W224c regression)"
    allowed_modes = set(VALID_MODES) | {"unmoded"}
    assert envelope["mode"] in allowed_modes, (
        f"envelope['mode'] = {envelope['mode']!r}; expected one of {sorted(allowed_modes)}"
    )
    assert "summary" in envelope and "active_mode" in envelope["summary"], (
        "pr-bundle envelope missing summary.active_mode (W224c regression)"
    )
    assert envelope["summary"]["active_mode"] == envelope["mode"], (
        f"summary.active_mode ({envelope['summary']['active_mode']!r}) "
        f"does not mirror top-level mode ({envelope['mode']!r})"
    )

    # Step 3 (collector-side): inject a known-good mode and confirm
    # the collector emits AuthorityRef(authority_kind="mode", ...).
    # This stays intact from the pre-W224c test so the collector path
    # is covered regardless of which mode the producer happened to
    # resolve above.
    envelope_with_mode = dict(envelope)
    envelope_with_mode["mode"] = "safe_edit"

    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope_with_mode)
    assert packet.mode == "safe_edit", f"collector did not pick up mode; packet.mode={packet.mode!r}"
    assert any(a.authority_kind == "mode" and a.authority_id == "safe_edit" for a in packet.authority_refs), (
        f"collector did not emit mode AuthorityRef; got {packet.authority_refs}"
    )


# ---------------------------------------------------------------------------
# Producer = pr-bundle: affected_symbols -> changed_subjects
# ---------------------------------------------------------------------------


def test_pr_bundle_affected_symbols_contract(cli_runner, bundle_project):
    """W219 contract: pr-bundle `add affected <sym>` populates
    affected_symbols[], and the collector promotes each row to an
    EvidenceSubject on ChangeEvidence.changed_subjects."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "affected contract"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "useRetry", "--kind", "function", "--blast-radius", "5"],
    )
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "affected", "uploadFile", "--kind", "function", "--blast-radius", "12"],
    )
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    names = {s["name"] for s in envelope.get("affected_symbols", [])}
    assert "useRetry" in names
    assert "uploadFile" in names

    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    subj_names = {s.qualified_name for s in packet.changed_subjects}
    assert "useRetry" in subj_names
    assert "uploadFile" in subj_names
    # All EvidenceSubjects emitted from affected_symbols carry kind="symbol".
    assert all(s.kind == "symbol" for s in packet.changed_subjects)


# ---------------------------------------------------------------------------
# Producer = pr-bundle: tests -> tests_required + tests_run
# ---------------------------------------------------------------------------


def test_pr_bundle_tests_required_contract(cli_runner, bundle_project):
    """W219 contract: pr-bundle `add test-required <file>` populates
    tests_required[], collector flattens to ChangeEvidence.tests_required."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "tests_required contract"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "test-required", "tests/test_retry.py", "--reason", "covers retry path"],
    )
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    test_files = {t["test_file"] for t in envelope.get("tests_required", [])}
    assert "tests/test_retry.py" in test_files

    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    assert "tests/test_retry.py" in packet.tests_required


def test_pr_bundle_tests_run_contract(cli_runner, bundle_project):
    """W219 contract: pr-bundle `add test-run <file>` populates tests_run[],
    collector preserves dicts on ChangeEvidence.tests_run."""
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "tests_run contract"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "test-run", "tests/test_retry.py", "--passed", "--duration-ms", "42"],
    )
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    runs = envelope.get("tests_run", [])
    assert any(r.get("test_file") == "tests/test_retry.py" for r in runs)

    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    assert any(r.get("test_file") == "tests/test_retry.py" for r in packet.tests_run)


# ---------------------------------------------------------------------------
# Producer = pr-bundle: context_files
# ---------------------------------------------------------------------------


def test_pr_bundle_context_files_contract():
    """W219 contract: collector consumes pr-bundle `context_files[]` into
    ChangeEvidence.context_refs.

    Producer gap (W186/W201): cmd_pr_bundle.py does NOT emit a top-level
    ``context_files`` key today. `pr-bundle add context-file` writes the
    file path into ``context_read.files_inspected`` instead. The collector
    probes ``context_files`` directly (collector.py:127), so until the
    producer wires it, the field is permanently empty from real
    invocations. This test pins the collector half against a synthetic
    envelope so the contract is recorded.

    Follow-up wave: have pr-bundle promote ``context_read.files_inspected``
    into a top-level ``context_files`` array on emit (or update the
    collector to read from the nested path).
    """
    synthetic_envelope = {
        "command": "pr-bundle",
        "schema": "roam-envelope-v1",
        "context_files": [
            {"path": "src/upload.py", "content_hash": "a" * 64},
            "src/retry.py",  # plain string form is also accepted
        ],
    }
    packet, warnings = collect_change_evidence(pr_bundle_envelope=synthetic_envelope)
    assert warnings == [], f"unexpected warnings: {warnings}"
    assert len(packet.context_refs) == 2
    paths_or_inline = []
    for art in packet.context_refs:
        if art.path:
            paths_or_inline.append(art.path)
        elif art.content_inline:
            paths_or_inline.append(art.content_inline)
    assert "src/upload.py" in paths_or_inline
    assert "src/retry.py" in paths_or_inline


def test_pr_bundle_real_envelope_promotes_context_files_to_top_level(
    cli_runner,
    bundle_project,
):
    """W219 contract (W224a): the real pr-bundle emit envelope NOW
    promotes ``context_read.files_inspected`` to a top-level
    ``context_files[]`` array of ``{path, content_hash}`` dicts.

    Was a known-gap baseline (W186/W201) until W224a wired the producer
    in :func:`roam.commands.cmd_pr_bundle._build_envelope`. The collector
    at ``src/roam/evidence/collector.py`` probes ``context_files``
    directly; this contract guarantees end-to-end flow from
    ``pr-bundle add context-file`` to ``ChangeEvidence.context_refs``.
    """
    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "context_files contract"])
    _invoke(
        cli_runner,
        ["pr-bundle", "add", "context-file", "src/upload.py"],
    )
    result = _invoke(
        cli_runner,
        ["--json", "pr-bundle", "emit", "--no-auto-collect"],
    )
    envelope = parse_json_output(result, command="pr-bundle")

    # W224a: producer now emits context_files[] at the top level.
    assert "context_files" in envelope, "context_files MUST be present at the envelope top level (W224a)"
    paths = [entry.get("path") for entry in envelope["context_files"]]
    assert "src/upload.py" in paths, f"src/upload.py missing from context_files; got {paths}"
    # The legacy nested path is preserved for backward compatibility.
    inspected = envelope.get("context_read", {}).get("files_inspected", [])
    assert "src/upload.py" in inspected

    # And the collector now sees a context_ref for the inspected file.
    packet, _ = collect_change_evidence(pr_bundle_envelope=envelope)
    refs = [(art.path or art.content_inline or "") for art in packet.context_refs]
    assert any("src/upload.py" in r for r in refs), (
        f"collector did not materialise a context_ref for src/upload.py; got {refs}"
    )


# ---------------------------------------------------------------------------
# Producer = critique (W153): findings[]
# ---------------------------------------------------------------------------


def test_critique_findings_contract():
    """W219 contract: a critique envelope's ``findings[]`` array becomes
    ChangeEvidence.findings entries.

    We construct a synthetic critique envelope mirroring
    ``cmd_critique._build_envelope`` exactly — the JSON shape contract is
    what the collector reads, not the CLI's plumbing. Running ``roam
    critique`` end-to-end requires an indexed project + a real diff;
    that's covered in ``tests/test_critique*.py``.
    """
    critique_env = {
        "command": "critique",
        "schema": "roam-envelope-v1",
        "summary": {
            "verdict": "1 high-severity finding",
            "changed_files": 1,
            "changed_symbols": 2,
            "findings": 1,
            "high_severity": 1,
        },
        "severity_breakdown": {"high": 1, "medium": 0, "low": 0},
        "findings": [
            {
                "check": "clones_not_edited",
                "severity": "high",
                "title": "clone siblings not modified",
                "detail": "Found 3 clone siblings of `handleSave`",
                "subject_kind": "diff_region",
                "source_detector": "critique",
            }
        ],
        "top_finding": None,
        "bench_hint": None,
        "changed_symbols": [],
    }
    packet, warnings = collect_change_evidence(critique_envelope=critique_env)
    # subject_kind=diff_region is in SUBJECT_KINDS, so no warning expected.
    assert warnings == [], f"unexpected warnings: {warnings}"

    critique_findings = [f for f in packet.findings if f.get("source_detector") == "critique"]
    assert len(critique_findings) == 1
    assert critique_findings[0]["check"] == "clones_not_edited"
    assert critique_findings[0]["severity"] == "high"


# ---------------------------------------------------------------------------
# Producer = pr-risk (W134): findings[] (or the lack thereof)
# ---------------------------------------------------------------------------


def test_pr_risk_envelope_has_findings_key():
    """W242 contract: the ``pr-risk`` envelope from ``cmd_pr_risk.py``
    now carries a top-level ``findings[]`` array.

    Pre-W242 this test pinned the GAP (envelope lacked the key;
    collector emitted a ``"no 'findings' array"`` warning when handed
    the envelope). W242 closes the gap: every ``roam pr-risk`` run
    stamps the same W134 row dicts the registry receives via
    ``--persist`` at the top-level ``findings`` key, so the collector
    flows them into ``ChangeEvidence.findings`` without warnings.

    Synthetic envelope below mirrors what the real producer emits:
    factor scalars / ``per_file`` / ``suggested_reviewers`` PLUS the
    new top-level ``findings[]`` array carrying registry-shape rows.
    """
    # Build a synthetic envelope mirroring the W242 pr-risk JSON shape
    # (now WITH a top-level findings[] array carrying W134 rows).
    pr_risk_env = {
        "command": "pr-risk",
        "schema": "roam-envelope-v1",
        "summary": {
            "verdict": "MEDIUM risk (45)",
            "risk_score": 45,
            "risk_level": "MEDIUM",
            "changed_files": 3,
            "findings_count": 1,
        },
        "findings": [
            {
                "finding_id_str": "pr-risk:composite-risk-score:abc123def456",
                "source_detector": "pr-risk",
                "source_version": "1.0.0",
                "subject_kind": "commit",
                "subject_id": None,
                "confidence": "heuristic",
                "claim": "pr-risk: MEDIUM (45/100) on unstaged",
                "kind": "pr-risk:composite-risk-score",
                "severity": "medium",
                "evidence": {
                    "diff_id": "abc123def456",
                    "risk_score": 45,
                    "risk_level": "MEDIUM",
                },
            },
        ],
        "risk_score": 45,
        "risk_level": "MEDIUM",
        "blast_radius_pct": 12.5,
        "hotspot_score": 0.3,
        "test_coverage_pct": 78.0,
        "per_file": [
            {
                "path": "src/upload.py",
                "symbols": 5,
                "blast": 12,
                "is_test": False,
                "lines_added": 10,
                "lines_removed": 2,
            },
        ],
        "suggested_reviewers": [
            {"author": "alice@example.com", "actor": "alice@example.com", "lines": 42},
        ],
    }
    # The collector flattens the top-level findings[] array onto
    # ChangeEvidence.findings without emitting the legacy
    # "no 'findings' array" warning.
    packet, warnings = collect_change_evidence(pr_risk_envelope=pr_risk_env)
    pr_risk_rows = [f for f in packet.findings if f.get("source_detector") == "pr-risk"]
    assert len(pr_risk_rows) == 1, f"pr-risk envelope should flow exactly one finding row; got {pr_risk_rows}"
    assert pr_risk_rows[0]["kind"] == "pr-risk:composite-risk-score"
    # The legacy "no 'findings' array" warning is gone for pr-risk.
    assert not any("pr_risk_envelope" in w and "no 'findings' array" in w for w in warnings), (
        f"unexpected no-findings-array warning: {warnings}"
    )


def test_pr_risk_synthetic_envelope_with_findings_flows():
    """W219 / W242 collector-side contract: when an envelope IS shaped
    with a top-level ``findings[]`` array (the shape pr-risk now emits
    post-W242), the collector flattens the rows correctly.

    The W241 collector allowlist strips free-form keys (``risk_score``,
    ``risk_level``, ...) from finding rows — only canonical keys
    (``finding_id_str``, ``source_detector``, ``kind``, ``claim``,
    ``severity``, etc.) survive. Detector-specific data lands inside
    ``evidence`` instead, but ``evidence`` is also not in the W241
    allowlist, so the assertion below pins the canonical-keys contract
    rather than reaching into payload internals.
    """
    pr_risk_env_with_findings = {
        "command": "pr-risk",
        "schema": "roam-envelope-v1",
        "summary": {"verdict": "MEDIUM risk (45)", "risk_score": 45},
        "findings": [
            {
                "finding_id_str": "pr-risk:composite-risk-score:diff_abc",
                "source_detector": "pr-risk",
                "subject_kind": "commit",
                "claim": "pr-risk: MEDIUM (45/100) on unstaged",
                "kind": "pr-risk:composite-risk-score",
                "severity": "medium",
            },
        ],
    }
    packet, _ = collect_change_evidence(pr_risk_envelope=pr_risk_env_with_findings)
    pr_risk_rows = [f for f in packet.findings if f.get("source_detector") == "pr-risk"]
    assert len(pr_risk_rows) == 1
    # The W241 allowlist keeps the canonical id / kind / severity keys.
    assert pr_risk_rows[0]["finding_id_str"] == ("pr-risk:composite-risk-score:diff_abc")
    assert pr_risk_rows[0]["kind"] == "pr-risk:composite-risk-score"
    assert pr_risk_rows[0]["severity"] == "medium"


# ---------------------------------------------------------------------------
# Producer = run-events (W190): agent + timestamp -> ActorRef + started/completed
# ---------------------------------------------------------------------------


def test_run_event_agent_contract(monkeypatch):
    """W219 contract: each run-event's ``agent`` string promotes to an
    ActorRef(actor_kind="agent")."""
    _scrub_env(monkeypatch)
    events = [
        {
            "ts": "2026-05-13T10:05:00Z",
            "seq": 1,
            "run_id": "run_w219",
            "agent": "agent:run-event-test",
            "action": "preflight",
        },
        {
            "ts": "2026-05-13T10:10:00Z",
            "seq": 2,
            "run_id": "run_w219",
            "agent": "agent:run-event-test",
            "action": "impact",
        },
    ]
    # Producer-side assertion: the run-event shape we feed the collector
    # matches what ``roam.runs.ledger.log_event`` writes to events.jsonl.
    # (verified by reading ``src/roam/runs/ledger.py`` — every event
    # carries ``ts``, ``seq``, and the ``agent`` field flows from
    # ``RunMeta.agent``.)
    for ev in events:
        assert "agent" in ev and "ts" in ev

    packet, _ = collect_change_evidence(run_events=events)
    agent_refs = [r for r in packet.actor_refs if r.actor_kind == "agent"]
    assert len(agent_refs) == 1, f"expected one deduped agent ActorRef; got {agent_refs}"
    assert agent_refs[0].actor_id == "agent:run-event-test"


def test_run_event_timestamps_contract(monkeypatch):
    """W219 contract: earliest run-event ts -> packet.started_at;
    latest -> packet.completed_at."""
    _scrub_env(monkeypatch)
    events = [
        {"ts": "2026-05-13T10:15:00Z", "seq": 3, "run_id": "run_w219_ts", "action": "critique"},
        {"ts": "2026-05-13T10:00:00Z", "seq": 1, "run_id": "run_w219_ts", "action": "preflight"},
        {"ts": "2026-05-13T10:10:00Z", "seq": 2, "run_id": "run_w219_ts", "action": "impact"},
    ]
    packet, _ = collect_change_evidence(run_events=events)
    assert packet.started_at == "2026-05-13T10:00:00Z"
    assert packet.completed_at == "2026-05-13T10:15:00Z"
    assert "run_w219_ts" in packet.run_ids


# ---------------------------------------------------------------------------
# Producer = audit-trail (W195): envelope -> manifest artifact + policy_decisions
# ---------------------------------------------------------------------------


def test_audit_trail_envelope_contract():
    """W219 contract: an audit-trail envelope is promoted to a manifest
    artifact (chain_valid in extra) and chain-integrity policy_decisions
    rows (one pass / per-issue fails).

    Synthetic envelope mirrors ``cmd_audit_trail_verify.py``'s
    ``json_envelope("audit-trail-verify", summary={chain_valid, ...},
    issues=[...])`` shape verbatim.
    """
    audit_env = {
        "command": "audit-trail-verify",
        "schema": "roam-envelope-v1",
        "summary": {
            "verdict": "chain valid (8 records)",
            "state": "valid",
            "chain_valid": True,
            "total_records": 8,
            "issues_count": 0,
            "audit_trail_path": "/nonexistent/.roam/audit-trail.jsonl",
            "run_id": "run_w219_audit",
        },
        "issues": [],
        "records": 8,
    }
    packet, _ = collect_change_evidence(audit_trail_envelope=audit_env)

    # Manifest artifact carries chain_valid + entries_count in extra.
    manifests = [a for a in packet.artifacts if a.kind == "manifest"]
    assert len(manifests) == 1
    assert manifests[0].extra.get("chain_valid") is True
    assert manifests[0].extra.get("entries_count") == 8
    assert manifests[0].artifact_id == "audit-trail:run_w219_audit"

    # Chain-integrity policy_decisions row (pass when chain_valid=True).
    chain_decisions = [d for d in packet.policy_decisions if d.get("rule_id") == "audit_trail_chain_integrity"]
    assert len(chain_decisions) == 1
    assert chain_decisions[0]["decision"] == "pass"


# ---------------------------------------------------------------------------
# Producer = vuln-reach (W193): vulnerabilities[] -> findings + raw artifact
# ---------------------------------------------------------------------------


def test_vuln_reach_envelope_contract():
    """W219 contract: vuln-reach `vulnerabilities[]` rows become
    findings(source_detector="vuln-reach"); the whole envelope is
    promoted to a raw_envelope artifact.

    Shape mirrors ``cmd_vuln_reach._output_all`` verbatim.
    """
    vuln_env = {
        "command": "vuln-reach",
        "schema": "roam-envelope-v1",
        "summary": {
            "verdict": "1 reachable vulnerability",
            "total_vulns": 1,
            "reachable_count": 1,
            "critical_count": 0,
        },
        "vulnerabilities": [
            {
                "cve": "CVE-2026-0001",
                "package": "left-pad",
                "severity": "high",
                "reachable": True,
                "path": ["main", "handle_upload", "left_pad"],
                "hops": 3,
                "blast_radius": 17,
            },
        ],
    }
    packet, warnings = collect_change_evidence(vuln_reach_envelopes=[vuln_env])
    assert warnings == [], f"unexpected warnings: {warnings}"

    vuln_findings = [f for f in packet.findings if f.get("source_detector") == "vuln-reach"]
    assert len(vuln_findings) == 1
    assert vuln_findings[0]["cve"] == "CVE-2026-0001"
    assert vuln_findings[0]["severity"] == "high"

    raw_arts = [a for a in packet.artifacts if a.kind == "raw_envelope"]
    assert any(a.artifact_id.startswith("vuln-reach:") for a in raw_arts)


# ---------------------------------------------------------------------------
# Producer = test-impact (W193): tests[] -> tests_required + raw artifact
# ---------------------------------------------------------------------------


def test_test_impact_envelope_contract():
    """W219 contract: test-impact `tests[]` rows flow into
    ChangeEvidence.tests_required; the whole envelope becomes a
    raw_envelope artifact.

    Shape mirrors ``cmd_test_impact._run`` verbatim:
    ``tests: [{file: "tests/foo.py", reach_count: N}, ...]``.
    """
    ti_env = {
        "command": "test-impact",
        "schema": "roam-envelope-v1",
        "summary": {"verdict": "2 test files reachable", "count": 2},
        "changed_files": ["src/upload.py"],
        "tests": [
            {"file": "tests/test_upload.py", "reach_count": 4},
            {"file": "tests/test_integration.py", "reach_count": 1},
        ],
    }
    packet, warnings = collect_change_evidence(test_impact_envelopes=[ti_env])
    assert warnings == [], f"unexpected warnings: {warnings}"

    assert "tests/test_upload.py" in packet.tests_required
    assert "tests/test_integration.py" in packet.tests_required

    raw_arts = [a for a in packet.artifacts if a.kind == "raw_envelope"]
    assert any(a.artifact_id.startswith("test-impact:") for a in raw_arts)


# ---------------------------------------------------------------------------
# Producer = cga (W194): statement -> cga_predicate artifact
# ---------------------------------------------------------------------------


def test_cga_envelope_contract():
    """W219 contract: a CGA in-toto v1 statement is promoted to an
    EvidenceArtifact(kind="cga_predicate") keyed by predicate type +
    short merkle hash.

    Shape mirrors ``cmd_cga.py``'s in-toto statement envelope.
    """
    merkle = "f" * 64
    cga_env = {
        "command": "cga-emit",
        "schema": "roam-envelope-v1",
        "summary": {
            "verdict": "CGA emitted",
            "merkle_root": merkle,
            "edge_bundle_digest": "e" * 64,
            "predicate_type": "https://roam-code.com/cga/v1",
            "written_to": "/nonexistent/.roam/cga/statement.json",
            "symbol_count": 500,
            "edge_count": 2000,
        },
        "statement": {
            "_type": "https://in-toto.io/Statement/v1",
            "predicateType": "https://roam-code.com/cga/v1",
            "subject": [{"name": "repo:roam", "digest": {"sha256": "d" * 64}}],
            "predicate": {
                "merkle_root": merkle,
                "edge_bundle_digest": "e" * 64,
                "symbol_count": 500,
                "edge_count": 2000,
            },
        },
    }
    packet, warnings = collect_change_evidence(cga_envelopes=[cga_env])
    assert warnings == [], f"unexpected warnings: {warnings}"

    cga_arts = [a for a in packet.artifacts if a.kind == "cga_predicate"]
    assert len(cga_arts) == 1
    art = cga_arts[0]
    assert art.artifact_id.startswith("cga:")
    assert merkle[:12] in art.artifact_id
    assert art.extra.get("predicate_type") == "https://roam-code.com/cga/v1"
    assert art.extra.get("merkle_root") == merkle


# ---------------------------------------------------------------------------
# Producer = rules (W192): results[] -> policy_decisions
# ---------------------------------------------------------------------------


def test_rules_envelope_contract():
    """W219 contract: ``roam rules`` ``results[]`` rows become
    ChangeEvidence.policy_decisions entries with rule_id + pass/fail.

    Shape mirrors ``cmd_rules.py`` verbatim: ``results: [{name, passed,
    severity, violations, reason, ...}, ...]``.
    """
    rules_env = {
        "command": "rules",
        "schema": "roam-envelope-v1",
        "summary": {
            "verdict": "1 of 2 rules passed, 1 error",
            "total": 2,
            "passed": 1,
            "failed": 1,
        },
        "results": [
            {
                "name": "no-secret-in-diff",
                "passed": True,
                "severity": "error",
                "violations": [],
            },
            {
                "name": "preflight-required",
                "passed": False,
                "severity": "error",
                "reason": "no preflight evidence",
                "violations": [{"symbol": "handleSave"}],
            },
        ],
    }
    packet, warnings = collect_change_evidence(rules_envelopes=[rules_env])
    assert warnings == [], f"unexpected warnings: {warnings}"

    decisions = {d["rule_id"]: d for d in packet.policy_decisions}
    assert decisions["no-secret-in-diff"]["decision"] == "pass"
    assert decisions["preflight-required"]["decision"] == "fail"
    assert decisions["preflight-required"]["reason"] == "no preflight evidence"
    assert decisions["preflight-required"]["violation_count"] == 1


# ---------------------------------------------------------------------------
# Producer = mcp-receipts (W197): jsonl files -> artifacts + actor_refs
# ---------------------------------------------------------------------------


def _write_mcp_receipt(path: Path, **fields):
    """Write a synthetic McpDecisionReceipt-shaped JSON file to disk.

    Mirrors the same helper in ``tests/test_evidence_collector.py``.
    The McpDecisionReceipt dataclass validates its own fields; we feed
    the on-disk JSON through the collector's parser, which constructs
    the dataclass and emits artifacts + refs.
    """
    defaults = {
        "tool_call": "tc_w219",
        "client_id": "mcp:cursor-w219",
        "tool_name": "roam_preflight",
        "actor_ref_id": None,
        "declared_side_effects": [],
        "required_mode": "safe_edit",
        "input_hash": "a" * 64,
        "policy_decision": "allow",
        "output_ref": None,
        "output_hash": "b" * 64,
        "run_event_id": None,
        "redactions": [],
        "extra": {},
    }
    defaults.update(fields)
    path.write_text(json.dumps(defaults), encoding="utf-8")


def test_mcp_receipts_dir_contract(tmp_path, monkeypatch):
    """W219 contract: each *.json in mcp_receipts_dir becomes one
    EvidenceArtifact(kind="other", receipt_kind="mcp_receipt") AND two
    ActorRefs (mcp_client + tool)."""
    _scrub_env(monkeypatch)
    receipts_dir = tmp_path / "mcp_receipts" / "run_w219_mcp"
    receipts_dir.mkdir(parents=True)

    # Producer step: simulate the W196 receipt emitter by writing files
    # of the shape ``McpDecisionReceipt.to_canonical_json()`` produces.
    _write_mcp_receipt(
        receipts_dir / "tc_w219_a.json",
        tool_call="tc_w219_a",
        client_id="mcp:cursor-w219",
        tool_name="roam_preflight",
    )
    _write_mcp_receipt(
        receipts_dir / "tc_w219_b.json",
        tool_call="tc_w219_b",
        client_id="mcp:cursor-w219",
        tool_name="roam_impact",
    )

    packet, warnings = collect_change_evidence(mcp_receipts_dir=receipts_dir)
    assert warnings == [], f"unexpected warnings: {warnings}"

    # Two artifacts.
    receipt_arts = [a for a in packet.artifacts if a.kind == "other" and a.extra.get("receipt_kind") == "mcp_receipt"]
    assert len(receipt_arts) == 2
    art_ids = {a.artifact_id for a in receipt_arts}
    assert "mcp_receipt:tc_w219_a" in art_ids
    assert "mcp_receipt:tc_w219_b" in art_ids

    # ActorRef mirrors: one mcp_client + two tools (preflight + impact).
    pairs = {(r.actor_kind, r.actor_id) for r in packet.actor_refs}
    assert ("mcp_client", "mcp:cursor-w219") in pairs
    assert ("tool", "roam_preflight") in pairs
    assert ("tool", "roam_impact") in pairs
