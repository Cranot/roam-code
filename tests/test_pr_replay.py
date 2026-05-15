"""Tests for ``roam pr-replay`` — the productised PR Replay command.

PR Replay is the paid audit deliverable Roam sells before a Review
subscription: $0 DIY 5-PR sample, $2,500 Team (30 PR) report,
$6,000 Deep (90 PR) report. It wraps ``roam postmortem`` with
buyer-facing framing, an aggregated detector-class breakdown, and a
markdown narrative.

Tests focus on:
* Each tier renders a distinct, well-formed report.
* The DIY sample carries the watermark; paid tiers do not.
* JSON envelope is parseable and contains the expected keys.
* ``--output`` writes the markdown to disk (and stdout stays clean).
* ``--client`` is reflected on paid tiers, suppressed on the sample.
* Detector aggregation rolls up correctly.

These tests run against the harness repo via CliRunner; we do **not**
need a fixture project — pr-replay invokes git on the working tree,
and the suite already runs from a real git checkout.
"""

from __future__ import annotations

import json as _json

from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(*args: str, json_mode: bool = False) -> tuple[int, str]:
    """Invoke ``roam pr-replay`` with the given args. Returns (exit_code, output)."""
    from roam.cli import cli

    runner = CliRunner()
    cli_args = (["--json"] if json_mode else []) + ["pr-replay", *args]
    result = runner.invoke(cli, cli_args, catch_exceptions=False)
    return result.exit_code, result.output


# ---------------------------------------------------------------------------
# Smoke tests — every tier produces a valid report.
# ---------------------------------------------------------------------------


def test_sample_tier_runs_and_emits_watermarked_report():
    """``roam pr-replay --tier sample`` is the free DIY entry point."""
    code, out = _invoke("--tier", "sample")
    assert code == 0, f"non-zero exit: {out[:200]}"
    # Header
    assert "# PR Replay Report" in out
    assert "DIY 5-PR sample" in out
    # Watermark — the line that tells the reader this is the free version
    assert "Sample report." in out
    assert "https://roam-code.com/#audit" in out
    # No client header (sample never carries one)
    assert "PR Replay Report — " not in out
    # Methodology block always present
    assert "## Methodology" in out


def test_team_tier_runs_without_watermark():
    """Team tier doesn't carry the sample watermark."""
    code, out = _invoke("--tier", "team")
    assert code == 0
    assert "Team — 30 PRs" in out
    assert "Sample report." not in out


def test_deep_tier_runs_without_watermark():
    """Deep tier doesn't carry the sample watermark either."""
    code, out = _invoke("--tier", "deep")
    assert code == 0
    assert "Deep — 90 PRs" in out
    assert "Sample report." not in out


def test_client_name_appears_on_paid_tier():
    """``--client`` injects the buyer name into the report header."""
    code, out = _invoke("--tier", "team", "--client", "Acme Inc")
    assert code == 0
    assert "PR Replay Report — Acme Inc" in out


def test_client_name_suppressed_on_sample_tier():
    """``--client`` is intentionally ignored on sample (sample is anonymous)."""
    code, out = _invoke("--tier", "sample", "--client", "Acme Inc")
    assert code == 0
    # Header is the unbranded one, not "Report — Acme Inc"
    assert "PR Replay Report — Acme Inc" not in out
    assert "# PR Replay Report\n" in out


# ---------------------------------------------------------------------------
# Custom range
# ---------------------------------------------------------------------------


def test_custom_range_overrides_tier_default():
    """``--range`` wins over the tier-default commit count."""
    code, out = _invoke("--tier", "sample", "--range", "HEAD~3..HEAD")
    assert code == 0
    assert "HEAD~3..HEAD" in out
    # Tier framing is still applied (sample watermark)
    assert "Sample report." in out


# ---------------------------------------------------------------------------
# JSON envelope
# ---------------------------------------------------------------------------


def test_json_envelope_is_well_formed():
    """``roam --json pr-replay`` emits a parseable envelope with the expected keys."""
    code, out = _invoke("--tier", "sample", json_mode=True)
    assert code == 0
    envelope = _json.loads(out)

    # Standard envelope shell
    assert envelope["command"] == "pr-replay"
    assert "schema" in envelope
    assert "summary" in envelope

    # Pr-replay-specific fields
    summary = envelope["summary"]
    assert summary["tier"] == "sample"
    assert "verdict" in summary
    assert "commit_range" in summary
    assert "commits_scanned" in summary
    assert "generated_at" in summary

    # Body
    assert isinstance(envelope.get("commits"), list)
    assert isinstance(envelope.get("by_detector"), list)
    assert isinstance(envelope.get("report_markdown"), str)
    assert envelope["report_markdown"].startswith("# PR Replay Report")


def test_json_summary_top_detector_is_string_or_none():
    """``top_detector`` is either a string or null — never something else."""
    code, out = _invoke("--tier", "sample", json_mode=True)
    assert code == 0
    envelope = _json.loads(out)
    top = envelope["summary"].get("top_detector")
    assert top is None or isinstance(top, str)


# ---------------------------------------------------------------------------
# --output
# ---------------------------------------------------------------------------


def test_output_writes_markdown_to_file(tmp_path):
    """``--output PATH`` writes the markdown report to disk."""
    target = tmp_path / "report.md"
    code, out = _invoke("--tier", "team", "--output", str(target))
    assert code == 0
    assert target.exists(), "output file was not written"
    body = target.read_text(encoding="utf-8")
    assert body.startswith("# PR Replay Report")
    assert "Team — 30 PRs" in body
    # Stdout in --output mode should mention the bytes written
    assert "Wrote" in out
    assert str(target) in out


def test_output_in_json_mode_writes_file_AND_emits_envelope(tmp_path):
    """``roam --json pr-replay --output X`` writes the file *and* emits the envelope."""
    target = tmp_path / "report.md"
    code, out = _invoke("--tier", "team", "--output", str(target), json_mode=True)
    assert code == 0
    # JSON envelope still came out on stdout
    envelope = _json.loads(out[out.find("{") :])
    assert envelope["command"] == "pr-replay"
    assert envelope["summary"]["output_path"] == str(target)
    # File exists and contains the markdown
    assert target.exists()
    assert target.read_text(encoding="utf-8").startswith("# PR Replay Report")


# ---------------------------------------------------------------------------
# Aggregator unit tests — pure-function logic
# ---------------------------------------------------------------------------


def test_aggregate_by_detector_sums_across_commits():
    """``_aggregate_by_detector`` rolls up commit-level kinds lists."""
    from roam.commands.cmd_pr_replay import _aggregate_by_detector

    commits = [
        {"sha": "a", "kinds": ["clones-not-edited x2", "blast-radius x1"]},
        {"sha": "b", "kinds": ["clones-not-edited x1"]},
        {"sha": "c", "kinds": []},
    ]
    out = _aggregate_by_detector(commits)
    # Result is sorted by total_findings descending
    assert out[0]["detector"] == "clones-not-edited"
    assert out[0]["total_findings"] == 3
    assert out[0]["commits_with_finding"] == 2
    assert out[1]["detector"] == "blast-radius"
    assert out[1]["total_findings"] == 1
    assert out[1]["commits_with_finding"] == 1


def test_aggregate_by_detector_handles_empty_input():
    """Empty input returns an empty list (no crash, no None)."""
    from roam.commands.cmd_pr_replay import _aggregate_by_detector

    assert _aggregate_by_detector([]) == []


def test_aggregate_by_detector_skips_malformed_kind_strings():
    """Defensive: malformed ``kinds`` entries are ignored, not crashed on."""
    from roam.commands.cmd_pr_replay import _aggregate_by_detector

    commits = [
        {"sha": "a", "kinds": ["valid x2", "missing-x", None, ""]},
    ]
    out = _aggregate_by_detector(commits)
    assert len(out) == 1
    assert out[0]["detector"] == "valid"
    assert out[0]["total_findings"] == 2


# ---------------------------------------------------------------------------
# Tier dictionary contract
# ---------------------------------------------------------------------------


def test_tiers_dict_has_three_entries_with_required_keys():
    """The tier registry is the public contract — keep it stable."""
    from roam.commands.cmd_pr_replay import _TIERS

    assert set(_TIERS.keys()) == {"sample", "team", "deep"}
    required = {"default_count", "label", "purpose_line", "watermark", "max_per_pr_findings_listed"}
    for tier, meta in _TIERS.items():
        missing = required - meta.keys()
        assert not missing, f"tier '{tier}' missing keys: {missing}"
    # Sample is always watermarked; paid tiers never are
    assert _TIERS["sample"]["watermark"] is True
    assert _TIERS["team"]["watermark"] is False
    assert _TIERS["deep"]["watermark"] is False
    # Tier counts increase with price
    assert _TIERS["sample"]["default_count"] < _TIERS["team"]["default_count"]
    assert _TIERS["team"]["default_count"] < _TIERS["deep"]["default_count"]


# ---------------------------------------------------------------------------
# Engagement ledger
# ---------------------------------------------------------------------------


def test_paid_tier_with_output_appends_to_engagement_ledger(tmp_path, monkeypatch):
    """Paid tiers + --output write a JSONL record to .roam/engagements.jsonl."""
    monkeypatch.chdir(tmp_path)
    # We need a valid git checkout for postmortem to walk; the test repo
    # itself isn't usable from tmp_path. Instead invoke the engagement
    # helper directly — that's the contract that matters.
    from roam.commands.cmd_pr_replay import _record_engagement

    rec = _record_engagement(
        tier="team",
        client="Acme Inc",
        commit_range="HEAD~30..HEAD",
        commits_scanned=30,
        commits_with_findings=11,
        top_detector="clones-not-edited",
        output_path=str(tmp_path / "report.md"),
        generated_at="2026-05-08 10:00 UTC",
    )
    assert rec is not None, "ledger write returned None"
    ledger = tmp_path / ".roam" / "engagements.jsonl"
    assert ledger.exists()
    line = ledger.read_text(encoding="utf-8").strip()
    record = _json.loads(line)
    assert record["tier"] == "team"
    assert record["client"] == "Acme Inc"
    assert record["commits_scanned"] == 30
    assert record["commits_with_findings"] == 11
    assert record["top_detector"] == "clones-not-edited"
    assert record["ledger_schema"] == 1


def test_engagement_ledger_appends_not_overwrites(tmp_path, monkeypatch):
    """Two engagements in the same repo append two lines, not overwrite."""
    monkeypatch.chdir(tmp_path)
    from roam.commands.cmd_pr_replay import _record_engagement

    _record_engagement(
        tier="team",
        client="Acme Inc",
        commit_range="HEAD~30..HEAD",
        commits_scanned=30,
        commits_with_findings=5,
        top_detector="blast-radius",
        output_path="acme.md",
        generated_at="2026-05-08 10:00 UTC",
    )
    _record_engagement(
        tier="deep",
        client="Beta Corp",
        commit_range="HEAD~90..HEAD",
        commits_scanned=90,
        commits_with_findings=22,
        top_detector="clones-not-edited",
        output_path="beta.md",
        generated_at="2026-05-08 11:00 UTC",
    )
    ledger = tmp_path / ".roam" / "engagements.jsonl"
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec_a = _json.loads(lines[0])
    rec_b = _json.loads(lines[1])
    assert rec_a["client"] == "Acme Inc"
    assert rec_b["client"] == "Beta Corp"
    assert rec_b["tier"] == "deep"


def test_no_track_engagement_flag_skips_ledger(tmp_path, monkeypatch):
    """``--no-track-engagement`` opts out — useful for dry-run / CI use."""
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "report.md"
    code, out = _invoke(
        "--tier",
        "team",
        "--output",
        str(output),
        "--no-track-engagement",
    )
    assert code == 0
    ledger = tmp_path / ".roam" / "engagements.jsonl"
    # Either the ledger doesn't exist or it doesn't contain this run.
    if ledger.exists():
        assert ledger.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------


def test_render_pdf_returns_failure_when_neither_backend_present(tmp_path, monkeypatch):
    """When pandoc + reportlab are both absent, PDF render fails gracefully."""
    import sys

    from roam.commands.cmd_pr_replay import _render_pdf

    # Make ``shutil.which("pandoc")`` return None
    monkeypatch.setattr("shutil.which", lambda _: None)
    # Block reportlab import by injecting a sentinel that raises on attribute access
    monkeypatch.setitem(sys.modules, "reportlab", None)

    ok, msg = _render_pdf("# Test\n\nbody", tmp_path / "out.pdf")
    assert ok is False
    assert "pandoc" in msg.lower() or "reportlab" in msg.lower()


def test_pdf_flag_implies_output_when_unset(tmp_path):
    """``--pdf`` without ``--output`` writes the markdown source as ``<pdf>.md``."""
    pdf_target = tmp_path / "report.pdf"
    code, out = _invoke("--tier", "team", "--pdf", str(pdf_target))
    # Note: we can't guarantee the PDF render succeeded (pandoc / reportlab
    # may not be on the test machine), but the markdown sibling MUST be
    # written regardless.
    assert code == 0
    md_sibling = tmp_path / "report.md"
    assert md_sibling.exists(), f"markdown sibling not written next to {pdf_target}"
    assert md_sibling.read_text(encoding="utf-8").startswith("# PR Replay Report")


def test_json_envelope_exposes_pdf_fields(tmp_path):
    """``roam --json pr-replay --output X`` carries pdf_path + pdf_backend in summary."""
    output = tmp_path / "report.md"
    code, out = _invoke("--tier", "sample", "--output", str(output), json_mode=True)
    assert code == 0
    envelope = _json.loads(out[out.find("{") :])
    summary = envelope["summary"]
    # When --pdf is unset, both fields should be None.
    assert "pdf_path" in summary
    assert "pdf_backend" in summary
    assert summary["pdf_path"] is None
    assert summary["pdf_backend"] is None


# ---------------------------------------------------------------------------
# Review-suggestion block — BUILD-PRIORITIES P1.2
#
# Every paid PR Replay should suggest a starter Roam Review configuration
# derived from the detector classes that actually fired. The block is
# absent (not empty) when there's nothing to suggest — Pattern 1 in
# CLAUDE.md: never emit {} where None / absence is the honest signal.
# ---------------------------------------------------------------------------


def test_build_review_suggestions_returns_none_on_empty_input():
    """No detector hits → no suggestions (explicit absence, Pattern 1)."""
    from roam.commands.cmd_pr_replay import _build_review_suggestions

    out = _build_review_suggestions(by_detector=[], commits=[], tier="sample")
    assert out is None, "empty replay must return None, not {}"


def test_build_review_suggestions_emits_block_when_recurring_detector_present():
    """Recurring detector class → all four suggestion parts populated."""
    from roam.commands.cmd_pr_replay import _build_review_suggestions

    by_detector = [
        # Recurring: 5 findings across 3 PRs
        {"detector": "clones-not-edited", "total_findings": 5, "commits_with_finding": 3},
        # Recurring: 2 findings on 2 PRs (low watermark)
        {"detector": "layer-violation", "total_findings": 2, "commits_with_finding": 2},
        # Not recurring: one-off — filtered out
        {"detector": "intent", "total_findings": 1, "commits_with_finding": 1},
    ]
    commits = [
        {"short_sha": "abc1234", "date": "2026-05-01", "subject": "Fix X",
         "high": 2, "medium": 1, "kinds": ["clones-not-edited x2", "impact x1"]},
        {"short_sha": "def5678", "date": "2026-05-02", "subject": "Refactor Y",
         "high": 0, "medium": 2, "kinds": ["intent x2"]},
    ]
    out = _build_review_suggestions(by_detector=by_detector, commits=commits, tier="team")
    assert out is not None, "non-empty replay must return suggestions dict"

    # recurring_risk_classes filters out the one-off
    classes = {row["class"] for row in out["recurring_risk_classes"]}
    assert "clones-not-edited" in classes
    assert "layer-violation" in classes
    assert "intent" not in classes  # one-off, not recurring

    # suggested_roam_rules_yml is a string with copy-pasteable rules
    yml = out["suggested_roam_rules_yml"]
    assert isinstance(yml, str)
    assert "rules:" in yml
    assert "gate-clones-not-edited" in yml
    assert "gate-no-layer-violations" in yml

    # suggested_ci_gates lists concrete `roam <cmd>` invocations w/ rationale
    gates = out["suggested_ci_gates"]
    assert len(gates) >= 1
    for gate in gates:
        assert gate["gate"].startswith("roam "), "gate must be a copy-paste-executable command"
        assert "rationale" in gate and gate["rationale"]
    # Umbrella gate is appended
    gate_strs = {g["gate"] for g in gates}
    assert "roam critique --ci" in gate_strs

    # what_review_would_have_blocked includes the high-severity commit only
    blocked = out["what_review_would_have_blocked"]
    shas = {b["sha"] for b in blocked}
    assert "abc1234" in shas  # high=2 → blocked
    assert "def5678" not in shas  # medium-only → review, not block
    for b in blocked:
        assert "rationale" in b and b["rationale"]

    # Marketing nudge
    assert "Roam Review" in out["upgrade_pitch"]
    assert "https://roam-code.com" in out["upgrade_pitch"]

    # Tier hint round-trips
    assert out["replay_tier"] == "team"


def test_build_review_suggestions_omits_yaml_when_no_template_match():
    """Recurring detectors with NO known template → no fake yaml is generated."""
    from roam.commands.cmd_pr_replay import _build_review_suggestions

    by_detector = [
        # Imaginary detector class — we MUST NOT mock a rule for it
        {"detector": "novel-unknown-detector-xyz", "total_findings": 5, "commits_with_finding": 3},
    ]
    out = _build_review_suggestions(by_detector=by_detector, commits=[], tier="deep")
    assert out is not None  # the unknown detector IS still recurring
    assert out["recurring_risk_classes"][0]["class"] == "novel-unknown-detector-xyz"
    # But we don't fabricate a rule for it
    assert "suggested_roam_rules_yml" not in out, (
        "must NOT generate a YAML rule for an unmapped detector — Pattern 1"
    )


def test_pr_replay_envelope_omits_review_suggestions_when_no_findings():
    """JSON envelope: no detector hits ⇒ no review_suggestions key (explicit absence)."""
    # The current repo's recent commits may or may not flag detectors; we
    # exercise the empty-data path via a stubbed postmortem.
    from roam.commands import cmd_pr_replay as mod

    real_postmortem = mod._run_postmortem

    def _empty_postmortem(commit_range, *, limit):
        return {
            "summary": {"verdict": "no findings", "commits_scanned": 0,
                        "commits_with_findings": 0, "total_high": 0, "total_medium": 0},
            "commits": [],
        }

    mod._run_postmortem = _empty_postmortem
    try:
        code, out = _invoke("--tier", "sample", json_mode=True)
    finally:
        mod._run_postmortem = real_postmortem
    assert code == 0

    envelope = _json.loads(out[out.find("{") :])
    # The key MUST be absent — not an empty object, not null.
    assert "review_suggestions" not in envelope, (
        "Pattern 1: empty replay must omit the key entirely, not emit {}"
    )
    # Summary boolean reflects absence
    assert envelope["summary"]["review_suggestions_present"] is False


def test_pr_replay_envelope_emits_review_suggestions_when_data_present():
    """JSON envelope: detector hits ⇒ review_suggestions block populated."""
    from roam.commands import cmd_pr_replay as mod

    real_postmortem = mod._run_postmortem

    def _flagged_postmortem(commit_range, *, limit):
        return {
            "summary": {
                "verdict": "11/30 PRs would have been flagged",
                "commits_scanned": 30,
                "commits_with_findings": 11,
                "total_high": 4,
                "total_medium": 7,
            },
            "commits": [
                {
                    "short_sha": "abc1234", "date": "2026-05-01",
                    "subject": "Touch many things",
                    "high": 2, "medium": 1,
                    "kinds": ["clones-not-edited x2", "impact x1"],
                },
                {
                    "short_sha": "def5678", "date": "2026-05-02",
                    "subject": "Refactor a layer",
                    "high": 1, "medium": 0,
                    "kinds": ["layer-violation x1"],
                },
                {
                    "short_sha": "ff09abc", "date": "2026-05-03",
                    "subject": "Add clone",
                    "high": 0, "medium": 2,
                    "kinds": ["clones-not-edited x2"],
                },
            ],
        }

    mod._run_postmortem = _flagged_postmortem
    try:
        code, out = _invoke("--tier", "team", json_mode=True)
    finally:
        mod._run_postmortem = real_postmortem
    assert code == 0

    envelope = _json.loads(out[out.find("{") :])
    assert "review_suggestions" in envelope, (
        "non-empty replay must surface review_suggestions block"
    )
    assert envelope["summary"]["review_suggestions_present"] is True

    block = envelope["review_suggestions"]
    # All four mandatory sub-keys present
    assert "recurring_risk_classes" in block
    assert "suggested_ci_gates" in block
    assert "what_review_would_have_blocked" in block
    assert "upgrade_pitch" in block

    # The clones-not-edited detector recurred ⇒ it must be in the list
    recurring_classes = {r["class"] for r in block["recurring_risk_classes"]}
    assert "clones-not-edited" in recurring_classes

    # YAML preview present (we have templates for both recurring classes)
    assert "suggested_roam_rules_yml" in block
    assert "gate-clones-not-edited" in block["suggested_roam_rules_yml"]

    # Blocked PRs are the high-severity ones only
    blocked_shas = {b["sha"] for b in block["what_review_would_have_blocked"]}
    assert "abc1234" in blocked_shas
    assert "def5678" in blocked_shas
    assert "ff09abc" not in blocked_shas  # medium-only

    # Per-PR rationale is concrete (cites detector kinds, not a generic string)
    for b in block["what_review_would_have_blocked"]:
        assert "BLOCK" in b["rationale"]


def test_review_suggestions_bounded_to_at_most_ten_items():
    """Suggestion lists honour the 5-10 items max constraint."""
    from roam.commands.cmd_pr_replay import _build_review_suggestions

    # 15 recurring detector classes (well over the cap)
    by_detector = [
        {"detector": f"detector-{i}", "total_findings": 5, "commits_with_finding": 3}
        for i in range(15)
    ]
    # 15 high-severity commits
    commits = [
        {"short_sha": f"sha{i:04d}", "date": "2026-05-01", "subject": f"PR {i}",
         "high": 1, "medium": 0, "kinds": [f"detector-{i} x1"]}
        for i in range(15)
    ]
    out = _build_review_suggestions(by_detector=by_detector, commits=commits, tier="deep")
    assert out is not None
    assert len(out["recurring_risk_classes"]) <= 10
    assert len(out["suggested_ci_gates"]) <= 10
    assert len(out["what_review_would_have_blocked"]) <= 10
