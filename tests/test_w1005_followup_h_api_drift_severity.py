"""W1005-followup-H -- cmd_api_drift confidence widened with canonical alias map.

Pattern 3a (cross-command metric divergence). Pre-W1005-followup-H,
``roam api-drift --confidence`` accepted ONLY the 3-tier emit vocab
``{high, medium, low, all}`` (where ``all`` was the bypass sentinel
that skipped filtering entirely). An agent fluent in the W547 canonical
vocabulary (``critical / error / high / warning / medium / low / info /
note``) who typed ``--confidence critical`` (because that's what
``roam smells``, ``roam alerts``, ``roam api-changes``,
``roam dogfood-aggregate``, ``roam pr-bundle add risk`` accept post-W1005
/ -C / -D / -F / -G) hit a click usage error 2.

Path A-variant fix (mirroring W1005-followup-F/G). Widen Click.Choice
to accept the union of the emit vocab + W547 canonical tokens + the
``all`` bypass sentinel. Project canonical tokens onto the emit vocab
via :data:`_CANONICAL_TO_CONFIDENCE` BEFORE the equality filter
(equality, not floor -- api-drift uses ``f["confidence"] == confidence``
because the detector emits exactly three confidence classes). EMIT
vocab stays high/medium/low so downstream consumers (the ``_CONF_LABEL``
formatter, JSON summary buckets ``n_high``/``n_medium``/``n_low``) are
unchanged. The ``all`` sentinel still short-circuits the filter
entirely.

Projection (mirrors :func:`roam.output._severity.severity_to_confidence_level`
-- the W565 closed table; api-drift adopts the same shape):
    critical / error / high -> high
    warning / medium        -> medium
    info / low / note       -> low
    all                     -> (bypass)

What this test pins
-------------------

1. Canonical 3-tier still accepted (back-compat): high / medium / low.
2. W547 canonical 7-tier accepted post-widening: critical / error /
   warning / info / note.
3. ``all`` sentinel bypasses the filter (returns every finding).
4. Case-insensitive: HIGH, high, High all parse.
5. Unknown token rejected with click usage error 2.
6. Projection alignment with :func:`severity_rank` -- canonical tokens
   that rank-higher-than-low keep at least the high-confidence findings.
"""

from __future__ import annotations

import pytest

from tests._helpers.repo_root import repo_root
from tests.conftest import (
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# Resolve the canonical repo root so the test file lives correctly even
# when dispatched through a nested worktree (W572 lesson).
REPO_ROOT = repo_root()


# ===========================================================================
# Fixture -- Laravel + TS project with deliberate drift on multiple tiers.
#
# The fixture deliberately produces at least one finding of every
# confidence tier (high / medium / low) so the equality filter is
# observable. Mirrors tests/test_api_drift.py::drift_project but
# extended with a fuzzy-match (low confidence) and a backend-only
# field (medium confidence).
# ===========================================================================


@pytest.fixture
def multi_tier_drift_project(tmp_path):
    """Project with at least one finding per confidence tier."""
    proj = tmp_path / "multi_drift"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # PHP model: 'name' (matches), 'phone' (medium = backend-only),
    # 'addressLine1' (low = fuzzy match to TS 'address').
    models = proj / "app" / "Models"
    models.mkdir(parents=True)
    (models / "User.php").write_text(
        "<?php\nnamespace App\\Models;\n\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n\n"
        "class User extends Model {\n"
        "    protected $fillable = ['name', 'email', 'phone', 'address_line_1'];\n"
        "    protected $hidden = ['password'];\n"
        "}\n"
    )

    # TypeScript interface:
    #  - 'avatar' (high = frontend-only, missing in backend)
    #  - 'address' (low = fuzzy match to address_line_1)
    types = proj / "frontend" / "types"
    types.mkdir(parents=True)
    (types / "user.ts").write_text(
        "export interface User {\n"
        "  id: number;\n"
        "  name: string;\n"
        "  email: string;\n"
        "  avatar: string;\n"
        "  address: string;\n"
        "}\n"
    )

    git_init(proj)
    index_in_process(proj)
    return proj


# ===========================================================================
# 1. Canonical 3-tier still accepted (back-compat unchanged)
# ===========================================================================


class TestCanonical3TierAccepted:
    """Back-compat: high/medium/low parse cleanly (pre-fix behaviour unchanged)."""

    @pytest.mark.parametrize("token", ["high", "medium", "low"])
    def test_3tier_token_parses_cleanly(self, cli_runner, multi_tier_drift_project, monkeypatch, token):
        """``--confidence high|medium|low`` parses cleanly (back-compat)."""
        monkeypatch.chdir(multi_tier_drift_project)
        result = invoke_cli(
            cli_runner,
            ["api-drift", "--confidence", token],
            cwd=multi_tier_drift_project,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"api-drift --confidence {token}: expected exit 0 "
            f"(back-compat unchanged), got exit {result.exit_code}; "
            f"output: {result.output}"
        )
        assert REPO_ROOT.exists()  # drift guard: helper resolves correctly


# ===========================================================================
# 2. W547 canonical 7-token vocab accepted post-widening (Pattern 3a fix)
# ===========================================================================


class TestCanonicalFull7Accepted:
    """Pattern 3a fix: W547 canonical tokens parse without usage error."""

    @pytest.mark.parametrize("token", ["critical", "error", "warning", "info", "note"])
    def test_canonical_token_parses_cleanly(self, cli_runner, multi_tier_drift_project, monkeypatch, token):
        """``--confidence <canonical>`` parses cleanly (was usage error 2 pre-fix)."""
        monkeypatch.chdir(multi_tier_drift_project)
        result = invoke_cli(
            cli_runner,
            ["api-drift", "--confidence", token],
            cwd=multi_tier_drift_project,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"api-drift --confidence {token}: expected exit 0 "
            f"(canonical token parses via W547 alias), got exit "
            f"{result.exit_code}; output: {result.output}"
        )

    def test_critical_projects_to_high(self, cli_runner, multi_tier_drift_project, monkeypatch):
        """``--confidence critical`` projects to ``high`` -- keeps only high findings.

        The fixture has at least one ``missing_in_backend`` (high)
        finding: the TS interface declares ``avatar`` but the PHP model
        doesn't. Projection: ``critical`` -> ``high`` via
        :data:`_CANONICAL_TO_CONFIDENCE`. Filter (equality):
        ``f["confidence"] == "high"`` keeps only high findings.
        """
        monkeypatch.chdir(multi_tier_drift_project)
        result = invoke_cli(
            cli_runner,
            ["api-drift", "--confidence", "critical"],
            cwd=multi_tier_drift_project,
            json_mode=True,
        )
        data = parse_json_output(result, "api-drift")
        # Every surfaced finding must be confidence == "high".
        confidences = {f["confidence"] for match in data.get("matches", []) for f in match.get("findings", [])}
        assert confidences <= {"high"}, (
            f"--confidence critical (projects to high) surfaced findings "
            f"at non-high tiers: {confidences}. Projection map drifted."
        )

    def test_warning_projects_to_medium(self, cli_runner, multi_tier_drift_project, monkeypatch):
        """``--confidence warning`` projects to ``medium`` -- keeps medium findings."""
        monkeypatch.chdir(multi_tier_drift_project)
        result = invoke_cli(
            cli_runner,
            ["api-drift", "--confidence", "warning"],
            cwd=multi_tier_drift_project,
            json_mode=True,
        )
        data = parse_json_output(result, "api-drift")
        confidences = {f["confidence"] for match in data.get("matches", []) for f in match.get("findings", [])}
        assert confidences <= {"medium"}, (
            f"--confidence warning (projects to medium) surfaced findings at non-medium tiers: {confidences}."
        )


# ===========================================================================
# 3. ``all`` sentinel bypasses filter entirely
# ===========================================================================


class TestAllSentinelBypasses:
    """``--confidence all`` returns every finding regardless of severity."""

    def test_all_sentinel_returns_all_findings(self, cli_runner, multi_tier_drift_project, monkeypatch):
        """``--confidence all`` keeps every finding.

        Bypass semantic (line 675-676 in cmd_api_drift.py): when
        ``confidence == "all"`` the filter loop is skipped entirely,
        preserving every finding regardless of its emit-vocab tier.
        """
        monkeypatch.chdir(multi_tier_drift_project)
        # First, get the baseline count with explicit --confidence all.
        result_all = invoke_cli(
            cli_runner,
            ["api-drift", "--confidence", "all"],
            cwd=multi_tier_drift_project,
            json_mode=True,
        )
        data_all = parse_json_output(result_all, "api-drift")
        total_all = data_all["summary"]["findings"]

        # Then, get the count with --confidence high (subset).
        result_high = invoke_cli(
            cli_runner,
            ["api-drift", "--confidence", "high"],
            cwd=multi_tier_drift_project,
            json_mode=True,
        )
        data_high = parse_json_output(result_high, "api-drift")
        total_high = data_high["summary"]["findings"]

        # ``all`` must surface AT LEAST as many findings as ``high``.
        # Strict-greater would require the fixture to produce >=2 tiers;
        # we already engineer that, but greater-equal is the safer pin.
        assert total_all >= total_high, (
            f"--confidence all ({total_all}) surfaced FEWER findings than "
            f"--confidence high ({total_high}) -- the bypass sentinel "
            f"is leaking through the filter."
        )

    def test_all_is_the_default(self, cli_runner, multi_tier_drift_project, monkeypatch):
        """Omitting ``--confidence`` matches ``--confidence all`` (default unchanged)."""
        monkeypatch.chdir(multi_tier_drift_project)
        result_default = invoke_cli(
            cli_runner,
            ["api-drift"],
            cwd=multi_tier_drift_project,
            json_mode=True,
        )
        data_default = parse_json_output(result_default, "api-drift")

        result_all = invoke_cli(
            cli_runner,
            ["api-drift", "--confidence", "all"],
            cwd=multi_tier_drift_project,
            json_mode=True,
        )
        data_all = parse_json_output(result_all, "api-drift")

        assert data_default["summary"]["findings"] == data_all["summary"]["findings"], (
            "default (no --confidence) should be equivalent to --confidence all -- the bypass sentinel is the default."
        )


# ===========================================================================
# 4. Case insensitivity (Click.Choice case_sensitive=False)
# ===========================================================================


class TestCaseInsensitive:
    """case_sensitive=False on the Click.Choice -- HIGH / high / High all parse."""

    @pytest.mark.parametrize("token", ["HIGH", "High", "high", "CRITICAL", "critical"])
    def test_case_insensitive_parse(self, cli_runner, multi_tier_drift_project, monkeypatch, token):
        """All-caps / mixed-case tokens parse identically to lowercase."""
        monkeypatch.chdir(multi_tier_drift_project)
        result = invoke_cli(
            cli_runner,
            ["api-drift", "--confidence", token],
            cwd=multi_tier_drift_project,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"api-drift --confidence {token}: expected exit 0 "
            f"(case-insensitive parse), got exit {result.exit_code}; "
            f"output: {result.output}"
        )


# ===========================================================================
# 5. Unknown token rejected with click usage error 2
# ===========================================================================


class TestUnknownTokenRejected:
    """Closed-enum Click.Choice -- unknown tokens trip click usage error 2."""

    @pytest.mark.parametrize("bogus", ["bogus", "blocker", "extreme", "0", "very-high"])
    def test_unknown_token_exits_2(self, cli_runner, multi_tier_drift_project, monkeypatch, bogus):
        """``--confidence <bogus>`` exits with click usage error 2."""
        monkeypatch.chdir(multi_tier_drift_project)
        result = invoke_cli(
            cli_runner,
            ["api-drift", "--confidence", bogus],
            cwd=multi_tier_drift_project,
            json_mode=True,
        )
        assert result.exit_code == 2, (
            f"api-drift --confidence {bogus}: expected exit 2 "
            f"(closed-enum click usage error), got exit "
            f"{result.exit_code}; output: {result.output}"
        )


# ===========================================================================
# 6. Severity-rank alignment -- canonical-vs-emit projection is rank-coherent
# ===========================================================================


class TestSeverityRankAlignment:
    """The projection map respects :func:`severity_rank` ordering."""

    def test_projection_matches_w565_confidence_level_axis(self):
        """The projection map mirrors the W565 confidence-LEVEL axis verbatim.

        Note on axes: this projection sits on the *confidence-LEVEL*
        axis (high/medium/low), NOT the W547 *severity* axis
        (critical/error/warning/info -- where info < low by rank).
        That's why a naive ``severity_rank(canonical) >= severity_rank
        (projected)`` polarity check would FAIL on the
        ``info -> low`` entry (severity_rank("info")=0 < severity_rank
        ("low")=1). The api-drift detector emits high/medium/low at the
        confidence-LEVEL axis, so the projection map adopts that axis;
        rank-direction is irrelevant when the axes differ.

        The pinned mapping is the W565 table verbatim
        (:data:`roam.output._severity._DEFAULT_SEVERITY_TO_CONFIDENCE_LEVEL`)
        restricted to the keys api-drift exposes via Click.Choice.
        """
        from roam.commands.cmd_api_drift import _CANONICAL_TO_CONFIDENCE

        # The W565 confidence-LEVEL projection (restricted to api-drift
        # vocabulary). If this drifts the cross-command alignment with
        # cmd_complexity / cmd_smells breaks.
        _EXPECTED = {
            "critical": "high",
            "error": "high",
            "high": "high",
            "warning": "medium",
            "medium": "medium",
            "info": "low",
            "low": "low",
            "note": "low",
        }
        assert _CANONICAL_TO_CONFIDENCE == _EXPECTED, (
            f"_CANONICAL_TO_CONFIDENCE drifted from the W565 "
            f"confidence-LEVEL axis. Expected: {_EXPECTED}. "
            f"Got: {dict(_CANONICAL_TO_CONFIDENCE)}."
        )

    def test_projection_map_keys_in_click_choice(self):
        """Every key of _CANONICAL_TO_CONFIDENCE lives in the Click.Choice.

        Drift guard: if a contributor adds a canonical token to the
        alias map but forgets the Click.Choice widening,
        ``--confidence <new>`` would trip click usage error 2 even
        though the projection map is ready.
        """
        from roam.commands.cmd_api_drift import (
            _CANONICAL_TO_CONFIDENCE,
            api_drift_cmd,
        )

        confidence_opt = next(p for p in api_drift_cmd.params if p.name == "confidence")
        choice_values = {c.lower() for c in confidence_opt.type.choices}
        for canonical_token in _CANONICAL_TO_CONFIDENCE:
            assert canonical_token in choice_values, (
                f"_CANONICAL_TO_CONFIDENCE includes {canonical_token!r} "
                f"but the --confidence Click.Choice does not -- widening "
                f"drifted out of sync with the alias map. Choice: "
                f"{sorted(choice_values)}."
            )

    def test_projection_map_values_in_emit_vocab(self):
        """Every value in _CANONICAL_TO_CONFIDENCE is a valid emit-vocab slot.

        Polarity guard: a future contributor extending the canonical
        vocab must also map the new token onto one of the three
        emit-vocab slots (high/medium/low), NOT introduce a fourth
        slot (which would silently leak canonical vocabulary into
        the EMIT side and break the by-confidence buckets in the JSON
        envelope).
        """
        from roam.commands.cmd_api_drift import _CANONICAL_TO_CONFIDENCE

        _VALID_EMIT_VOCAB = {"high", "medium", "low"}
        for canonical, emit in _CANONICAL_TO_CONFIDENCE.items():
            assert emit in _VALID_EMIT_VOCAB, (
                f"_CANONICAL_TO_CONFIDENCE[{canonical!r}] -> {emit!r} "
                f"is NOT a valid emit-vocab slot "
                f"({sorted(_VALID_EMIT_VOCAB)}). The EMIT side is "
                f"closed; widen INPUT only."
            )

    def test_bypass_sentinel_constant_is_all(self):
        """The bypass sentinel constant is exactly the string ``"all"``.

        Pin the literal so a future maintainer who renames the constant
        also has to acknowledge the help-text + downstream-doc impact.
        """
        from roam.commands.cmd_api_drift import _CONFIDENCE_BYPASS_SENTINEL

        assert _CONFIDENCE_BYPASS_SENTINEL == "all", (
            f"_CONFIDENCE_BYPASS_SENTINEL = {_CONFIDENCE_BYPASS_SENTINEL!r}; "
            f"expected 'all'. Renaming this constant requires updating "
            f"the --confidence help text and any user-facing docs that "
            f"reference 'all' as the bypass token."
        )
