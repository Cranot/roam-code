"""W1083-followup-3 — multi-value ``structured_unknown_filter_many``.

The single-value helper (W1077) and its ``to_summary_payload`` sibling
(W1083) cover 7 callsites where a single user-supplied value is
validated against a closed vocabulary. Two BAILed callsites
(``cmd_math --only/--exclude`` + ``cmd_smells --kind``) feed a LIST of
values from a click ``multiple=True`` option; the W1083-RESEARCH memo
recommended a SIBLING helper (not a refactor) that natively partitions
the list, emits per-unknown ``difflib`` suggestions, and pre-formats
warnings_out strings.

These tests pin:

1. Unit-level shape contract for ``structured_unknown_filter_many`` +
   ``to_summary_payload_many`` across the three scenarios callsites care
   about (all-valid, all-unknown, mixed) plus the empty-input edge.
2. End-to-end integration on the two migrated callsites: cmd_math
   ``--only`` and cmd_smells ``--kind``.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.output.structured_unknowns import (
    structured_unknown_filter_many,
    to_summary_payload_many,
)

# ---------------------------------------------------------------------------
# Helper unit tests — three partition scenarios + did_you_mean shape.
# ---------------------------------------------------------------------------


def test_all_valid_emits_no_pattern_1d_disclosure():
    """Happy path: every requested value is in the known set. The
    fragment carries the partition (valid/unknown) but NO ``state`` /
    ``partial_success`` / ``did_you_mean`` keys (Pattern-1D disclosure
    is opt-in iff unknown is non-empty)."""
    frag = structured_unknown_filter_many(
        ["clones", "dead"],
        {"clones", "dead", "complexity"},
        field_name="detector",
        fact_anchor="detectors",
        state="unknown_detectors",
    )
    assert frag["valid_detectors"] == ["clones", "dead"]
    assert frag["unknown_detectors"] == []
    assert frag["requested_detectors"] == ["clones", "dead"]
    assert frag["known_detectors"] == ["clones", "complexity", "dead"]
    # Pattern-1D disclosure fields stay OFF on the happy path.
    assert "state" not in frag
    assert "partial_success" not in frag
    assert "did_you_mean" not in frag
    assert frag["verdict_suffix"] == ""
    assert frag["warnings_text"] == []


def test_all_unknown_partitions_and_emits_disclosure():
    """All-unknown: zero valid, every unknown surfaces, Pattern-1D
    disclosure stamped (state + partial_success + did_you_mean for
    each unknown that has a close match)."""
    frag = structured_unknown_filter_many(
        ["clonez", "garblargleXYZ"],
        {"clones", "dead", "complexity"},
        field_name="detector",
        fact_anchor="detectors",
        state="unknown_detectors",
    )
    assert frag["valid_detectors"] == []
    assert frag["unknown_detectors"] == ["clonez", "garblargleXYZ"]
    assert frag["state"] == "unknown_detectors"
    assert frag["partial_success"] is True
    # ``clonez`` is within cutoff 0.6 of ``clones``; ``garblargleXYZ``
    # is far from every known entry. ``did_you_mean`` is a per-unknown
    # map carrying ONLY entries that had a hit (no synthetic empties).
    assert "clonez" in frag["did_you_mean"]
    assert "clones" in frag["did_you_mean"]["clonez"]
    assert "garblargleXYZ" not in frag["did_you_mean"]
    # Verdict-suffix is single-line (LAW 6 — works without other fields).
    assert "\n" not in frag["verdict_suffix"]
    assert "'clonez'" in frag["verdict_suffix"]
    # Pre-formatted warnings_text: one entry per unknown, ready to
    # splice into warnings_list via ``warnings_list.extend(...)``.
    assert len(frag["warnings_text"]) == 2
    assert any("clonez" in w for w in frag["warnings_text"])
    assert any("garblargleXYZ" in w for w in frag["warnings_text"])


def test_mixed_partition_carries_both_subsets():
    """Mixed: one valid, one unknown. The partition surfaces both
    subsets explicitly so the caller can drop a local partition loop."""
    frag = structured_unknown_filter_many(
        ["clones", "clonez"],
        {"clones", "dead", "complexity"},
        field_name="detector",
        fact_anchor="detectors",
        state="unknown_detectors",
    )
    assert frag["valid_detectors"] == ["clones"]
    assert frag["unknown_detectors"] == ["clonez"]
    assert frag["partial_success"] is True
    assert "clonez" in frag["did_you_mean"]


def test_empty_input_emits_no_unknown_and_no_warning():
    """Edge case: caller passes an empty list (the click option was not
    given). No unknowns, no warnings, no Pattern-1D disclosure. The
    helper still returns a dict for consistent shape — neither callsite
    treats this as an error."""
    frag = structured_unknown_filter_many(
        [],
        {"clones", "dead"},
        field_name="detector",
        fact_anchor="detectors",
        state="unknown_detectors",
    )
    assert frag["requested_detectors"] == []
    assert frag["valid_detectors"] == []
    assert frag["unknown_detectors"] == []
    assert "state" not in frag
    assert "partial_success" not in frag
    assert frag["warnings_text"] == []


def test_drop_empty_skips_blank_entries():
    """``drop_empty=True`` (default) skips falsy entries. cmd_smells
    relies on this (``for k in kind_filter: if not k: continue``)."""
    frag = structured_unknown_filter_many(
        ["", "clones", ""],
        {"clones", "dead"},
        field_name="detector",
        fact_anchor="detectors",
        state="unknown_detectors",
    )
    assert frag["valid_detectors"] == ["clones"]
    assert frag["unknown_detectors"] == []


def test_partial_success_only_when_unknown_non_empty():
    """The ``partial_success`` derivation is ``bool(unknown)``. Even with
    a populated ``requested``, if every value is valid we MUST NOT flip
    partial_success — that would mark a clean run as degraded."""
    frag_clean = structured_unknown_filter_many(
        ["clones"],
        {"clones", "dead"},
        field_name="detector",
        fact_anchor="detectors",
        state="unknown_detectors",
    )
    assert "partial_success" not in frag_clean
    frag_dirty = structured_unknown_filter_many(
        ["typo"],
        {"clones", "dead"},
        field_name="detector",
        fact_anchor="detectors",
        state="unknown_detectors",
    )
    assert frag_dirty["partial_success"] is True


# ---------------------------------------------------------------------------
# ``to_summary_payload_many`` — splice helper for envelope-merging.
# ---------------------------------------------------------------------------


def test_to_summary_payload_many_excludes_presentation_fields():
    """Payload carries summary-shape fields ONLY. ``facts``,
    ``verdict_suffix``, ``warnings_text`` belong on agent_contract /
    verdict / warnings_out respectively and must not leak into
    summary."""
    frag = structured_unknown_filter_many(
        ["typo"],
        {"clones", "dead"},
        field_name="detector",
        fact_anchor="detectors",
        state="unknown_detectors",
    )
    payload = to_summary_payload_many(frag)
    assert "facts" not in payload
    assert "verdict_suffix" not in payload
    assert "warnings_text" not in payload


def test_to_summary_payload_many_include_known_false_drops_known():
    """``include_known=False`` lets cmd_math keep its lighter pre-
    migration envelope (no ``known_detectors`` echo on summary)."""
    frag = structured_unknown_filter_many(
        ["typo"],
        {"clones", "dead"},
        field_name="detector",
        fact_anchor="detectors",
        state="unknown_detectors",
    )
    payload = to_summary_payload_many(frag, include_known=False)
    assert "known_detectors" not in payload
    assert "requested_detectors" in payload
    assert "unknown_detectors" in payload


# ---------------------------------------------------------------------------
# Shared fixture: build a minimal git-tracked roam workspace.
# ---------------------------------------------------------------------------


@pytest.fixture
def roam_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    runner = CliRunner()
    init_result = runner.invoke(cli, ["init"], catch_exceptions=False)
    assert init_result.exit_code == 0, init_result.output
    return repo


# ---------------------------------------------------------------------------
# Integration — cmd_math: --only with mixed (valid + unknown) names.
# ---------------------------------------------------------------------------


def test_cmd_math_only_unknown_envelope_carries_did_you_mean(roam_repo):
    """End-to-end cmd_math integration. ``--only foo,bar`` where ``bar``
    is unknown: envelope still carries ``only_unknown`` (W1057
    pre-migration field) AND the new ``unknown_only_detectors`` +
    ``did_you_mean`` (W1083-followup-3) suggestions."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "math", "--only", "totally-not-a-real-detector"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    raw = getattr(result, "stdout", None) or result.output
    payload = json.loads(raw)
    summary = payload["summary"]
    # Pre-W1083 invariant: only_unknown echo still present.
    assert summary.get("only_unknown") == ["totally-not-a-real-detector"]
    assert summary.get("partial_success") is True
    assert summary.get("warnings_count", 0) >= 1
    # W1083-followup-3: helper-derived state + partition fields.
    assert summary.get("state") in {"unknown_only_detectors", "unknown_exclude_detectors"} or \
        "unknown_only_detectors" in summary or \
        "unknown_exclude_detectors" in summary
    # The unknown name surfaces on the helper's unknown_only_detectors list.
    assert summary.get("unknown_only_detectors") == ["totally-not-a-real-detector"] or \
        summary.get("requested_only_detectors") == ["totally-not-a-real-detector"]


def test_cmd_math_no_filter_envelope_byte_close_to_pre_migration(roam_repo):
    """No --only/--exclude: the helper is never invoked. Envelope must
    NOT carry any of the new W1083-followup-3 keys so the default-path
    envelope stays byte-close to pre-W1083 shape."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "math"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    raw = getattr(result, "stdout", None) or result.output
    payload = json.loads(raw)
    summary = payload["summary"]
    # Helper-derived keys must NOT appear on the default path.
    for forbidden in (
        "unknown_only_detectors",
        "unknown_exclude_detectors",
        "requested_only_detectors",
        "requested_exclude_detectors",
        "valid_only_detectors",
        "valid_exclude_detectors",
        "only_did_you_mean",
        "exclude_did_you_mean",
    ):
        assert forbidden not in summary, f"{forbidden} leaked onto default path"
    # And the pre-W1083 unknown keys also stay off.
    assert "only_unknown" not in summary
    assert "exclude_unknown" not in summary


# ---------------------------------------------------------------------------
# Integration — cmd_smells: --kind with unknown id.
# ---------------------------------------------------------------------------


def test_cmd_smells_kind_unknown_envelope_carries_did_you_mean(roam_repo):
    """End-to-end cmd_smells integration. ``--kind foo`` where ``foo``
    is unknown: envelope carries the W1083-followup-3 partition + the
    pre-W1066 warning string still in ``warnings_out``."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--json", "smells", "--kind", "totally-not-a-real-smell"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    raw = getattr(result, "stdout", None) or result.output
    payload = json.loads(raw)
    summary = payload["summary"]
    # The W987 / W1066 warnings still flow into the envelope's
    # warnings_out (the callsite re-formats them for wire-compat).
    assert any(
        "totally-not-a-real-smell" in w
        for w in payload.get("warnings_out", [])
    )
    # W1083-followup-3 helper-derived disclosure on summary.
    assert summary.get("partial_success") is True
    assert summary.get("state") == "unknown_kinds"
    assert summary.get("unknown_kinds") == ["totally-not-a-real-smell"]


def test_cmd_smells_no_kind_envelope_does_not_carry_helper_keys(roam_repo):
    """No --kind: helper is never invoked. Default-path envelope must
    not carry any of the new W1083-followup-3 keys."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "smells"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    raw = getattr(result, "stdout", None) or result.output
    payload = json.loads(raw)
    summary = payload["summary"]
    for forbidden in (
        "state",
        "unknown_kinds",
        "valid_kinds",
        "requested_kinds",
        "did_you_mean",
    ):
        assert forbidden not in summary, f"{forbidden} leaked onto default path"
