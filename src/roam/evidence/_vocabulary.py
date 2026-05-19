"""Phase 0 evidence-compiler vocabulary freeze (W174).

These frozenset constants name the closed enumerations every evidence
dataclass validates against. They mirror the ``_SMELL_KIND_TO_CONFIDENCE``
pattern in ``src/roam/commands/cmd_smells.py``: a named module-level
constant whose membership test is O(1) and which raises a clear
``ValueError`` at construction time when a caller passes an unknown
literal.

Why a frozenset and not an ``enum.Enum``?

* Frozensets keep the on-disk evidence shape as plain JSON strings; no
  enum import dance at serialisation time, no need to register a
  ``json.JSONEncoder`` for every kind.
* ``frozenset`` is immutable; ``add`` raises ``AttributeError`` so a
  caller cannot mutate the closed set by accident.
* The frozenset is a closed enumeration — extending it is a deliberate
  source-code edit, not a runtime hack via ``MyEnum._member_map_``.

Drift guards live in ``tests/test_evidence_v0.py``: every kind documented
in the docstrings below has a corresponding entry in the frozenset, and
vice versa.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Subject kinds (what an evidence packet can be about)
# ---------------------------------------------------------------------------

#: Closed enumeration of evidence-subject kinds.
#:
#: Maps to the architecture memo (2026-05-13, lines 111-130) plus the
#: evidence-level additions the memo calls out (``rule``, ``control``,
#: ``run``, ``bundle``, ``finding``, ``test``, ``artifact``). The
#: ``EvidenceSubject.qualified_name`` carries the portable identifier;
#: ``kind`` lets a consumer route to the right rendering / lookup.
#:
#: Kinds and what they mean:
#:
#: * ``symbol``          - a function, class, method, or other code symbol
#: * ``file``            - a single source file
#: * ``module``          - a logical module / package directory
#: * ``directory``       - a directory in the repo (broader than module)
#: * ``endpoint``        - an HTTP / RPC / queue endpoint
#: * ``package``         - a third-party or first-party package identity
#: * ``commit``          - a git commit SHA
#: * ``ledger_entry``    - a single event in ``.roam/runs/<id>/events.jsonl``
#: * ``file_pair``       - two files paired by clone / co-change / move
#: * ``environment``     - a build / runtime environment identity
#: * ``edge``            - a graph edge between two subjects
#: * ``cycle``           - a strongly-connected component
#: * ``diff_region``     - a range of lines in a diff hunk
#: * ``rule``            - a policy / lint rule identity
#: * ``control``         - a governance control identity (OSCAL-ish)
#: * ``run``             - a single agent run (``run_YYYYMMDD_<hash>``)
#: * ``bundle``          - a proof bundle under ``.roam/pr-bundles/``
#: * ``finding``         - a row in the central findings registry
#: * ``test``            - a test case identity
#: * ``artifact``        - a generated artifact (report, SARIF, attestation)
SUBJECT_KINDS: frozenset[str] = frozenset(
    {
        "symbol",
        "file",
        "module",
        "directory",
        "endpoint",
        "package",
        "commit",
        "ledger_entry",
        "file_pair",
        "environment",
        "edge",
        "cycle",
        "diff_region",
        # Evidence-level additions per memo lines 122-130:
        "rule",
        "control",
        "run",
        "bundle",
        "finding",
        "test",
        "artifact",
    }
)


# ---------------------------------------------------------------------------
# Link kinds (typed edges inside an evidence packet)
# ---------------------------------------------------------------------------

#: Closed enumeration of typed edges between evidence subjects.
#:
#: Maps to the architecture memo (lines 136-153). The link answers
#: "what claim is being made about the relationship between source and
#: target?" Use the past-tense / state-verb form that already exists in
#: the memo - new link kinds should be deliberate additions, not free
#: composition at call sites.
#:
#: Kinds and what they mean:
#:
#: * ``derived_from``     - target produced source (provenance)
#: * ``touches``          - source modifies / mentions target
#: * ``calls``            - source invokes target (call-graph edge)
#: * ``tested_by``        - target verifies source (source is asserted)
#: * ``triggered``        - source caused target to run
#: * ``blocked_by``       - target gate stopped source from proceeding
#: * ``allowed_by``       - target rule permitted source to proceed
#: * ``accepted_by``      - target actor signed off on source risk
#: * ``satisfies_control``- source provides evidence for target control
#: * ``maps_to_standard`` - source maps to a named external standard
#: * ``supersedes``       - source replaces target (audit-preserving)
#: * ``mitigates``        - source reduces risk associated with target
LINK_KINDS: frozenset[str] = frozenset(
    {
        "derived_from",
        "touches",
        "calls",
        "tested_by",
        "triggered",
        "blocked_by",
        "allowed_by",
        "accepted_by",
        "satisfies_control",
        "maps_to_standard",
        "supersedes",
        "mitigates",
    }
)


# ---------------------------------------------------------------------------
# Artifact kinds (what an EvidenceArtifact represents)
# ---------------------------------------------------------------------------

#: Closed enumeration of artifact kinds an evidence packet can reference.
#:
#: Maps to the architecture memo "Projections" table (lines 196-204) and
#: the redaction section (lines 268-279). Artifacts can be referenced by
#: path+hash (preferred for large blobs) or embedded inline (small only).
#:
#: Kinds and what they mean:
#:
#: * ``report``          - human-readable Markdown / PDF report
#: * ``sarif``           - SARIF 2.1.0 findings document
#: * ``attestation``     - in-toto / SLSA signed predicate
#: * ``cga_predicate``   - Roam CGA predicate (Code Graph Attestation)
#: * ``bundle``          - proof bundle (``.roam/pr-bundles/<id>/``)
#: * ``trace``           - OpenTelemetry / Jaeger trace dump
#: * ``control_mapping`` - YAML / JSON control-to-evidence map
#: * ``manifest``        - manifest / index file (SBOM-shaped or otherwise)
#: * ``log_excerpt``     - selected log lines (after redaction)
#: * ``raw_envelope``    - raw JSON envelope from a roam subcommand
#: * ``other``           - escape hatch; record kind in ``extra`` instead
ARTIFACT_KINDS: frozenset[str] = frozenset(
    {
        "report",
        "sarif",
        "attestation",
        "cga_predicate",
        "bundle",
        "trace",
        "control_mapping",
        "manifest",
        "log_excerpt",
        "raw_envelope",
        "other",
    }
)


# ---------------------------------------------------------------------------
# Claim severities (severity scale for evidence claims and findings)
# ---------------------------------------------------------------------------

# CLAIM_SEVERITIES - 5-tier evidence vocabulary (critical / high / medium /
# low / info). Preserves CVSS-style round-trip when ingesting external feeds
# (OSV, npm-audit, trivy, GitHub Advisory DB) without lossy normalization.
#
# NOT the SARIF projection - see roam.output._severity.SEVERITY_LEVELS for
# the 4-tier canonical (critical / error / warning / info) that SARIF
# consumers see. roam.output._severity.SEVERITY_ALIASES maps high->warning,
# medium->info, low->info at the SARIF emission boundary (W547 / W564).

#: Closed enumeration of claim severities.
#:
#: Mirrors SARIF / Code Scanning severity vocabulary so projections can
#: map straight through. Use ``info`` (not ``"none"``) for the lowest
#: tier so consumers reading the JSON don't confuse it with absence.
#:
#: Severities (high to low):
#:
#: * ``critical`` - production-impacting; demands immediate action
#: * ``high``     - reachable risk; review before merge
#: * ``medium``   - localized risk; review opportunistically
#: * ``low``      - cosmetic / hygiene; informational
#: * ``info``     - neutral observation; carries no risk claim
CLAIM_SEVERITIES: frozenset[str] = frozenset(
    {
        "critical",
        "high",
        "medium",
        "low",
        "info",
    }
)


# ---------------------------------------------------------------------------
# Redaction reasons (why a field was masked from the evidence)
# ---------------------------------------------------------------------------

#: Closed enumeration of redaction reasons.
#:
#: Borrows the OPA decision-log idea: when a field is masked, record the
#: REASON in the packet's ``redactions[]`` array rather than silently
#: dropping the field. The architecture memo's redaction section (lines
#: 265-279) calls this out as a hard requirement.
#:
#: Reasons and what they mean:
#:
#: * ``secret``                 - credential / token / key material
#: * ``pii``                    - personal identifying information
#: * ``sensitive_content``      - business-sensitive payload (not PII)
#: * ``size_limit``             - artifact exceeded inline-size budget
#: * ``policy``                 - blocked by an explicit redaction rule
#: * ``user_opt_in_required``   - raw context capture needs opt-in
#: * ``machine_local_path``     - W241: collector rejected a developer-home
#:                                / credential-directory absolute path on
#:                                an artifact reference (path field cleared,
#:                                content_hash retained as canonical identity)
#: * ``schema_strict``          - W241: collector applied a closed-allowlist
#:                                whitelist when inlining a producer envelope
#:                                (only known-safe keys survived the copy)
#: * ``producer_not_available`` - W261: an evidence-question producer is not
#:                                wired into this pipeline yet. Distinct from
#:                                ``user_opt_in_required`` (which means "raw
#:                                context capture needs explicit opt-in") and
#:                                from ``policy`` (which means an active rule
#:                                masked the field). ``producer_not_available``
#:                                means the data source itself is missing. The
#:                                first concrete case is Q8 (accept) on
#:                                ``roam pr-replay``: PR Replay has no human-
#:                                approvals harvester today, so the packet
#:                                names the gap rather than silently emitting
#:                                empty ``approvals`` / ``accepted_risks``
#:                                tuples.
REDACTION_REASONS: frozenset[str] = frozenset(
    {
        "secret",
        "pii",
        "sensitive_content",
        "size_limit",
        "policy",
        "user_opt_in_required",
        "machine_local_path",
        "schema_strict",
        "producer_not_available",
    }
)


# ---------------------------------------------------------------------------
# Actor kinds (W182 - agentic assurance crosswalk: identity)
# ---------------------------------------------------------------------------

#: Closed enumeration of actor kinds that can participate in a change.
#:
#: Maps to the agentic-assurance crosswalk memo
#: (``(internal memo)``) §"Build deltas"
#: item 1. The crosswalk frames Roam's distinguishing claim as
#: ``identity + authority + evidence`` — ``ACTOR_KINDS`` populates the
#: identity axis on ``ChangeEvidence.actor_refs[]``.
#:
#: Kinds and what they mean:
#:
#: * ``human``       - human committer / approver / reviewer
#: * ``agent``       - AI coding agent (Cursor, Claude Code, Codex, ...)
#: * ``mcp_client``  - MCP client process speaking to the Roam MCP server
#: * ``tool``        - one individual tool invocation (finer-grained
#:                     than the client - e.g. a single ``roam_preflight``
#:                     tool call inside an MCP session)
#: * ``ci_runner``   - CI provider job / runner (GitHub Actions, ...)
#: * ``external``    - unknown / unattributed origin; escape hatch
ACTOR_KINDS: frozenset[str] = frozenset(
    {
        "human",
        "agent",
        "mcp_client",
        "tool",
        "ci_runner",
        "external",
    }
)


# ---------------------------------------------------------------------------
# Authority kinds (W182 - agentic assurance crosswalk: authority)
# ---------------------------------------------------------------------------

#: Closed enumeration of authority kinds that gated a change.
#:
#: Maps to the agentic-assurance crosswalk memo §"Build deltas" item 2.
#: ``AUTHORITY_KINDS`` populates the authority axis on
#: ``ChangeEvidence.authority_refs[]`` - each entry names the piece of
#: authority (mode, permit, lease, rule, approval, token scope) under
#: which the change was permitted to proceed.
#:
#: Kinds and what they mean:
#:
#: * ``mode``         - the active Roam mode at the time of the change
#:                      (``read_only`` / ``safe_edit`` / ``migration`` /
#:                      ``autonomous_pr``)
#: * ``permit``       - an explicit ``roam permit`` override identity.
#:                      W198 closed the original facade gap: today,
#:                      ``roam permit`` retains its verdict facade default
#:                      (ALLOW / REVIEW / BLOCK) when invoked without a
#:                      subcommand, AND ``roam permit issue --persist``
#:                      now writes a stable permit_id to
#:                      ``.roam/permits/<id>.json``. The
#:                      ``AuthorityRef(authority_kind="permit")`` slot
#:                      binds to that persisted identity; a permit row
#:                      missing ``permit_id`` (still possible from older
#:                      synthetic envelopes) lands on the verdict facade
#:                      path and auto-stamps ``extra["facade"] = True``
#:                      per :class:`roam.evidence.refs.AuthorityRef`.
#: * ``lease``        - a multi-agent lease claim
#: * ``policy_rule``  - a rule id from ``rules.yml`` (or equivalent)
#: * ``approval``     - a human approval id (PR review, etc.)
#: * ``token_scope``  - a hash of the token scope (NEVER the raw token)
AUTHORITY_KINDS: frozenset[str] = frozenset(
    {
        "mode",
        "permit",
        "lease",
        "policy_rule",
        "approval",
        "token_scope",
    }
)


# ---------------------------------------------------------------------------
# Environment kinds (W182 - agentic assurance crosswalk: where it ran)
# ---------------------------------------------------------------------------

#: Closed enumeration of execution-environment kinds.
#:
#: Maps to the agentic-assurance crosswalk memo §"Build deltas" item 3.
#: ``ENV_KINDS`` populates ``ChangeEvidence.environment_refs[]`` and
#: answers "where did this change execute?" (CI job vs local box;
#: which workspace; which branch range).
#:
#: Kinds and what they mean:
#:
#: * ``ci_job``        - CI provider job identifier (e.g.
#:                       ``github.com/owner/repo/actions/runs/12345``)
#: * ``local_run``     - a local invocation on a developer machine
#: * ``workspace``     - workspace / repo identifier (clone path or
#:                       canonical repo id)
#: * ``branch_range``  - a branch + git-range pair (e.g.
#:                       ``main:abc1234..def5678``)
ENV_KINDS: frozenset[str] = frozenset(
    {
        "ci_job",
        "local_run",
        "workspace",
        "branch_range",
    }
)


# ---------------------------------------------------------------------------
# Actor trust tiers (W211 - identity provenance)
# ---------------------------------------------------------------------------

#: Closed enumeration of actor trust tiers.
#:
#: Per the W211 directive: when an evidence packet records an
#: ``ActorRef``, the consumer needs to know *how trustworthy that
#: identity is*. A git author email is plain-text metadata (anyone can
#: set ``git config user.email``); a CI provider's OIDC token is a
#: cryptographic attestation. Both are valid identity surfaces but they
#: occupy different tiers on the trust ladder, and downstream consumers
#: (e.g. governance reports, automated approval gates) need to be able
#: to discriminate.
#:
#: The default tier is :data:`"unknown"` (the most-conservative tier)
#: so that unset paths are honest about the absence of identity
#: provenance rather than silently claiming a higher tier.
#:
#: Tiers (loosely high to low confidence):
#:
#: * ``verified_ci``        - CI provider attestation (GitHub OIDC, ...)
#: * ``git_author``         - git config user.email (no crypto verify)
#: * ``local_env``          - env var (``ROAM_AGENT_ID`` and similar)
#: * ``self_reported_agent``- caller-arg / CLI flag from the agent
#: * ``unknown``            - no identity surface available (default)
ACTOR_TRUST_TIERS: frozenset[str] = frozenset(
    {
        "verified_ci",
        "git_author",
        "local_env",
        "self_reported_agent",
        "unknown",
    }
)


# ---------------------------------------------------------------------------
# Authority sources (W211 - where the authority claim came from)
# ---------------------------------------------------------------------------

#: Closed enumeration of authority sources.
#:
#: Per the W211 directive: an ``AuthorityRef`` names a piece of
#: authority that gated the change (a mode, a permit, an approval, ...)
#: but it does NOT by itself say *where the producer learned about
#: that authority*. ``AUTHORITY_SOURCES`` adds that provenance axis -
#: separating "this came from the active mode declaration" from "this
#: came from an explicit ``roam permit`` override" from "the collector
#: inferred a default because nothing was declared".
#:
#: The default source is :data:`"inferred_fallback"` because the most
#: common populating path today is the W176 collector inferring a value
#: when no explicit declaration was found. Marking that path honestly
#: keeps downstream consumers from over-trusting inferred authority.
#:
#: Sources and what they mean:
#:
#: * ``mode``              - from active mode declaration
#: * ``permit``            - from explicit ``roam permit`` override.
#:                           W198 note: ``roam permit`` is currently a
#:                           verdict-only facade and does NOT persist a
#:                           permit_id. Pair this source with the
#:                           ``AuthorityRef.extra["facade"] = True``
#:                           marker (set automatically by ``AuthorityRef``
#:                           when ``source="permit"`` and no permit_id
#:                           is present in ``extra``) so consumers can
#:                           see the facade signal explicitly.
#: * ``rule_config``       - from ``.roam/rules.yml``
#: * ``ci_policy``         - from CI branch protection / required checks
#: * ``human_approval``    - from a recorded approval event
#: * ``inferred_fallback`` - collector inferred (default mode, etc.)
AUTHORITY_SOURCES: frozenset[str] = frozenset(
    {
        "mode",
        "permit",
        "rule_config",
        "ci_policy",
        "human_approval",
        "inferred_fallback",
    }
)


# ---------------------------------------------------------------------------
# Claim confidence basis (W210 - per-claim confidence band)
# ---------------------------------------------------------------------------

#: Closed enumeration of confidence bases for a single evidence claim.
#:
#: Maps to the 2026-05-14 strategic directive item 1: every finding row
#: produced by a detector should carry a ``confidence_basis`` field naming
#: which level of the producer/collector chain originated the claim.
#: Findings on ``ChangeEvidence`` are typed as ``Mapping[str, Any]``
#: rather than a dataclass (Phase 1 contract), so this frozenset is a
#: documentary-and-validation vocabulary - collectors that stamp
#: ``confidence_basis`` on a finding-row dict should pick one of these
#: literals; consumers reading findings can route on the literal value.
#: There is no construction-time validator on the finding dict itself
#: (it stays a Mapping), but the literal set is a closed enumeration
#: so a lint or schema check can verify producers stay in-vocabulary.
#:
#: Bases (strongest to weakest signal):
#:
#: * ``direct``           - producer emitted this directly (best signal:
#:                          a detector said "I found X")
#: * ``derived``          - collector computed from producer output
#:                          (a transform / aggregation over direct facts)
#: * ``inferred``         - collector inferred from indirect signals
#:                          (heuristic, statistical, cross-reference)
#: * ``legacy_fallback``  - legacy scalar / probe path (lowest signal;
#:                          retained for backward compat with pre-W210
#:                          producers that don't stamp a basis explicitly)
CLAIM_CONFIDENCES: frozenset[str] = frozenset(
    {
        "direct",
        "derived",
        "inferred",
        "legacy_fallback",
    }
)


# ---------------------------------------------------------------------------
# Policy-decision verdicts (W279 - promotion of policy_decisions to typed
# dataclass; see ``roam.evidence.policy.PolicyDecision``)
# ---------------------------------------------------------------------------

# POLICY_DECISIONS - 9-tier policy-evaluation verdict vocabulary used by
# ChangeEvidence.policy_decisions[].decision. This is the policy-rule
# decision layer; OTHER verdict-like vocabularies in roam are:
#
# - Permit facade (src/roam/commands/cmd_permit.py): ALLOW / REVIEW / BLOCK -
#   3-tier coarse risk verdict for human-facing approval flows. Mapped onto
#   POLICY_DECISIONS via allow=ALLOW, deny=BLOCK, escalate=REVIEW at the
#   collector boundary.
# - Rule envelope (src/roam/rules/engine.py): passed=True / passed=False -
#   2-tier per-rule pass/fail boolean on the rules-engine result dict. Rolls
#   up into POLICY_DECISIONS pass / fail at the decision level.
#
# The three vocabularies are layered by intent: rule envelope (most
# granular per-rule boolean) -> POLICY_DECISIONS (mid; named verdict over
# a rule or gate) -> permit facade (coarsest; human-facing approval
# tri-state). They are NOT aliases of each other; each layer owns a
# distinct decision granularity.

#: Closed enumeration of policy-decision verdict literals.
#:
#: Maps to the W279 directive: ``policy_decisions[]`` entries on
#: ``ChangeEvidence`` carry one of these verdicts on the ``decision``
#: field. The frozenset is the construction-time guard against silent
#: producer drift (e.g. a future ``decision="approved"`` typo that the
#: free-form dict shape would let through).
#:
#: Verdicts and what they mean (producer/site cross-reference in
#: parentheses):
#:
#: * ``pass``           - rule evaluated and passed (rules envelope,
#:                        audit-trail-verify chain-valid case)
#: * ``fail``           - rule evaluated and failed (rules envelope,
#:                        audit-trail-verify per-entry tamper findings)
#: * ``allow``          - authority gate evaluated to allow (permits
#:                        present, leases held)
#: * ``deny``           - authority gate evaluated to deny (a future
#:                        ``roam permit --persist`` deny-side producer)
#: * ``escalate``       - rule evaluation deferred to a higher tier
#:                        (e.g. mode escalation, human-review gate)
#: * ``redact``         - rule produced a redaction decision (the
#:                        producer chose to mask a field rather than
#:                        emit ``pass`` / ``fail``)
#: * ``not_evaluated``  - rule was registered but not executed during
#:                        this change scope (constitution gates whose
#:                        commands didn't run; permits/leases whose
#:                        evaluation result wasn't captured)
#: * ``unknown``        - rule emitted neither ``passed`` nor an
#:                        explicit ``decision`` literal (rules envelope
#:                        fallback path - kept in the closed set so
#:                        the legacy column doesn't silently leak)
#: * ``would_deny_dry_run`` - MCP-P1.1 shadow-mode marker. The 4-mode
#:                        gate WOULD have denied this call under
#:                        ``ROAM_MODE_ENFORCEMENT=1``, but
#:                        ``ROAM_MODE_DRY_RUN=1`` was set so the call
#:                        was allowed to proceed for observe-only
#:                        rollout. Distinct from ``deny`` (which is the
#:                        steady-state advisory verdict when enforcement
#:                        is off without dry-run) — dry-run is an
#:                        explicit operator opt-in to preview policy
#:                        before flipping enforcement.
POLICY_DECISIONS: frozenset[str] = frozenset(
    {
        "pass",
        "fail",
        "allow",
        "deny",
        "escalate",
        "redact",
        "not_evaluated",
        "unknown",
        "would_deny_dry_run",
    }
)


# ---------------------------------------------------------------------------
# Provenance sources (W282 - per-field evidence provenance vocabulary)
# ---------------------------------------------------------------------------

#: Closed enumeration of provenance sources for evidence fields.
#:
#: Distinct from ``CLAIM_CONFIDENCES`` (W210): confidence answers *how
#: strongly do we trust this claim?*, provenance answers *where did the
#: value come from?*. Both axes are needed - a ``"direct"`` confidence
#: claim sourced from ``ci_env_var`` carries a different audit weight
#: than the same ``"direct"`` confidence claim sourced from a
#: ``cli_flag`` the agent set itself.
#:
#: This wave (W282) is vocabulary + helper ONLY. No producer wires this
#: in yet; the helper :func:`roam.evidence.provenance.provenance_label`
#: provides a compact API future producers can use to stamp the source
#: into the free-form ``extra["provenance"]`` slot on ``ActorRef`` /
#: ``AuthorityRef`` / ``EnvironmentRef`` without a schema change. The
#: subsequent wave (W289+) wires real producers to call the helper.
#:
#: Sources and what they mean:
#:
#: * ``ci_env_var``         - sourced from a CI provider environment
#:                            variable. W251 matrix covers
#:                            ``GITHUB_ACTOR`` / ``GITLAB_USER_LOGIN`` /
#:                            ``BUILDKITE_BUILD_AUTHOR`` etc. Use this
#:                            tier when the value was read directly from
#:                            a CI-provider env var (different trust
#:                            level from a generic local env var).
#: * ``git_config``         - sourced from ``git config user.email`` or
#:                            ``git config user.name`` (or the
#:                            equivalent committer fields). Plain-text
#:                            metadata; the canonical "git author"
#:                            identity surface.
#: * ``run_ledger``         - sourced from the active ``.roam/runs/<id>``
#:                            event ledger entry. Carries the agent /
#:                            mode declarations the producer recorded at
#:                            run-start time.
#: * ``cli_flag``           - sourced from an explicit ``--agent`` /
#:                            ``--mode`` / ``--actor`` CLI flag passed
#:                            to the producer at invocation time.
#: * ``env_var``            - sourced from a NON-CI environment variable
#:                            (``ROAM_AGENT_ID`` and similar). Keep this
#:                            tier distinct from ``ci_env_var`` so
#:                            consumers can differentiate "CI provider
#:                            asserted this" from "local shell exported
#:                            this".
#: * ``producer_envelope``  - sourced from an upstream Roam JSON
#:                            envelope (pr-bundle, pr-risk, runs, ...).
#:                            The collector lifted the value out of an
#:                            already-emitted producer envelope rather
#:                            than reading the underlying surface
#:                            itself.
#: * ``audit_trail``        - sourced from ``.roam/audit-trail.jsonl``.
#:                            Used when the value originates in the
#:                            tamper-evident audit-trail rather than
#:                            the run ledger (e.g. cross-run identity
#:                            claims that pre-date the active run).
#: * ``mcp_receipt``        - sourced from a W196 MCP decision receipt
#:                            at ``.roam/mcp_receipts/<run_id>/<call>.json``.
#:                            Records the MCP-tool-call surface as the
#:                            originating provenance.
#: * ``inferred``           - the collector derived the value
#:                            heuristically WITHOUT a single canonical
#:                            source (e.g. cross-referenced multiple
#:                            partial surfaces). Distinct from
#:                            ``unknown`` (which means "we have no idea
#:                            where this came from") and from
#:                            ``CLAIM_CONFIDENCES`` ``"inferred"`` (the
#:                            confidence basis; orthogonal axis).
#: * ``unknown``            - provenance unknown. Default for legacy
#:                            data and the most-conservative tier so
#:                            unset paths stay honest rather than
#:                            silently claiming a higher provenance
#:                            tier.
PROVENANCE_SOURCES: frozenset[str] = frozenset(
    {
        "ci_env_var",
        "git_config",
        "run_ledger",
        "cli_flag",
        "env_var",
        "producer_envelope",
        "audit_trail",
        "mcp_receipt",
        "inferred",
        "unknown",
    }
)


# ---------------------------------------------------------------------------
# GitHub PR review states (W247a - real approvals producer, parser half)
# ---------------------------------------------------------------------------

#: Closed enumeration of GitHub PR review states.
#:
#: GitHub's PR review API (``GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews``)
#: returns review rows whose ``state`` field is one of these literals. The
#: W247a parser/normalizer (``roam.evidence.github_reviews``) validates every
#: incoming review row against this frozenset and raises ``ValueError`` for
#: anything else. The closed enumeration documents the exact GitHub vocabulary
#: the parser accepts and protects against producer drift (e.g. a future
#: GitHub API addition that the parser hasn't been updated to handle).
#:
#: States and what they mean (per GitHub REST API docs):
#:
#: * ``APPROVED``          - reviewer approved the PR. Becomes an
#:                            ``ApprovalRecord`` ONLY when the review's
#:                            ``commit_id`` matches the current head commit
#:                            (older approvals are "outdated" in GitHub UI
#:                            once subsequent commits land).
#: * ``CHANGES_REQUESTED`` - reviewer requested changes. Becomes a
#:                            ``PolicyDecision(decision="deny", ...)`` row
#:                            on the evidence packet - a blocker / policy
#:                            signal, NOT an approval.
#: * ``COMMENTED``         - reviewer left comments without approving or
#:                            requesting changes. Filtered out by the parser:
#:                            neither approval nor blocker.
#: * ``DISMISSED``         - a prior review was dismissed (by the PR author
#:                            or an admin). Filtered out: no current claim.
#: * ``PENDING``           - reviewer started a review but hasn't submitted.
#:                            Filtered out: review isn't on the record yet.
GITHUB_REVIEW_STATES: frozenset[str] = frozenset(
    {
        "APPROVED",
        "CHANGES_REQUESTED",
        "COMMENTED",
        "DISMISSED",
        "PENDING",
    }
)


# ---------------------------------------------------------------------------
# Reference-removal verdicts (W1156 - closed-enum substrate for
# cmd_refs_text + cmd_delete_check; W1134 audit recommendation)
# ---------------------------------------------------------------------------

# REFERENCE_REMOVAL_VERDICTS - closed-enum domain vocabulary for the
# reference-removal-safety axis. Distinct from POLICY_DECISIONS (which
# names policy-rule evaluation: pass/fail/allow/deny/...). Reference
# removal asks "is this identifier still being called?" - a graph-
# reachability question, not a policy question.
#
# cmd_refs_text emits: safe_to_remove / review / load_bearing
# cmd_delete_check emits: safe / likely_safe / break_risk
# Both subsets share the closed-enum invariant.
#
# Canonical form: lowercase + underscore (matches POLICY_DECISIONS
# membership convention). The CLI text-output layer renders as
# UPPERCASE-WITH-HYPHENS (e.g. "SAFE-TO-REMOVE", "BREAK-RISK") - that's a
# display concern, not a vocab concern. Validators at the producer
# boundary normalize via ``.lower().replace("-", "_")`` before membership
# check.
#: Closed enumeration of reference-removal verdict literals.
REFERENCE_REMOVAL_VERDICTS: frozenset[str] = frozenset(
    {
        "safe_to_remove",
        "review",
        "load_bearing",
        "safe",
        "likely_safe",
        "break_risk",
    }
)


__all__ = [
    "SUBJECT_KINDS",
    "LINK_KINDS",
    "ARTIFACT_KINDS",
    "CLAIM_SEVERITIES",
    "REDACTION_REASONS",
    "ACTOR_KINDS",
    "AUTHORITY_KINDS",
    "ENV_KINDS",
    "ACTOR_TRUST_TIERS",
    "AUTHORITY_SOURCES",
    "CLAIM_CONFIDENCES",
    "POLICY_DECISIONS",
    "PROVENANCE_SOURCES",
    "GITHUB_REVIEW_STATES",
    "REFERENCE_REMOVAL_VERDICTS",
]
