"""W1005-followup-J -- cmd_laws --min-confidence widened with canonical alias map.

Pattern 3a (cross-command metric divergence). Pre-W1005-followup-J,
``roam laws mine --min-confidence`` accepted ONLY the 3-tier
``{low, medium, high}`` emit vocab. An agent fluent in the W547 canonical
vocabulary (``critical / error / high / warning / medium / low / info /
note``) -- the vocabulary that every sibling --confidence / --severity site
accepts post-W1005-followup-{B, C, D, F, G, H} -- who typed
``--min-confidence critical`` hit a click usage error 2.

Path A-variant fix (mirroring W1005-followup-H on cmd_api_drift). Widen
Click.Choice to accept the union of the emit vocab + W547 canonical tokens.
Project canonical tokens onto the emit vocab via
:data:`_CANONICAL_TO_CONFIDENCE` BEFORE the existing
``confidence_level_rank()`` floor (W596). EMIT vocab stays low/medium/high
so the W596 strict-floor clamp at line 255 of cmd_laws.py is preserved.

Projection (mirrors :data:`roam.output._severity._DEFAULT_SEVERITY_TO_CONFIDENCE_LEVEL`
-- the W565 closed table; same shape cmd_api_drift adopts at W1005-followup-H):
    critical / error / high -> high
    warning / medium        -> medium
    info / low / note       -> low

What this test pins
-------------------

1. Canonical 3-tier still accepted (back-compat): low / medium / high.
2. W547 canonical 7-tier accepted post-widening: critical / error /
   warning / info / note.
3. Case-insensitive: HIGH, high, High all parse.
4. Unknown token rejected with click usage error 2.
5. Projection alignment with :data:`severity_to_confidence_level`
   (W565 closed table verbatim).
6. Projection map keys all live in the Click.Choice (drift guard).
7. Projection map values all live in the emit vocab (drift guard).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# Resolve the canonical repo root so the test file lives correctly even
# when dispatched through a nested worktree (W572 lesson).
REPO_ROOT = repo_root()


# ===========================================================================
# Fixture -- snake_case-dominant Python project that mines a high-confidence
# naming law (>=90% conformance threshold from miner._HIGH_CONFIDENCE_PCT).
# ===========================================================================


@pytest.fixture
def snake_project(tmp_path, monkeypatch):
    """9 snake_case + 1 camelCase function -> high-confidence naming law."""
    proj = tmp_path / "snakeproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    body = textwrap.dedent(
        """\
        def fetch_user(id): return id
        def update_user(id): return id
        def delete_user(id): return id
        def list_users(): return []
        def make_token(): return "t"
        def parse_email(raw): return raw
        def format_name(first, last): return first
        def validate_input(x): return x
        def serialize_payload(p): return p
        def myCamelOdd(x): return x
        """
    )
    (proj / "app.py").write_text(body)
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    return proj


# ===========================================================================
# 1. Canonical 3-tier still accepted (back-compat unchanged)
# ===========================================================================


class TestCanonical3TierAccepted:
    """Back-compat: low/medium/high parse cleanly (pre-fix behaviour unchanged)."""

    @pytest.mark.parametrize("token", ["low", "medium", "high"])
    def test_3tier_token_parses_cleanly(self, cli_runner, snake_project, monkeypatch, token):
        """``--min-confidence low|medium|high`` parses cleanly (back-compat)."""
        monkeypatch.chdir(snake_project)
        result = invoke_cli(
            cli_runner,
            ["laws", "mine", "--min-confidence", token],
            cwd=snake_project,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"laws mine --min-confidence {token}: expected exit 0 "
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
    def test_canonical_token_parses_cleanly(self, cli_runner, snake_project, monkeypatch, token):
        """``--min-confidence <canonical>`` parses cleanly (was usage error 2 pre-fix)."""
        monkeypatch.chdir(snake_project)
        result = invoke_cli(
            cli_runner,
            ["laws", "mine", "--min-confidence", token],
            cwd=snake_project,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"laws mine --min-confidence {token}: expected exit 0 "
            f"(canonical token parses via W547 alias), got exit "
            f"{result.exit_code}; output: {result.output}"
        )

    def test_critical_projects_to_high(self, cli_runner, snake_project, monkeypatch):
        """``--min-confidence critical`` projects to ``high`` -- equivalent to ``--min-confidence high``.

        Both filters must produce the same set of laws because the
        projection map sends ``critical -> high`` (matching the W565
        closed table). Pre-fix this hit a click usage error 2.
        """
        monkeypatch.chdir(snake_project)
        result_critical = invoke_cli(
            cli_runner,
            ["laws", "mine", "--min-confidence", "critical"],
            cwd=snake_project,
            json_mode=True,
        )
        result_high = invoke_cli(
            cli_runner,
            ["laws", "mine", "--min-confidence", "high"],
            cwd=snake_project,
            json_mode=True,
        )

        data_critical = parse_json_output(result_critical, "laws mine")
        data_high = parse_json_output(result_high, "laws mine")

        laws_critical = data_critical.get("laws", [])
        laws_high = data_high.get("laws", [])

        # Project critical -> high; both filters must keep the same set.
        ids_critical = sorted(law.get("id", "") for law in laws_critical)
        ids_high = sorted(law.get("id", "") for law in laws_high)
        assert ids_critical == ids_high, (
            f"--min-confidence critical kept {ids_critical}; "
            f"--min-confidence high kept {ids_high}. Projection "
            f"critical -> high drifted -- they must produce identical sets."
        )

    def test_warning_projects_to_medium(self, cli_runner, snake_project, monkeypatch):
        """``--min-confidence warning`` projects to ``medium`` -- same set as ``--min-confidence medium``."""
        monkeypatch.chdir(snake_project)
        result_warning = invoke_cli(
            cli_runner,
            ["laws", "mine", "--min-confidence", "warning"],
            cwd=snake_project,
            json_mode=True,
        )
        result_medium = invoke_cli(
            cli_runner,
            ["laws", "mine", "--min-confidence", "medium"],
            cwd=snake_project,
            json_mode=True,
        )
        data_warning = parse_json_output(result_warning, "laws mine")
        data_medium = parse_json_output(result_medium, "laws mine")

        ids_warning = sorted(law.get("id", "") for law in data_warning.get("laws", []))
        ids_medium = sorted(law.get("id", "") for law in data_medium.get("laws", []))
        assert ids_warning == ids_medium, (
            f"--min-confidence warning kept {ids_warning}; "
            f"--min-confidence medium kept {ids_medium}. Projection "
            f"warning -> medium drifted."
        )

    def test_info_projects_to_low(self, cli_runner, snake_project, monkeypatch):
        """``--min-confidence info`` projects to ``low`` -- same set as ``--min-confidence low``."""
        monkeypatch.chdir(snake_project)
        result_info = invoke_cli(
            cli_runner,
            ["laws", "mine", "--min-confidence", "info"],
            cwd=snake_project,
            json_mode=True,
        )
        result_low = invoke_cli(
            cli_runner,
            ["laws", "mine", "--min-confidence", "low"],
            cwd=snake_project,
            json_mode=True,
        )
        data_info = parse_json_output(result_info, "laws mine")
        data_low = parse_json_output(result_low, "laws mine")

        ids_info = sorted(law.get("id", "") for law in data_info.get("laws", []))
        ids_low = sorted(law.get("id", "") for law in data_low.get("laws", []))
        assert ids_info == ids_low, (
            f"--min-confidence info kept {ids_info}; "
            f"--min-confidence low kept {ids_low}. Projection "
            f"info -> low drifted."
        )


# ===========================================================================
# 3. Case insensitivity (Click.Choice case_sensitive=False)
# ===========================================================================


class TestCaseInsensitive:
    """case_sensitive=False on the Click.Choice -- HIGH / high / High all parse."""

    @pytest.mark.parametrize("token", ["HIGH", "High", "high", "CRITICAL", "critical"])
    def test_case_insensitive_parse(self, cli_runner, snake_project, monkeypatch, token):
        """All-caps / mixed-case tokens parse identically to lowercase."""
        monkeypatch.chdir(snake_project)
        result = invoke_cli(
            cli_runner,
            ["laws", "mine", "--min-confidence", token],
            cwd=snake_project,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"laws mine --min-confidence {token}: expected exit 0 "
            f"(case-insensitive parse), got exit {result.exit_code}; "
            f"output: {result.output}"
        )


# ===========================================================================
# 4. Unknown token rejected with click usage error 2
# ===========================================================================


class TestUnknownTokenRejected:
    """Closed-enum Click.Choice -- unknown tokens trip click usage error 2."""

    @pytest.mark.parametrize("bogus", ["bogus", "blocker", "extreme", "0", "very-high"])
    def test_unknown_token_exits_2(self, cli_runner, snake_project, monkeypatch, bogus):
        """``--min-confidence <bogus>`` exits with click usage error 2."""
        monkeypatch.chdir(snake_project)
        result = invoke_cli(
            cli_runner,
            ["laws", "mine", "--min-confidence", bogus],
            cwd=snake_project,
            json_mode=True,
        )
        assert result.exit_code == 2, (
            f"laws mine --min-confidence {bogus}: expected exit 2 "
            f"(closed-enum click usage error), got exit "
            f"{result.exit_code}; output: {result.output}"
        )


# ===========================================================================
# 5. Projection map alignment with W565 confidence-LEVEL axis
# ===========================================================================


class TestProjectionMapDriftGuards:
    """Drift guards: projection map keys/values stay in sync with Choice + emit vocab."""

    def test_projection_matches_w565_confidence_level_axis(self):
        """The projection map mirrors the W565 confidence-LEVEL axis verbatim.

        Note on axes: this projection sits on the *confidence-LEVEL*
        axis (low/medium/high), NOT the W547 *severity* axis
        (critical/error/warning/info -- where info < low by rank).
        The laws detector emits low/medium/high at the
        confidence-LEVEL axis (via ``_confidence_from_pct()``), so
        the projection map adopts that axis. Same shape as
        cmd_api_drift._CANONICAL_TO_CONFIDENCE (W1005-followup-H).
        """
        from roam.commands.cmd_laws import _CANONICAL_TO_CONFIDENCE

        # The W565 confidence-LEVEL projection (restricted to the keys
        # laws exposes via Click.Choice). If this drifts the
        # cross-command alignment with cmd_api_drift breaks.
        _EXPECTED = {
            "critical": "high",
            "error": "high",
            "warning": "medium",
            "info": "low",
            "high": "high",
            "medium": "medium",
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
        ``--min-confidence <new>`` would trip click usage error 2 even
        though the projection map is ready.
        """
        from roam.commands.cmd_laws import _CANONICAL_TO_CONFIDENCE, laws_mine

        min_conf_opt = next(p for p in laws_mine.params if p.name == "min_confidence")
        choice_values = {c.lower() for c in min_conf_opt.type.choices}
        for canonical_token in _CANONICAL_TO_CONFIDENCE:
            assert canonical_token in choice_values, (
                f"_CANONICAL_TO_CONFIDENCE includes {canonical_token!r} "
                f"but the --min-confidence Click.Choice does not -- widening "
                f"drifted out of sync with the alias map. Choice: "
                f"{sorted(choice_values)}."
            )

    def test_projection_map_values_in_emit_vocab(self):
        """Every value in _CANONICAL_TO_CONFIDENCE is a valid emit-vocab slot.

        Polarity guard: a future contributor extending the canonical
        vocab must also map the new token onto one of the three
        emit-vocab slots (low/medium/high), NOT introduce a fourth
        slot (which would silently leak canonical vocabulary into
        ``Law.confidence`` and break ``confidence_level_rank()``).
        """
        from roam.commands.cmd_laws import _CANONICAL_TO_CONFIDENCE

        _VALID_EMIT_VOCAB = {"low", "medium", "high"}
        for canonical, emit in _CANONICAL_TO_CONFIDENCE.items():
            assert emit in _VALID_EMIT_VOCAB, (
                f"_CANONICAL_TO_CONFIDENCE[{canonical!r}] -> {emit!r} "
                f"is NOT a valid emit-vocab slot "
                f"({sorted(_VALID_EMIT_VOCAB)}). The EMIT side is "
                f"closed; widen INPUT only."
            )

    def test_project_confidence_input_helper(self):
        """``_project_confidence_input`` projects canonical tokens to emit vocab.

        Pin the helper contract (case-insensitive, unknown labels fall
        through unchanged so the Click.Choice stays the closed-enum gate).
        """
        from roam.commands.cmd_laws import _project_confidence_input

        # Canonical projections
        assert _project_confidence_input("critical") == "high"
        assert _project_confidence_input("error") == "high"
        assert _project_confidence_input("warning") == "medium"
        assert _project_confidence_input("info") == "low"
        assert _project_confidence_input("note") == "low"
        # Emit-vocab pass-through
        assert _project_confidence_input("high") == "high"
        assert _project_confidence_input("medium") == "medium"
        assert _project_confidence_input("low") == "low"
        # Case-insensitive
        assert _project_confidence_input("CRITICAL") == "high"
        assert _project_confidence_input("Warning") == "medium"
