"""W1513 -- the shipped agent-review workflow must accept the verdict roam emits.

``src/roam/templates/ci/agent-review.yml`` is an artefact roam ships to other
people's repositories. Its "Run pr-analyze" step validated
``summary.verdict`` by exact set-membership against
``{INTENTIONAL, SAFE, REVIEW, BLOCK, NOCHANGES}`` -- but pr-analyze has never
emitted a bare token there. ``cmd_pr_analyze`` composes the canonical W607-BY
LAW-6 standalone-parse sentence ``"<VERDICT> (risk_level <level>)"``, so the
validator raised ``SystemExit`` on *every* verdict, including ``SAFE`` on a
clean PR. The drop-in workflow was permanently red for every adopter.

The reason CI never caught it is the shape this file exists to correct: the
old guard asserted that the refusal STRING was present in the template, never
that the refusal FIRED correctly. Presence of a check is not evidence the
check works. Every test below therefore EXECUTES the template's own validator
-- extracted from the shipped YAML, byte-for-byte -- rather than grepping it.

Two families, and the second is the one that matters:

  must-fire   -- each of the five canonical sentences is accepted and yields
                 the bare token on ``$GITHUB_OUTPUT``, and the real envelope
                 produced by a real ``roam pr-analyze`` run parses.
  must-not-fire -- the degraded W607-BY floor sentence
                 ``"pr-analyze completed (risk_level low)"``, a missing
                 verdict, a bare token with no suffix, an unknown risk level,
                 and substring traps such as ``"UNSAFE (risk_level low)"``
                 are all still REFUSED. The step exists to fail closed on a
                 verdict roam could not compute; a looser parse
                 (``verdict.split()[0]``, ``any(v in verdict ...)``) would
                 turn it into a rubber stamp.
"""

from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from tests._helpers.repo_root import repo_root

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

REPO_ROOT = repo_root()
TEMPLATE = REPO_ROOT / "src" / "roam" / "templates" / "ci" / "agent-review.yml"

HEREDOC_OPEN = "python - <<'PY' >> \"$GITHUB_OUTPUT\""
HEREDOC_CLOSE = "PY"


def _extract_validator() -> str:
    """Return the verdict-validator python source from the shipped template.

    Read out of the YAML rather than off a line range so the guard follows the
    step if the file is reordered, and fails loudly if the heredoc is renamed
    or deleted instead of silently testing nothing.
    """
    payload = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    steps = payload["jobs"]["main"]["steps"]
    analyze = [s for s in steps if s.get("id") == "analyze"]
    assert len(analyze) == 1, f"expected exactly one `id: analyze` step, got {len(analyze)}"
    script = analyze[0]["run"]
    assert HEREDOC_OPEN in script, "agent-review.yml no longer validates the pr-analyze verdict"
    body = script.split(HEREDOC_OPEN, 1)[1]
    lines: list[str] = []
    for line in body.splitlines()[1:]:
        if line.strip() == HEREDOC_CLOSE:
            break
        lines.append(line)
    else:  # pragma: no cover -- malformed template
        pytest.fail("unterminated validator heredoc in agent-review.yml")
    source = "\n".join(lines)
    assert "json" in source and "verdict" in source, source
    return source


VALIDATOR_SOURCE = _extract_validator()


def _run_validator(tmp_path: Path, envelope: object) -> tuple[int, str, str]:
    """Run the shipped validator against *envelope*, exactly as CI would.

    Returns ``(returncode, github_output_contents, stderr)``. The workflow
    appends stdout to ``$GITHUB_OUTPUT``, so stdout IS the published value.
    """
    artefacts = tmp_path / ".roam-artifacts"
    artefacts.mkdir(parents=True, exist_ok=True)
    (artefacts / "pr-analysis.json").write_text(_json.dumps(envelope), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-"],
        input=VALIDATOR_SOURCE,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    return proc.returncode, proc.stdout, proc.stderr


def _envelope(verdict: object, **summary_extra: object) -> dict:
    summary: dict = {"verdict": verdict}
    summary.update(summary_extra)
    return {"command": "pr-analyze", "summary": summary}


# ---------------------------------------------------------------------------
# must-fire -- the canonical sentences roam actually emits are accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    ["INTENTIONAL", "SAFE", "REVIEW", "BLOCK", "NOCHANGES"],
)
@pytest.mark.parametrize("level", ["low", "medium", "high", "critical"])
def test_canonical_verdict_sentence_is_accepted(tmp_path, token, level) -> None:
    """Every (verdict x risk_level) pair roam can compose must pass the gate."""
    rc, out, err = _run_validator(tmp_path, _envelope(f"{token} (risk_level {level})"))
    assert rc == 0, f"validator rejected a verdict roam emits: {err}"
    assert out.strip() == f"verdict={token}", out


def test_published_token_feeds_the_block_gate_vocabulary(tmp_path) -> None:
    """The token written to ``$GITHUB_OUTPUT`` must match the `case` arms.

    The `Gate merge on BLOCK` step branches on the bare token; publishing the
    decorated sentence would fall through to the ``*)`` arm and fail the job
    even on a clean PR.
    """
    template = TEMPLATE.read_text(encoding="utf-8")
    assert "INTENTIONAL|SAFE|REVIEW|NOCHANGES)" in template
    for token in ("INTENTIONAL", "SAFE", "REVIEW", "BLOCK", "NOCHANGES"):
        rc, out, _ = _run_validator(tmp_path, _envelope(f"{token} (risk_level low)"))
        assert rc == 0
        assert out.strip().split("=", 1)[1] == token


def test_matching_verdict_code_is_accepted(tmp_path) -> None:
    """``summary.verdict_code`` agreeing with the sentence is a normal envelope."""
    rc, out, err = _run_validator(tmp_path, _envelope("REVIEW (risk_level medium)", verdict_code="REVIEW"))
    assert rc == 0, err
    assert out.strip() == "verdict=REVIEW"


# ---------------------------------------------------------------------------
# must-not-fire -- the refusals the step exists for are all preserved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verdict",
    [
        # The W607-BY floor: emitted when the verdict could NOT be composed.
        # This is the one refusal that must survive any reparse of the field.
        "pr-analyze completed (risk_level low)",
        # Bare token, no canonical suffix -- not a sentence roam emits.
        "SAFE",
        # Substring traps: a `in`-based or split()-based parse would take
        # these for a passing verdict.
        "UNSAFE (risk_level low)",
        "NOT SAFE (risk_level low)",
        "SAFE (risk_level low) BLOCK (risk_level critical)",
        "prefix SAFE (risk_level low)",
        "SAFE (risk_level low) trailing",
        # Risk level outside the canonical closed 4-tier vocabulary.
        "SAFE (risk_level unknown)",
        "SAFE (risk_level LOW)",
        # Absent / wrong-typed verdicts.
        None,
        "",
        42,
    ],
)
def test_uncomputed_or_malformed_verdict_is_refused(tmp_path, verdict) -> None:
    rc, out, err = _run_validator(tmp_path, _envelope(verdict))
    assert rc != 0, f"validator ACCEPTED {verdict!r} -- the gate no longer fails closed"
    assert "invalid or missing pr-analyze verdict" in err, err
    assert out.strip() == "", f"a refused run must publish nothing, got {out!r}"


def test_missing_summary_block_is_refused(tmp_path) -> None:
    """A truncated envelope must not post a green verdict."""
    rc, out, err = _run_validator(tmp_path, {"command": "pr-analyze"})
    assert rc != 0
    assert "invalid or missing pr-analyze verdict" in err
    assert out.strip() == ""


def test_disagreeing_verdict_code_is_refused(tmp_path) -> None:
    """A rewritten envelope whose two verdict surfaces disagree must refuse."""
    rc, out, err = _run_validator(tmp_path, _envelope("BLOCK (risk_level critical)", verdict_code="SAFE"))
    assert rc != 0
    assert "disagrees with verdict" in err, err
    assert out.strip() == ""


# ---------------------------------------------------------------------------
# End-to-end -- the validator against an envelope a REAL pr-analyze produced
# ---------------------------------------------------------------------------

_DIFF_TEXT = (
    "diff --git a/src/auth.py b/src/auth.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/src/auth.py\n"
    "+++ b/src/auth.py\n"
    "@@ -1,4 +1,5 @@\n"
    " from src.models import User\n"
    " \n"
    " def verify_token(t):\n"
    "+    # tweak\n"
    "     return User('test')\n"
    " \n"
)


@pytest.fixture
def pr_analyze_project(tmp_path, monkeypatch):
    proj = tmp_path / "w1513_project"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "models.py").write_text(
        "class User:\n    def __init__(self, name):\n        self.name = name\n",
        encoding="utf-8",
    )
    (src / "auth.py").write_text(
        "from src.models import User\n\ndef verify_token(t):\n    return User('test')\n\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


def _invoke_pr_analyze(proj, stdin: str):
    from roam.cli import cli

    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        return CliRunner().invoke(cli, ["--json", "pr-analyze"], input=stdin, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def test_real_pr_analyze_envelope_passes_the_shipped_validator(pr_analyze_project, tmp_path) -> None:
    """The end-to-end claim: what roam emits is what the workflow accepts.

    This is the assertion whose absence let the defect ship. The old guard
    checked that a refusal string existed in the file; nothing ever fed the
    validator an envelope roam had actually produced.
    """
    result = _invoke_pr_analyze(pr_analyze_project, _DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    payload = _json.loads(result.output)

    summary = payload["summary"]
    assert isinstance(summary.get("verdict"), str)
    rc, out, err = _run_validator(tmp_path / "validate_real", payload)
    assert rc == 0, f"the shipped workflow rejects a real pr-analyze envelope: {err}"
    token = out.strip().split("=", 1)[1]
    assert token in {"INTENTIONAL", "SAFE", "REVIEW", "BLOCK", "NOCHANGES"}
    assert summary["verdict"].startswith(token + " (risk_level ")


def test_real_envelope_publishes_a_machine_readable_verdict_code(pr_analyze_project) -> None:
    """``summary.verdict_code`` mirrors the token so consumers need no parser."""
    result = _invoke_pr_analyze(pr_analyze_project, _DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    summary = _json.loads(result.output)["summary"]
    code = summary.get("verdict_code")
    assert code in {"INTENTIONAL", "SAFE", "REVIEW", "BLOCK", "NOCHANGES"}, summary.get("verdict")
    assert summary["verdict"] == f"{code} (risk_level {summary['risk_level_canonical']})"


def test_verdict_code_is_absent_when_the_verdict_could_not_be_composed(pr_analyze_project, monkeypatch) -> None:
    """Fail-closed parity: the degraded floor publishes NO ``verdict_code``.

    If the machine-readable mirror were derived from the pre-composition
    ``verdict`` local it would still read ``SAFE``/``REVIEW`` on a run whose
    verdict text degraded to the floor, and a consumer preferring the mirror
    would inherit exactly the rubber stamp this finding is about.
    """
    from roam.commands import cmd_pr_analyze

    class _BadLevel:
        def __str__(self):
            raise RuntimeError("synthetic-compute-verdict-from-W1513")

        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-from-W1513")

    monkeypatch.setattr(cmd_pr_analyze, "normalize_risk_level", lambda level: _BadLevel())

    result = _invoke_pr_analyze(pr_analyze_project, _DIFF_TEXT)
    assert result.exit_code in (0, 5), result.output
    payload = _json.loads(result.output)
    summary = payload["summary"]

    assert summary["verdict"] == "pr-analyze completed (risk_level low)"
    assert "verdict_code" not in summary, summary
    assert any(m.startswith("pr_analyze_compute_verdict_failed:") for m in payload.get("warnings_out", []))


def test_degraded_real_envelope_is_refused_by_the_shipped_validator(pr_analyze_project, monkeypatch, tmp_path) -> None:
    """A real degraded run must still fail the workflow's evidence gate."""
    from roam.commands import cmd_pr_analyze

    class _BadLevel:
        def __str__(self):
            raise RuntimeError("synthetic-compute-verdict-from-W1513")

        def __format__(self, spec):
            raise RuntimeError("synthetic-compute-verdict-from-W1513")

    monkeypatch.setattr(cmd_pr_analyze, "normalize_risk_level", lambda level: _BadLevel())

    result = _invoke_pr_analyze(pr_analyze_project, _DIFF_TEXT)
    payload = _json.loads(result.output)
    rc, out, err = _run_validator(tmp_path / "validate_degraded", payload)
    assert rc != 0, "an uncomputed verdict must not pass the workflow's evidence gate"
    assert "invalid or missing pr-analyze verdict" in err
    assert out.strip() == ""


# ---------------------------------------------------------------------------
# Source-of-truth guard -- the parser and the emitter share one vocabulary
# ---------------------------------------------------------------------------


def test_template_vocabulary_matches_the_emitter(tmp_path) -> None:
    """The template's closed sets must be roam's own closed sets.

    Drift here is silent: a new verdict token or risk tier added in
    ``cmd_pr_analyze`` would start failing every adopter's workflow with no
    signal in this repository.
    """
    from roam.commands.cmd_pr_analyze import VERDICT_CODES
    from roam.output.risk import RISK_LEVELS

    for token in VERDICT_CODES:
        rc, _, err = _run_validator(tmp_path / f"v_{token}", _envelope(f"{token} (risk_level low)"))
        assert rc == 0, f"template rejects emitter verdict {token!r}: {err}"
    for level in RISK_LEVELS:
        rc, _, err = _run_validator(tmp_path / f"l_{level}", _envelope(f"SAFE (risk_level {level})"))
        assert rc == 0, f"template rejects canonical risk level {level!r}: {err}"
