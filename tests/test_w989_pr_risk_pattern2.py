"""W989: Pattern 2 (silent fallback) tightening in ``cmd_pr_risk.py``.

Third sibling of the W983 cmd_alerts case-study sweep (W987 hardened
cmd_smells, W988 hardened cmd_conventions, W989 hardens cmd_pr_risk).
W983 flagged cmd_pr_risk as carrying the same pre-W933 silent-fallback
shape as cmd_alerts: an unknown risk-level value silently floored to
``"low"`` (W718's CI-safety default) with no structured signal to the
caller — Pattern 2 in the worst form: the agent reading the envelope
sees a clean ``low`` verdict without any indication the canonical-level
invariant was breached upstream.

The W989 fix mirrors W969's four-anchor pattern from ``cmd_alerts.py``:

- **Anchor 1 (frozenset)**: ``_VALID_RISK_LEVELS`` re-expresses the
  canonical bucket vocabulary ``{"low","moderate","high","critical"}``
  as a closed set. Single source of truth for the validator.
- **Anchor 2 (helper)**: ``_coerce_risk_level`` accepts canonical
  lowercase silently, lowercases pre-W718 UPPER-cased fixtures silently,
  warns + defaults on anything else.
- **Anchor 3 (envelope plumb)**: ``_build_pr_risk_finding_rows`` grew a
  ``warnings_out: list[str] | None`` keyword; the CLI surfaces it on
  the envelope at ``warnings_out`` and flips ``summary.partial_success``
  on any non-empty warning.
- **Anchor 4 (drift guard)**: this test pins
  ``_VALID_RISK_LEVELS == frozenset(_PR_RISK_LEVEL_TO_SEVERITY)`` so
  a one-sided edit to either the bucket set or the severity mapping
  fails at CI time.

Discipline (per CLAUDE.md Pattern 2 + W718): preserve the CI-safety
floor (unknown -> "low") for back-compat, surface the silent-fallback
state as an actionable warning on ``warnings_out``, NEVER raise on
incomplete data (the W531 lesson: a typo'd label MUST NOT promote a
finding into a CI-failing rank).
"""

from __future__ import annotations

from roam.commands.cmd_pr_risk import (
    _PR_RISK_LEVEL_TO_SEVERITY,
    _VALID_RISK_LEVELS,
    _build_pr_risk_finding_rows,
    _coerce_risk_level,
    _normalise_pr_risk_level,
)


# ---------------------------------------------------------------------------
# Fixture: a minimal ``_pr_risk_data`` dict shaped like the one
# ``cmd_pr_risk`` builds before calling ``_build_pr_risk_finding_rows``.
# Only the fields the composite-risk-score row reads are populated;
# conditional sub-kinds (blast / coverage-gap / novelty) stay below
# their emit thresholds so the row list has exactly one row.
# ---------------------------------------------------------------------------


def _minimal_pr_risk_data(level: str) -> dict:
    return {
        "diff_id": "deadbeefdead",
        "label": "unstaged",
        "commit_range": None,
        "staged": False,
        "file_list": ["src/example.py"],
        "risk": 10,
        "level": level,
        "blast_pct": 1.0,  # below 20% emit threshold
        "hotspot_score": 0.0,
        "test_coverage": 1.0,  # full coverage -> no coverage-gap row
        "bus_factor_risk": 0.0,
        "coupling_score": 0.0,
        "novelty": 0.0,
        "familiarity_risk": 0.0,
        "minor_risk": 0.0,
        "reductive_change": False,
        "driver_label": None,
        "total_added": 1,
        "total_removed": 0,
        "resolved_author": None,
        "affected_count": 0,
        "total_syms_repo": 100,
        "changed_syms_count": 1,
        "source_files_count": 1,
        "covered_files": 1,
        "familiarity_details": {"avg_familiarity": 1.0, "files_assessed": 0},
    }


# ---------------------------------------------------------------------------
# Helper-level tests for ``_coerce_risk_level``
# ---------------------------------------------------------------------------


def test_canonical_risk_level_silent() -> None:
    """W989: canonical lowercase ``"low" / "moderate" / "high" / "critical"``
    passes through silently — no warning, value returned untouched.
    """
    for canonical in ("low", "moderate", "high", "critical"):
        warnings: list[str] = []
        result = _coerce_risk_level(
            canonical,
            default="low",
            field_name="level",
            warnings_out=warnings,
        )
        assert result == canonical, (
            f"Canonical {canonical!r} must return untouched, got {result!r}"
        )
        assert warnings == [], (
            f"Canonical {canonical!r} must emit no warnings, got {warnings!r}"
        )


def test_uppercase_risk_level_normalized_silently() -> None:
    """W989: pre-W718 UPPER-cased fixtures (``"CRITICAL"``, ``"HIGH"``,
    ``"MODERATE"``, ``"LOW"``) and mixed-case spellings round-trip to
    canonical lowercase WITHOUT a warning — unambiguous user intent.
    Back-compat with the W718 normalization path that ``cmd_pr_risk``
    has shipped since before the warnings_out plumb.
    """
    for raw, expected in (
        ("CRITICAL", "critical"),
        ("HIGH", "high"),
        ("MODERATE", "moderate"),
        ("LOW", "low"),
        ("Critical", "critical"),
        ("  high  ", "high"),  # also covers whitespace stripping
    ):
        warnings: list[str] = []
        result = _coerce_risk_level(
            raw,
            default="low",
            field_name="level",
            warnings_out=warnings,
        )
        assert result == expected, (
            f"UPPER-cased {raw!r} must coerce to {expected!r}, got {result!r}"
        )
        assert warnings == [], (
            f"UPPER-cased {raw!r} must coerce silently, got {warnings!r}"
        )


def test_unknown_risk_level_warns_and_defaults() -> None:
    """W989: an unknown level (typo, None, wrong type) appends a structured
    warning naming the field + value + valid spellings AND returns the
    *default* (preserving the W718 CI-safety floor). Pattern 2 discipline:
    NEVER raise on backward-incompatible input; surface the silent path.
    """
    for unknown in ("fatal", "blocker", "", None, 42, ["high"]):
        warnings: list[str] = []
        result = _coerce_risk_level(
            unknown,
            default="low",
            field_name="risk_level",
            warnings_out=warnings,
        )
        assert result == "low", (
            f"Unknown {unknown!r} must default to 'low', got {result!r}"
        )
        assert len(warnings) == 1, (
            f"Unknown {unknown!r} must emit exactly one warning, got {warnings!r}"
        )
        warning = warnings[0]
        # LAW 4 + LAW 2: warning must name the field, name the offending
        # value (string repr), and point at the valid alternatives.
        assert "risk_level" in warning, (
            f"Warning must name the field, got: {warning!r}"
        )
        assert "low" in warning and "high" in warning, (
            f"Warning must list the valid spellings, got: {warning!r}"
        )
        assert "defaulting" in warning, (
            f"Warning must name the resolution, got: {warning!r}"
        )


def test_unknown_risk_level_no_warning_when_accumulator_is_none() -> None:
    """W989 back-compat: when ``warnings_out=None`` (e.g. the pre-W989
    :func:`_normalise_pr_risk_level` wrapper), unknown input still floors
    to the default silently — preserves the byte-identical behaviour
    persisted finding row hashes depend on.
    """
    result = _coerce_risk_level(
        "fatal",
        default="low",
        field_name="level",
        warnings_out=None,
    )
    assert result == "low"


# ---------------------------------------------------------------------------
# Back-compat wrapper: ``_normalise_pr_risk_level``
# ---------------------------------------------------------------------------


def test_normalise_pr_risk_level_back_compat() -> None:
    """W989: :func:`_normalise_pr_risk_level` (the pre-W989 wrapper) keeps
    its (level) -> str signature. Every pre-W989 call site (23 in
    :func:`_build_pr_risk_finding_rows` and elsewhere) keeps working
    without modification — and the W718 CI-safety floor for unknown
    input is preserved byte-for-byte.
    """
    assert _normalise_pr_risk_level("critical") == "critical"
    assert _normalise_pr_risk_level("CRITICAL") == "critical"
    assert _normalise_pr_risk_level("fatal") == "low"  # W718 floor
    assert _normalise_pr_risk_level(None) == "low"
    assert _normalise_pr_risk_level("") == "low"


# ---------------------------------------------------------------------------
# Row builder: warnings_out plumb-through
# ---------------------------------------------------------------------------


def test_build_finding_rows_unknown_level_surfaces_warning() -> None:
    """W989: when ``_build_pr_risk_finding_rows`` is called with
    ``warnings_out=[]`` and the data dict carries an unknown ``level``,
    the warning accumulates AND the composite row's severity floors to
    ``"info"`` (via the lookup miss after _coerce_risk_level returns
    the safe ``"low"`` default which IS in the severity map -> "low").
    Persisted ``evidence_json.risk_level`` carries the raw input so a
    consumer can see the upstream-broken value verbatim.
    """
    data = _minimal_pr_risk_data(level="fatal")  # unknown
    warnings: list[str] = []
    rows = _build_pr_risk_finding_rows(
        data,
        source_version="1.0.0",
        warnings_out=warnings,
    )

    assert len(rows) == 1, "minimal fixture should emit exactly the composite row"
    assert warnings, "unknown level must surface at least one warning"
    assert any("level" in w for w in warnings)
    assert any("fatal" in w for w in warnings)

    composite = rows[0]
    assert composite["kind"] == "pr-risk:composite-risk-score"
    # severity floors to the value at _PR_RISK_LEVEL_TO_SEVERITY["low"]
    # because _coerce_risk_level returned "low" as the W718 CI-safety floor.
    assert composite["severity"] == "low", (
        f"unknown level must floor to W718 CI-safe severity, got {composite['severity']!r}"
    )
    # Hash stability: evidence_json carries the raw input level untouched,
    # so a downstream consumer can see what the upstream broke.
    assert composite["evidence"]["risk_level"] == "fatal"


def test_build_finding_rows_canonical_level_no_warning() -> None:
    """W989: canonical lowercase level produces zero warnings — happy path."""
    for canonical in ("low", "moderate", "high", "critical"):
        data = _minimal_pr_risk_data(level=canonical)
        warnings: list[str] = []
        rows = _build_pr_risk_finding_rows(
            data,
            source_version="1.0.0",
            warnings_out=warnings,
        )
        assert warnings == [], (
            f"Canonical {canonical!r} must emit no warnings, got {warnings!r}"
        )
        # Severity comes from the canonical mapping (moderate -> medium).
        expected_severity = _PR_RISK_LEVEL_TO_SEVERITY[canonical]
        assert rows[0]["severity"] == expected_severity


def test_build_finding_rows_hash_stability_without_warnings_out() -> None:
    """W989: when ``warnings_out`` is NOT passed (the persist path uses
    this — :func:`_emit_pr_risk_findings` doesn't supply it), the row
    dicts are byte-identical to the canonical path. Persisted finding
    row hashes depend on ``evidence_json``; this guards that
    ``_build_pr_risk_finding_rows`` does NOT mutate ``evidence_json``
    based on the presence of the accumulator.
    """
    data = _minimal_pr_risk_data(level="moderate")
    rows_with = _build_pr_risk_finding_rows(
        data, source_version="1.0.0", warnings_out=[]
    )
    rows_without = _build_pr_risk_finding_rows(data, source_version="1.0.0")
    assert rows_with[0]["evidence"] == rows_without[0]["evidence"]
    assert rows_with[0]["finding_id_str"] == rows_without[0]["finding_id_str"]


# ---------------------------------------------------------------------------
# Drift guard: frozenset <-> severity-map keys
# ---------------------------------------------------------------------------


def test_drift_guard_valid_risk_levels_match_severity_map_keys() -> None:
    """W989 drift guard (Anchor 4): ``_VALID_RISK_LEVELS`` and
    ``_PR_RISK_LEVEL_TO_SEVERITY`` are TWO closed sets that must stay in
    sync — a one-sided edit (adding a new bucket to the map without
    updating the frozenset, or vice versa) breaks the validator at a
    distance.

    The pr-risk module has NO ``Literal``-annotated TypedDict on the
    level field (unlike cmd_alerts.py W974 — the level here is computed
    by bucketing rather than user-supplied), so the drift guard pins
    the frozenset against the severity-map keys instead. Mirrors W968's
    discipline on ``_VALID_OPS`` <-> ``AlertThreshold.op``.

    Adding a new bucket means updating BOTH:
    - :data:`_VALID_RISK_LEVELS` (Anchor 1)
    - :data:`_PR_RISK_LEVEL_TO_SEVERITY` (severity mapping)
    - the bucketing logic in :func:`pr_risk` (the risk-score thresholds)
    """
    assert _VALID_RISK_LEVELS == frozenset(_PR_RISK_LEVEL_TO_SEVERITY), (
        f"_VALID_RISK_LEVELS={_VALID_RISK_LEVELS!r} drifted from "
        f"_PR_RISK_LEVEL_TO_SEVERITY keys={set(_PR_RISK_LEVEL_TO_SEVERITY)!r}. "
        f"Update BOTH the frozenset and the severity mapping when adding "
        f"a new pr-risk bucket."
    )
