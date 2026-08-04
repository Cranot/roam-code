"""W1331 — ``roam migration-plan`` text-branch degradation disclosure.

The W1331 shape: a Pattern-2 silent-fallback marker is produced, threaded
into the JSON envelope's ``summary.warnings_out`` — and never reaches the
text branch a human actually reads. The envelope honestly reports that
``_migration_plan_risk_level`` degraded and safe-floored; the text render
says nothing, so a human running ``roam migration-plan`` without ``--json``
sees a clean plan built on a floored risk rollup.

The INERT-FIX failure mode this module exists to catch
-------------------------------------------------------
A first pass at the fix hoisted only the *declaration* of the warnings
bucket out of the ``if json_mode:`` branch and added an
``echo_text_warnings(...)`` call to the text tail — while leaving the sole
PRODUCER (the ``_migration_plan_risk_level(..., warnings_out=...)`` call)
inside ``if json_mode:``. The text branch then echoes a list that is
*guaranteed* empty: the W1331 scanner goes quiet, the disclosure asymmetry
is untouched, and the false-clean detector is itself false-clean.

So the contract pinned here is deliberately behavioural, not structural:
a run in which the risk helper genuinely degrades MUST emit a marker on the
text path. A test that only asserted "``echo_text_warnings`` appears in the
source" passes against the inert form and is worthless.

Degradation is induced by making the risk vocabulary substrate go dark —
``normalize_risk_level`` stops recognising the per-step tokens, exactly the
Pattern-2 shape ``_migration_plan_risk_level`` documents (an upstream
rename in :mod:`roam.output.risk` desyncing from the per-step vocabulary).
The per-step ``risk`` tokens themselves are untouched, so the ``--max-risk``
gate (which reads ``risk_rank``, not ``normalize_risk_level``) still admits
the steps into the plan and the helper is reached with a non-empty list.

Negative control (the other half of the contract)
--------------------------------------------------
A guard that only checks "a marker appeared" is satisfied by a fix that
prints a marker unconditionally. So :class:`TestNegativeControlHealthyRun`
pins the converse: a healthy run emits ZERO markers, and — because
``echo_text_warnings`` writes to STDERR by design — the degraded run's
STDOUT is byte-identical to the healthy run's. Disclosure must be additive
on stderr, never a mutation of the human-facing plan.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402 — relative import after sys.path mutation

from roam.commands import cmd_migration_plan  # noqa: E402

MARKER_PREFIX = "migration_plan_unknown_severity"

# The plan-generating invocation shared by every test below. The symbol does
# not need to exist: ``_evaluate_move`` resolves 0 callers / no cross-layer
# and emits a ``low``-risk step, which passes the default ``--max-risk=high``
# gate and lands in ``plan`` — i.e. the risk helper is reached with a
# non-empty step-risk list.
PLAN_ARGS = ["migration-plan", "--move", "Nonexistent=src/x.py"]


@pytest.fixture
def migration_plan_project(project_factory, monkeypatch):
    """Indexed project with a couple of symbols — exercises plan generation."""
    proj = project_factory(
        {
            "src/api/users.py": ("def validate_user(name):\n    return name\n\ndef hash_password(p):\n    return p\n"),
            "src/services/user_service.py": (
                "from src.api.users import validate_user\n\n"
                "class UserService:\n"
                "    def serve(self):\n"
                "        return validate_user('x')\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def degrade_risk_vocab(monkeypatch):
    """Make the W631 risk-vocabulary substrate go dark.

    ``_migration_plan_risk_level`` resolves ``normalize_risk_level`` as a
    module global of ``cmd_migration_plan``, so patching it there reaches
    the helper without touching ``risk_rank`` — which the ``--max-risk``
    gate and the sort key depend on. Steps therefore still reach the plan
    and the helper still runs; it just cannot canonicalise any token, sets
    ``saw_unknown``, appends a marker and safe-floors to ``low``.
    """
    monkeypatch.setattr(cmd_migration_plan, "normalize_risk_level", lambda _value: None)


def _marker_lines(stderr: str) -> list[str]:
    """Every ``# warning:`` line carrying the migration-plan marker."""
    return [line for line in stderr.splitlines() if line.startswith("# warning:") and MARKER_PREFIX in line]


# ---------------------------------------------------------------------------
# Positive: the degraded text run must disclose
# ---------------------------------------------------------------------------


class TestTextBranchDisclosesDegradation:
    """The text branch must receive markers the risk helper produces.

    Every assertion here fails against the inert form (producer call left
    inside ``if json_mode:``) because the echoed bucket is always empty.
    """

    def test_degraded_text_run_emits_non_empty_marker(
        self, migration_plan_project, cli_runner, monkeypatch, degrade_risk_vocab
    ):
        """Risk helper degrades → TEXT stderr carries a NON-EMPTY marker."""
        monkeypatch.chdir(migration_plan_project)
        result = invoke_cli(cli_runner, PLAN_ARGS, cwd=migration_plan_project, json_mode=False)

        assert result.exit_code == 0, f"text run failed: {result.exit_code} / {result.output!r}"
        # The plan itself still renders — disclosure is additive, not a bail-out.
        assert "VERDICT:" in result.stdout, f"text plan did not render; stdout={result.stdout!r}"

        markers = _marker_lines(result.stderr)
        assert markers, (
            "W1331 INERT FIX: the risk helper degraded and produced a "
            "warnings marker, but the TEXT branch disclosed nothing. The "
            "producer call (_migration_plan_risk_level(..., warnings_out=...)) "
            "is reachable only from the `if json_mode:` branch, so "
            "echo_text_warnings() echoes a guaranteed-empty list. "
            f"stderr={result.stderr!r}"
        )
        assert any("unknown_token" in line for line in markers), (
            f"expected the unknown-token marker variant; got {markers!r}"
        )

    def test_text_marker_matches_json_warnings_out(
        self, migration_plan_project, cli_runner, monkeypatch, degrade_risk_vocab
    ):
        """No disclosure asymmetry: text stderr carries what the envelope carries.

        This is the W1331 contract proper — the JSON consumer and the human
        must learn the same thing. Run the SAME degraded scenario through
        both branches and compare the marker payloads.
        """
        monkeypatch.chdir(migration_plan_project)

        json_result = invoke_cli(cli_runner, PLAN_ARGS, cwd=migration_plan_project, json_mode=True)
        import json as _json

        envelope = _json.loads(json_result.stdout)
        json_markers = envelope["summary"].get("warnings_out", [])
        assert json_markers, (
            "precondition failed: the degraded scenario did not even reach "
            f"the JSON envelope's warnings_out; summary={envelope['summary']!r}"
        )

        text_result = invoke_cli(cli_runner, PLAN_ARGS, cwd=migration_plan_project, json_mode=False)
        text_markers = [line.removeprefix("# warning: ") for line in _marker_lines(text_result.stderr)]

        assert text_markers == json_markers, (
            "W1331 disclosure asymmetry: the JSON envelope reports "
            f"{json_markers!r} but the text branch reports {text_markers!r}"
        )


# ---------------------------------------------------------------------------
# Negative control: a healthy run must stay silent and byte-identical
# ---------------------------------------------------------------------------


class TestNegativeControlHealthyRun:
    """A fix that discloses unconditionally must NOT pass this class."""

    def test_healthy_text_run_emits_no_marker(self, migration_plan_project, cli_runner, monkeypatch):
        """Undegraded run → zero markers on stderr."""
        monkeypatch.chdir(migration_plan_project)
        result = invoke_cli(cli_runner, PLAN_ARGS, cwd=migration_plan_project, json_mode=False)

        assert result.exit_code == 0
        assert "VERDICT:" in result.stdout
        assert _marker_lines(result.stderr) == [], (
            f"negative control failed: healthy run disclosed a degradation marker; stderr={result.stderr!r}"
        )

    def test_healthy_json_run_omits_warnings_out(self, migration_plan_project, cli_runner, monkeypatch):
        """Undegraded run → the envelope omits warnings_out entirely."""
        monkeypatch.chdir(migration_plan_project)
        result = invoke_cli(cli_runner, PLAN_ARGS, cwd=migration_plan_project, json_mode=True)
        import json as _json

        envelope = _json.loads(result.stdout)
        assert "warnings_out" not in envelope["summary"], (
            f"negative control failed: healthy envelope carries warnings_out={envelope['summary']['warnings_out']!r}"
        )

    def test_stdout_byte_identical_across_degradation(self, migration_plan_project, cli_runner, monkeypatch, request):
        """Markers ride STDERR: degraded STDOUT == healthy STDOUT, byte for byte.

        Pins the ``echo_text_warnings`` contract (stdout stays stable so no
        golden-output test moves) AND doubles as a negative control on the
        hoist: hoisting the producer call out of ``if json_mode:`` must not
        perturb a single byte of the human-facing plan.
        """
        monkeypatch.chdir(migration_plan_project)
        healthy = invoke_cli(cli_runner, PLAN_ARGS, cwd=migration_plan_project, json_mode=False)

        # Degrade only now, so the healthy capture above is untouched.
        request.getfixturevalue("degrade_risk_vocab")
        degraded = invoke_cli(cli_runner, PLAN_ARGS, cwd=migration_plan_project, json_mode=False)

        assert degraded.stdout == healthy.stdout, (
            "degradation disclosure leaked into STDOUT — echo_text_warnings "
            "must write to stderr only.\n"
            f"healthy={healthy.stdout!r}\ndegraded={degraded.stdout!r}"
        )
        # …and the degraded run really was degraded (guards against the
        # byte-identity assertion passing vacuously).
        assert _marker_lines(degraded.stderr), "degraded run produced no marker — byte-identity check was vacuous"
