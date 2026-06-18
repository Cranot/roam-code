"""W761-critique — severity-vocabulary parity for cmd_critique.

Pattern 3a (cross-command metric divergence). cmd_critique emits a
``severity`` field on each finding (closed-vocab 4-tier
``high``/``medium``/``low``/``info`` per the :class:`Finding`
constructor in :mod:`roam.critique.checks`), an envelope
``severity_breakdown`` bucket via the aggregator, a SARIF projection
via ``critique_to_sarif``, and an exit-code 5 gate on the ``high``
bucket count. Pre-W547 / pre-W566 every step had drift potential. The
command is now wired onto the canonical helpers in
:mod:`roam.output._severity`:

* :data:`SEVERITY_LEVELS` — 4-tier canonical roam->SARIF vocabulary.
* :data:`SEVERITY_ALIASES` — CVSS spellings (``high``/``medium``/``low``)
  + ``note`` + ``unknown`` resolving back into the canonical set.
* :func:`severity_rank` — single source of truth for severity ORDER.
* :func:`severity_breakdown` — single bucketing helper.
* :func:`to_sarif_level` — single SARIF level projection.

What this regression test pins
------------------------------

1. **cmd_critique sources severity from the canonical layer.** The
   in-memory ``Finding`` constructor in :mod:`roam.critique.checks`
   declares a closed 4-tier vocab; the aggregator delegates bucketing
   to ``severity_breakdown`` (W566); the SARIF projection delegates to
   ``_to_level`` (W547); the canonical-risk projection inside
   cmd_critique covers the full 9-key canonical vocab.
2. **No inline ``severity_rank``-shaped dict literal** in cmd_critique
   or the aggregator. The W564 drift-guard predicate (>=2 string-keyed
   integer pairs over the severity vocab) flags none. The
   ``_CRITIQUE_SEVERITY_TO_RISK_LEVEL`` table is a Pattern-3-distinct
   *severity-to-risk-LEVEL* projection (str -> str), NOT a rank dict
   (str -> int) — outside W547 severity-rank scope by design.
3. **The Finding constructor severity vocab is the 4-tier
   ``{high, medium, low, info}``.** Every emit site inside
   :mod:`roam.critique.checks` constrains severity to that vocab; a
   widened emit ``severity="critical"`` would silently drop out of the
   aggregator's zero-padded 4-tier breakdown bucket.
4. **The W566 critique-aggregator zero-padded 4-tier breakdown
   contract is preserved.** Mirrors
   ``test_w566_breakdown_reproduces_critique_contract`` in
   :mod:`tests.test_w547_severity_drift`: empty findings -> zero-padded
   4-tier dict; non-empty -> per-level counts in vocab order.
5. **Every Finding emit value lies in the canonical 9-key vocab.**
   Static scan of :mod:`roam.critique.checks` source: every literal
   ``severity="..."`` token resolves to a finite ``severity_rank() >= 0``
   (no -1 collapse).
6. **The 9-key ``_CRITIQUE_SEVERITY_TO_RISK_LEVEL`` table covers the
   full canonical vocab.** A severity label that ``severity_rank()``
   accepts (rank >= 0) must also have a defined risk-level projection
   so the per-finding walk never falls into the unknown-label
   safe-floor branch on a CANONICAL label (the safe-floor is reserved
   for genuinely unrecognised labels — typos / future spellings).
7. **The exit-code 5 gate keys on the ``high`` bucket** and the
   aggregator zero-pads ``high`` into the breakdown so the gate
   expression ``severity_breakdown.get("high", 0) > 0`` is well-defined
   on every result (clean or not). Drift-guard: a future aggregator
   refactor that drops the zero-padded ``high`` key would silently
   disable the CI gate.
8. **cmd_critique exposes no ``--severity`` click.Choice option.** The
   gate semantics are exit-code 5 on the aggregated ``high`` count, not
   a CLI floor filter — distinct from cmd_secrets / cmd_vulns. Drift-
   guard: a future ``--severity`` addition must use the canonical
   9-key vocab + ``all`` sentinel (W1005-followup-C pattern) so cross-
   command floor semantics stay aligned.

Scope discipline (per task brief)
---------------------------------

* ONLY exercises cmd_critique + the aggregator + Finding closed-vocab.
* Does NOT modify ``roam.output._severity`` itself (pinned by
  :mod:`tests.test_w547_severity_drift`).
* Does NOT modify cmd_vulns / cmd_secrets (W761-vulns-secrets closed).
* Does NOT modify cmd_smells (W1005-followup-C closed).
* Preserves the W566 zero-padded critique-aggregator contract.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# Canonical helpers under test
from roam.output._severity import (
    SEVERITY_ALIASES,
    SEVERITY_LEVELS,
    severity_rank,
)
from tests._helpers.repo_root import repo_root

SRC_ROOT = repo_root() / "src" / "roam"
CMD_CRITIQUE = SRC_ROOT / "commands" / "cmd_critique.py"
CRITIQUE_AGGREGATOR = SRC_ROOT / "critique" / "aggregator.py"
CRITIQUE_CHECKS = SRC_ROOT / "critique" / "checks.py"

# The 9-key severity vocab severity_rank() accepts as defined keys
# (plus aliases). Used to assert emit values land in-vocab.
_CANONICAL_SEVERITY_VOCAB: frozenset[str] = SEVERITY_LEVELS | frozenset(SEVERITY_ALIASES.keys())


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ---------------------------------------------------------------------------
# 1. cmd_critique + aggregator source severity vocabulary from the canonical
# layer
# ---------------------------------------------------------------------------


def test_critique_aggregator_imports_canonical_severity_helpers() -> None:
    """The aggregator sources bucketing + ordering from roam.output._severity."""
    text = CRITIQUE_AGGREGATOR.read_text(encoding="utf-8")
    assert "roam.output._severity" in text, "W761-critique: critique.aggregator must import from roam.output._severity"
    # severity_breakdown helper is the W566 canonical bucketing entry
    # point — used for the zero-padded 4-tier critique contract.
    assert "severity_breakdown" in text, "W761-critique: critique.aggregator must reference severity_breakdown"
    # severity_rank is the W564 canonical ordering entry point —
    # imported as ``_canonical_severity_rank`` with a polarity-flip
    # alias for legacy ``sorted()`` call sites.
    assert "severity_rank" in text, "W761-critique: critique.aggregator must reference severity_rank"


def test_critique_sarif_projection_uses_canonical_to_level() -> None:
    """The cmd_critique SARIF projection delegates to canonical ``_to_level``.

    Located in :mod:`roam.output.sarif` (the central SARIF substrate)
    rather than cmd_critique itself per the central-SARIF convention.
    Drift-guard: a future inline SARIF table in cmd_critique would
    re-introduce Pattern 3a vocabulary divergence between the CLI
    output and the SARIF level a CI gate keys off.
    """
    sarif_text = (SRC_ROOT / "output" / "sarif.py").read_text(encoding="utf-8")
    # ``critique_to_sarif`` exists and references ``_to_level`` for the
    # severity -> SARIF level projection (no inline mapping).
    assert "def critique_to_sarif" in sarif_text, "W761-critique: critique_to_sarif must live in roam.output.sarif"
    # Pull the critique_to_sarif function body and assert it calls _to_level
    tree = ast.parse(sarif_text)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "critique_to_sarif":
            fn = node
            break
    assert fn is not None
    body_text = ast.get_source_segment(sarif_text, fn) or ""
    assert "_to_level(" in body_text, (
        "W761-critique: critique_to_sarif must delegate severity->SARIF "
        "level via _to_level (the canonical W547 projection)"
    )


# ---------------------------------------------------------------------------
# 2. No inline severity-rank dict literal in cmd_critique or aggregator
# ---------------------------------------------------------------------------


def _has_inline_severity_rank_dict(tree: ast.Module) -> list[int]:
    """Return line numbers of any inline ``{severity: int}`` rank dict.

    Mirrors the W564 drift-guard predicate: at least 2 string-keyed
    integer pairs where every string key is in the canonical severity
    vocab. The ``_CRITIQUE_SEVERITY_TO_RISK_LEVEL`` table in cmd_critique
    is a str -> str projection (Pattern-3-distinct severity-to-risk-LEVEL
    axis), NOT a rank dict — outside this check by design.
    """
    rank_vocab = _CANONICAL_SEVERITY_VOCAB | frozenset(s.upper() for s in _CANONICAL_SEVERITY_VOCAB)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs: list[tuple[str, int]] = []
        has_non_sev_key = False
        for k, v in zip(node.keys, node.values):
            if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                continue
            if k.value not in rank_vocab:
                has_non_sev_key = True
                continue
            if not isinstance(v, ast.Constant) or isinstance(v.value, bool):
                continue
            if not isinstance(v.value, int):
                continue
            pairs.append((k.value, v.value))
        if has_non_sev_key or len(pairs) < 2:
            continue
        if len({val for _, val in pairs}) >= 2:
            hits.append(node.lineno)
    return hits


def test_cmd_critique_no_inline_severity_rank_dict() -> None:
    """cmd_critique must not carry a private severity->int rank dict."""
    hits = _has_inline_severity_rank_dict(_parse(CMD_CRITIQUE))
    assert not hits, (
        f"W761-critique: cmd_critique has inline severity-rank dict "
        f"literal at lines {hits} — use severity_rank() from "
        f"roam.output._severity"
    )


def test_critique_aggregator_no_inline_severity_rank_dict() -> None:
    """critique.aggregator must not carry a private severity->int rank dict.

    The aggregator imports the canonical ``severity_rank(severity)``
    helper under a private alias (W564). No inline table — drift-guard
    ensures it stays that way.
    """
    hits = _has_inline_severity_rank_dict(_parse(CRITIQUE_AGGREGATOR))
    assert not hits, (
        f"W761-critique: critique.aggregator has inline severity-rank dict "
        f"literal at lines {hits} — use severity_rank() from "
        f"roam.output._severity"
    )


# ---------------------------------------------------------------------------
# 3. The Finding constructor severity vocab is the closed 4-tier
# ---------------------------------------------------------------------------


def test_critique_finding_emit_values_are_4_tier_in_vocab() -> None:
    """Every literal ``severity="..."`` token in checks.py is in 4-tier.

    The :class:`Finding` constructor's severity annotation
    (``# "high" | "medium" | "low" | "info"``) is informational; the
    enforcement is "every emit site emits one of those four". Drift-
    guard: introducing ``severity="critical"`` without widening the
    aggregator's vocab call would silently drop the finding from the
    zero-padded 4-tier breakdown (which feeds the CI gate).
    """
    text = CRITIQUE_CHECKS.read_text(encoding="utf-8")
    tree = ast.parse(text)

    expected_vocab = frozenset({"high", "medium", "low", "info"})
    emit_values: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        # ``severity="..."`` as a keyword argument in a Call (Finding(...))
        if isinstance(node, ast.keyword) and node.arg == "severity":
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                emit_values.append((node.lineno, node.value.value))
        # ``severity = "high"`` (or "medium") as an Assign whose RHS is a
        # string Constant — covers the branched-assignment pattern in
        # ``check_clones_not_edited`` / ``check_impact``.
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "severity":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        emit_values.append((node.lineno, node.value.value))
        # ``severity = "high" if ... else "medium"`` — IfExp body/orelse
        if isinstance(node, ast.IfExp):
            parent_check_severity = False
            # Best-effort: parent walk via ast.iter_child_nodes is awkward;
            # instead pick up any IfExp whose body/orelse is a 4-tier
            # string constant. This catches the ternary assignment in
            # ``check_impact``.
            for child in (node.body, node.orelse):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    if child.value in expected_vocab | frozenset({"critical", "error", "warning", "note", "unknown"}):
                        emit_values.append((node.lineno, child.value))
                        parent_check_severity = True
            _ = parent_check_severity  # marker — silences unused var lint

    # Dedupe + sort for stable error messages
    emit_set = sorted(set(emit_values))
    bad = [(lineno, sev) for lineno, sev in emit_set if sev not in expected_vocab]
    # Filter: only flag tokens that are PLAUSIBLY severity-shaped (one
    # of the canonical 9-key vocab). Random string constants in the
    # file (e.g. file paths) get filtered out by the in-canonical
    # check below.
    canonical_bad = [
        (lineno, sev)
        for lineno, sev in bad
        if sev in _CANONICAL_SEVERITY_VOCAB | frozenset(s.upper() for s in _CANONICAL_SEVERITY_VOCAB)
    ]
    assert not canonical_bad, (
        f"W761-critique: critique.checks emits severity outside the 4-tier "
        f"vocab at {canonical_bad}; either narrow to {sorted(expected_vocab)} "
        f"OR widen the aggregator's vocab call in critique.aggregator.py"
    )

    # Positive assertion: we found at least one emit site (so the scan
    # actually exercised the file).
    assert emit_set, (
        "W761-critique: severity emit scan found zero sites — the AST "
        "predicate may have stopped matching the Finding constructor shape; "
        "re-tune the walker before trusting the negative result."
    )


# ---------------------------------------------------------------------------
# 4. W566 critique-aggregator zero-padded 4-tier contract preserved
# ---------------------------------------------------------------------------


def test_critique_aggregator_w566_zero_padded_4_tier_preserved() -> None:
    """Reproduces the W566 zero-padded 4-tier critique contract.

    Mirrors :func:`test_w566_breakdown_reproduces_critique_contract` in
    :mod:`tests.test_w547_severity_drift`. The contract:

    * Empty findings -> ``{"high": 0, "medium": 0, "low": 0, "info": 0}``
    * Non-empty findings -> per-level counts in vocab order, zero-padded.

    Drift-guard: an aggregator refactor that drops zero-padding (or
    changes the vocab order) would break the exit-code gate at
    ``cmd_critique.py:1351`` (``severity_breakdown.get("high", 0) > 0``)
    AND the per-diff verdict string-builder in ``_run_batch``.
    """
    from roam.critique.aggregator import aggregate
    from roam.critique.checks import Finding

    # Empty input — zero-padded 4-tier dict
    empty = aggregate([])
    assert empty["severity_breakdown"] == {
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
    }, (
        "W761-critique: aggregator zero-padded 4-tier contract drifted on "
        "empty findings — exit-code gate at cmd_critique.py:1351 would crash "
        "on missing ``high`` key without the zero-padding."
    )

    # Non-empty input — per-level counts, zero-padded, vocab order
    non_empty = aggregate(
        [
            Finding("c1", "high", "h1", "...", {}),
            Finding("c1", "high", "h2", "...", {}),
            Finding("c1", "medium", "m1", "...", {}),
        ]
    )
    assert non_empty["severity_breakdown"] == {
        "high": 2,
        "medium": 1,
        "low": 0,
        "info": 0,
    }


# ---------------------------------------------------------------------------
# 5. Every Finding emit value lies in the canonical 9-key vocab
# ---------------------------------------------------------------------------


def test_critique_finding_emit_values_have_finite_canonical_rank() -> None:
    """Every emit token must rank >= 0 under the canonical severity_rank().

    Static scan of :mod:`roam.critique.checks`: every literal severity
    token resolves to a finite rank. Drift-guard against a future emit
    site that introduces a typo (``"hihgh"``) — ``severity_rank()``
    would collapse it to -1, the W531 CI-safety floor, and silently
    drop it below every defined ``info`` finding.
    """
    text = CRITIQUE_CHECKS.read_text(encoding="utf-8")
    # Capture every string literal that PLAUSIBLY looks like a severity
    # emit value: ``severity="..."`` keyword or ``severity = "..."``
    # assignment. Use a simple regex over the source text — the AST
    # walker above already proved the structural emit sites are
    # in-vocab; this regex is a belt-and-braces second pass that catches
    # patterns the walker missed (e.g. an f-string inside a stale
    # construction site).
    pattern = re.compile(
        r"""severity\s*=\s*["']([^"']+)["']""",
        re.MULTILINE,
    )
    tokens = set(pattern.findall(text))

    # Filter: only consider tokens that look severity-shaped (in the
    # canonical 9-key vocab — case-insensitive). Other string
    # assignments to a local ``severity`` variable in checks.py would
    # be a structural smell of their own, but outside this test's
    # scope.
    for tok in tokens:
        lower = tok.strip().lower()
        if lower not in _CANONICAL_SEVERITY_VOCAB:
            continue
        assert severity_rank(lower) >= 0, (
            f"W761-critique: critique.checks emits severity {tok!r} which "
            f"severity_rank() collapses to -1 — typo or unrecognised label"
        )


# ---------------------------------------------------------------------------
# 6. _CRITIQUE_SEVERITY_TO_RISK_LEVEL covers the full canonical vocab
# ---------------------------------------------------------------------------


def test_critique_severity_to_risk_level_covers_canonical_vocab() -> None:
    """The 9-key severity->risk-LEVEL projection covers the full canonical vocab.

    ``_CRITIQUE_SEVERITY_TO_RISK_LEVEL`` is the cmd_critique table
    projecting severity onto the canonical W631 risk-LEVEL axis
    (Pattern-3-distinct from severity-rank). Every canonical 9-key
    severity (4 canonical + 5 alias) MUST have a defined projection so
    the per-finding walk inside ``_critique_risk_level`` never falls
    into the unknown-label safe-floor branch on a canonical label.
    The safe-floor is reserved for genuinely unrecognised tokens
    (typos, future spellings) — landing a canonical label there would
    silently disclose a degradation marker (``critique_unknown_severity:high``)
    on every finding.
    """
    from roam.commands.cmd_critique import _CRITIQUE_SEVERITY_TO_RISK_LEVEL

    # Every canonical severity must have a projection
    missing = sorted(_CANONICAL_SEVERITY_VOCAB - frozenset(_CRITIQUE_SEVERITY_TO_RISK_LEVEL))
    assert not missing, (
        f"W761-critique: _CRITIQUE_SEVERITY_TO_RISK_LEVEL is missing "
        f"canonical severity keys {missing}; the per-finding walk would "
        f"safe-floor these labels to ``low`` AND emit a spurious "
        f"``critique_unknown_severity:<label>`` marker on the warnings_out "
        f"channel for an in-vocab label."
    )

    # Projection values must be in the canonical W631 risk-LEVEL set
    # (critical/high/medium/low). Critique never escalates to
    # ``critical`` (per the conservative-on-critical comment in
    # cmd_critique.py) so the projection saturates at ``high``.
    allowed_risk_levels = {"critical", "high", "medium", "low"}
    bad_values = {k: v for k, v in _CRITIQUE_SEVERITY_TO_RISK_LEVEL.items() if v not in allowed_risk_levels}
    assert not bad_values, (
        f"W761-critique: _CRITIQUE_SEVERITY_TO_RISK_LEVEL projects onto "
        f"out-of-vocab risk-LEVEL values {bad_values}; expected subset of "
        f"{sorted(allowed_risk_levels)}"
    )


# ---------------------------------------------------------------------------
# 7. Exit-code 5 gate keys on the zero-padded ``high`` bucket
# ---------------------------------------------------------------------------


def test_critique_exit_code_gate_keys_on_zero_padded_high_bucket() -> None:
    """The exit-code 5 gate expression keys on ``severity_breakdown["high"]``.

    The aggregator zero-pads ``high`` into the 4-tier breakdown so the
    gate expression at ``cmd_critique.py``::

        if result["severity_breakdown"].get("high", 0) > 0:
            ctx.exit(5)

    is well-defined on every result (clean OR partial). Drift-guard: a
    future aggregator refactor that drops the zero-padded ``high`` key
    (e.g. by switching to ``drop_zero=True``) would silently disable
    the CI gate — the ``.get("high", 0)`` would return 0 on a
    no-findings result AND a found-but-low/medium-only result. The
    gate stays correct only because zero-padding distinguishes the
    two: a present ``"high": 0`` key proves zero high-severity
    findings, NOT "no findings at all".
    """
    text = CMD_CRITIQUE.read_text(encoding="utf-8")
    # Pin the gate expression shape — exact match against the literal
    # source line. A refactor that splits this onto two lines or
    # renames the dict key would intentionally trip this guard.
    gate_pattern = re.compile(
        r"""severity_breakdown["'\]\.]+get\(["']high["'],\s*0\)""",
        re.MULTILINE,
    )
    assert gate_pattern.search(text), (
        "W761-critique: exit-code 5 gate expression "
        "``severity_breakdown.get('high', 0)`` not found in cmd_critique — "
        "if you renamed the gate key, update this test AND update the "
        "test_w566_breakdown_reproduces_critique_contract pin."
    )

    # Pin the aggregator's drop_zero=False (the zero-padding contract).
    aggregator_text = CRITIQUE_AGGREGATOR.read_text(encoding="utf-8")
    assert "drop_zero=False" in aggregator_text, (
        "W761-critique: aggregator must pass drop_zero=False to "
        "severity_breakdown so the zero-padded ``high`` bucket is always "
        "present — without it the exit-code 5 gate would silently disable "
        "on a no-high-findings result."
    )


# ---------------------------------------------------------------------------
# 8. cmd_critique exposes no ``--severity`` click.Choice option
# ---------------------------------------------------------------------------


def test_cmd_critique_has_no_severity_choice_option() -> None:
    """cmd_critique has no ``--severity`` CLI floor filter.

    The gate semantics are exit-code 5 on the aggregated ``high``
    count, NOT a CLI floor filter (distinct from cmd_secrets /
    cmd_vulns which expose ``--severity`` Choice options). Drift-
    guard: a future ``--severity`` addition must use the canonical
    9-key vocab + ``all`` sentinel (W1005-followup-C / W761-vulns-
    secrets pattern) so cross-command floor semantics stay aligned —
    extending to a narrower 3-tier or unrelated vocab would re-
    introduce the Pattern 3b parameter-name divergence the canonical
    layer was built to seal.
    """
    from roam.commands.cmd_critique import critique

    sev_opt = None
    for p in critique.params:
        if p.name == "severity":
            sev_opt = p
            break

    if sev_opt is None:
        # Expected state: no --severity option. Pass.
        return

    # If a future revision DOES add --severity, the choices must be
    # the canonical 9-key vocab + ``all`` sentinel.
    choices = set(getattr(sev_opt.type, "choices", ()) or ())
    expected = {
        "all",
        "critical",
        "error",
        "high",
        "warning",
        "medium",
        "low",
        "info",
    }
    assert choices == expected, (
        f"W761-critique: cmd_critique --severity choices drifted from the "
        f"canonical 9-key vocab; expected {sorted(expected)}, got "
        f"{sorted(choices)} — align with W1005-followup-C / W761-vulns-"
        f"secrets canonical Choice vocabulary."
    )

    # Every non-``all`` choice must have a finite canonical rank
    for choice in choices - {"all"}:
        assert severity_rank(choice) >= 0, (
            f"W761-critique: cmd_critique accepts --severity {choice!r} but "
            f"severity_rank({choice!r}) is -1 — the floor filter would "
            f"never trigger"
        )
