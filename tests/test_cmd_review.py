"""W1444 — the review affordance must not be a way around its own gate.

The commands exist so the 1b/4b obligations are cheap to FULFIL. The risk
in a convenience wrapper is that it becomes a convenient bypass: emitting
receipts the verifier would reject, accepting a digest instead of
deriving one, or quietly turning a negative review into a pass. Each test
here pins one of those shut.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

from roam.cli import cli  # noqa: E402
from roam.review_receipt import DIGEST_SCHEME, canonical_artifact_sha256, verify_receipt  # noqa: E402

PLAN = "1. add the write lock\n2. fsync before returning\n3. run the ledger tests\n"


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "revproj"
    proj.mkdir()
    # write_bytes, NOT write_text: Windows text mode translates newlines,
    # so the file raw bytes would differ from PLAN and every digest here
    # would compare two different artifacts. This is the raw-byte scheme
    # working as designed - the mismatch fails CLOSED (artifact_stale),
    # and the fix belongs in the transport, not in the hash.
    (proj / "plan.md").write_bytes(PLAN.encode("utf-8"))
    git_init(proj)
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def runner():
    return CliRunner()


def _accept(runner, **over):
    args = {
        "--phase": "1b",
        "--artifact": "plan.md",
        "--builder-family": "claude",
        "--reviewer-family": "openai",
        "--decision": "accept",
    }
    args.update(over)
    argv = ["review-accept"]
    for k, v in args.items():
        argv += [k, v]
    return runner.invoke(cli, argv)


# ---------------------------------------------------------------------------
# review-request: the brief carries the question, never the defence
# ---------------------------------------------------------------------------


def test_request_emits_derived_digest_and_criteria(project, runner):
    res = runner.invoke(cli, ["review-request", "--phase", "1b", "--artifact", "plan.md"])
    assert res.exit_code == 0
    assert canonical_artifact_sha256(PLAN) in res.output
    assert DIGEST_SCHEME in res.output
    assert "UNPROVEN HYPOTHESES" in res.output
    assert PLAN.strip().splitlines()[0] in res.output
    # the reviewer must not be handed the author's defence
    assert "rationale" not in res.output.lower().replace("anchors the reviewer", "")


def test_request_refuses_a_missing_artifact(project, runner):
    """A review must be OF something; there is no permissive default."""
    res = runner.invoke(cli, ["review-request", "--phase", "1b", "--artifact", "nope.md"])
    assert res.exit_code != 0
    assert "not found" in res.output


def test_request_rejects_an_unknown_phase(project, runner):
    res = runner.invoke(cli, ["review-request", "--phase", "9z", "--artifact", "plan.md"])
    assert res.exit_code != 0


# ---------------------------------------------------------------------------
# review-accept: the writer's output must satisfy its own reader
# ---------------------------------------------------------------------------


def test_accept_writes_a_receipt_its_own_verifier_accepts(project, runner):
    res = _accept(runner)
    assert res.exit_code == 0, res.output
    assert "declared_accepted" in res.output

    written = list((project / ".roam" / "reviews").glob("*.json"))
    assert len(written) == 1
    # the round trip that matters: the gate reads this exact file
    result = verify_receipt(
        written[0],
        expected_phase="1b_plan_critique",
        artifact_bytes=PLAN.encode("utf-8"),
        repo_root=project,
    )
    assert result["status"] == "declared_accepted"

    receipt = json.loads(written[0].read_text(encoding="utf-8"))
    assert receipt["artifact_sha256"] == canonical_artifact_sha256(PLAN)
    assert receipt["digest_scheme"] == DIGEST_SCHEME


def test_accept_has_no_digest_flag(project, runner):
    """Trust boundary: a caller-supplied digest would let both sides of the
    verifier's comparison come from the party being judged."""
    res = _accept(runner, **{"--artifact-sha256": canonical_artifact_sha256("something else")})
    assert res.exit_code != 0
    assert "no such option" in res.output.lower()


def test_same_family_is_recorded_with_a_coverage_note_not_refused(project, runner):
    """W1445 — measurement changed this behaviour.

    Pre-registered, blind-judged (n=3/arm, one design with known ground
    truth): a same-family review found the DECISIVE architectural defect
    3/3 -- level with cross-family. Refusing it would discard a review that
    demonstrably works. It IS narrower on the encoding/parser class (0/3 vs
    2/3), so it records with that limitation stated, not silently.
    """
    res = _accept(runner, **{"--reviewer-family": "claude"})
    assert res.exit_code == 0, res.output
    assert "same_family" in res.output
    assert "narrower" in res.output
    assert list((project / ".roam" / "reviews").glob("*.json"))


def test_unresolved_family_is_refused(project, runner):
    res = _accept(runner, **{"--reviewer-family": "unknown"})
    assert res.exit_code != 0
    assert "family_unresolved" in res.output
    assert not list((project / ".roam" / "reviews").glob("*.json"))


def test_blocking_finding_overrides_a_stated_accept(project, runner):
    """The convenience path must not launder a negative review into a pass."""
    res = runner.invoke(
        cli,
        [
            "review-accept",
            "--phase",
            "1b",
            "--artifact",
            "plan.md",
            "--builder-family",
            "claude",
            "--reviewer-family",
            "openai",
            "--decision",
            "accept",
            "--finding",
            "lock is never fsynced|critical",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "rejected" in res.output
    assert "declared_accepted" not in res.output
    # and it is RECORDED, so the verdict gate can read the rejection
    written = list((project / ".roam" / "reviews").glob("*.json"))
    receipt = json.loads(written[0].read_text(encoding="utf-8"))
    assert receipt["findings"] == [{"title": "lock is never fsynced", "severity": "critical"}]


def test_negative_decision_is_recorded_not_refused(project, runner):
    """A reject is a successful RECORDING of a negative outcome."""
    res = _accept(runner, **{"--decision": "reject"})
    assert res.exit_code == 0, res.output
    assert "rejected" in res.output
    assert list((project / ".roam" / "reviews").glob("*.json"))


def test_unknown_severity_is_rejected(project, runner):
    res = runner.invoke(
        cli,
        [
            "review-accept",
            "--phase",
            "1b",
            "--artifact",
            "plan.md",
            "--builder-family",
            "claude",
            "--reviewer-family",
            "openai",
            "--decision",
            "accept",
            "--finding",
            "x|spicy",
        ],
    )
    assert res.exit_code != 0


def test_editing_the_artifact_after_review_makes_the_receipt_stale(project, runner):
    """The binding that makes the whole gate worth having."""
    assert _accept(runner).exit_code == 0
    written = list((project / ".roam" / "reviews").glob("*.json"))[0]
    edited = PLAN + "4. also delete the audit log\n"
    result = verify_receipt(
        written,
        expected_phase="1b_plan_critique",
        artifact_bytes=edited.encode("utf-8"),
        repo_root=project,
    )
    assert result["status"] == "artifact_stale"


def test_json_mode_reports_status_and_partial_success(project, runner):
    res = _accept(runner, **{"--decision": "reject"})
    assert res.exit_code == 0
    res_json = runner.invoke(
        cli,
        [
            "--json",
            "review-accept",
            "--phase",
            "1b",
            "--artifact",
            "plan.md",
            "--builder-family",
            "claude",
            "--reviewer-family",
            "openai",
            "--decision",
            "reject",
        ],
    )
    if res_json.exit_code == 0 and res_json.output.strip().startswith("{"):
        env = json.loads(res_json.output)
        assert env["summary"]["status"] == "rejected"
        assert env["summary"]["partial_success"] is True
