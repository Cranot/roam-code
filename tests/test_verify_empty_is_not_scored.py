"""``roam verify`` on a clean tree must not narrate a verification it never ran.

The empty-paths branch (``_emit_empty_verify`` / ``_empty_verify_envelope``)
fires when changed-file discovery returns nothing. Zero files were read and
``checks_run`` is ``[]``, so any human line claiming that checks *passed*, or
advertising a quality *score*, is the ``fabricated_success`` shape this very
module legislates against (see cmd_verify.py:2127, :3982, :4915, :6874).

The wire envelope is a different question from the human text, and the two are
fixed differently on purpose -- see ``test_wire_score_is_pinned_by_compile_code``
below for the measured reason the numbers stay.
"""

from __future__ import annotations

import json

from roam.commands.cmd_verify import _ALL_CHECKS, _emit_empty_verify, _empty_verify_envelope

# The nine keys ``_empty_verify_envelope`` may emit under ``summary``. Pinned as
# a literal because compile-code's ``_require_known_shape(summary,
# allowed=_VERIFY_SUMMARY_KEYS)`` (src/compile_code/cli.py:2463) is a CLOSED key
# set: an unknown key raises ``ValueError("summary_schema")`` and exits
# EXIT_TOOLCHAIN on every clean tree. Read at compile-code cli.py:2463 --
# all nine below are members, so this envelope is accepted today.
_EMPTY_VERIFY_SUMMARY_KEYS = frozenset(
    {
        "verdict",
        "score",
        "threshold",
        "files_checked",
        "violation_count",
        "state",
        "checks_run",
        "verification_complete",
        "partial_success",
    }
)

# Substrings that state, in the agent-facing block, that nothing ran. At least
# one must appear; the list exists so rewording a fact does not silently drop
# the disclosure.
_NOT_RUN_MARKERS = ("not verified", "no check ran", "never applied", "not-run default")


def _summary_text(capsys) -> str:
    _emit_empty_verify(json_mode=False, threshold=70, summary_mode=True)
    return capsys.readouterr().out


def _verdict_text(capsys) -> str:
    _emit_empty_verify(json_mode=False, threshold=70, summary_mode=False)
    return capsys.readouterr().out


def test_summary_mode_does_not_claim_checks_passed(capsys):
    """``checks_run == []``: nothing passed, because nothing ran."""
    out = _summary_text(capsys)

    assert "all checks passed" not in out, f"empty verify claims checks passed with checks_run=[]; got {out!r}"
    assert "0 checks run" in out, f"empty verify must say no checks ran; got {out!r}"


def test_verdict_line_does_not_advertise_a_score(capsys):
    """``score 100/100`` reads as a measured quality score for zero work."""
    out = _verdict_text(capsys)

    assert "100/100" not in out, f"empty verify advertises a fabricated score; got {out!r}"
    assert "nothing to verify" in out, f"empty verify must name the not-run state; got {out!r}"


def test_envelope_keeps_the_not_run_discriminators():
    """The machine-readable "nothing ran" signals are the real guard; pin them.

    A JSON consumer distinguishes this envelope from a performed verification
    on these four fields, not on the score. Removing any one of them is what
    would actually make the PASS indistinguishable.
    """
    summary = _empty_verify_envelope(70)["summary"]

    assert summary["state"] == "no_changes"
    assert summary["files_checked"] == 0
    assert summary["checks_run"] == []
    assert summary["violation_count"] == 0


def test_wire_score_is_pinned_by_compile_code():
    """Do not null these numbers. Measured: it breaks the verification boundary.

    ``compile verify`` shells ``roam --json verify`` and validates the result
    with ``_validate_verify_protocol`` (compile-code src/compile_code/cli.py:3228).
    On the no-changes branch it hard-requires ``summary.score == 100``
    (cli.py:3275 ``_plain_int`` rejects ``None`` outright, then cli.py:3303),
    ``categories[*].score == 100`` (cli.py:3329) and the full eleven-category
    set (cli.py:3310). Feeding it ``score: None`` raises
    ``ValueError(invalid_integer)`` and omitting ``categories`` raises
    ``ValueError(envelope_contract)`` -- either one exits ``EXIT_TOOLCHAIN`` on
    every clean tree. The honest fix for the fabricated-PASS shape is therefore
    the human text (the tests above), not the wire number.
    """
    envelope = _empty_verify_envelope(70)

    assert envelope["summary"]["score"] == 100
    assert type(envelope["summary"]["score"]) is int
    for name, category in envelope["categories"].items():
        assert category["score"] == 100, name
        assert type(category["score"]) is int, name
    # JSON mode is unchanged by the text fix.
    assert json.loads(json.dumps(envelope))["summary"]["verdict"] == "PASS"


def test_agent_contract_facts_do_not_assert_a_verification_that_never_ran():
    """The AGENT-facing block is the one the MCP tool returns verbatim.

    ``roam_verify`` (src/roam/mcp_server.py) shells ``roam --json verify
    --changed`` and returns the envelope with no post-processing of
    ``agent_contract``, so these five strings are what the primary consumer
    reads. Auto-derivation turned ``summary`` into ``["PASS", "score 100", ...]``
    -- a verdict and a quality score for zero files and zero checks, which is
    the same ``fabricated_success`` shape the human text was fixed for.
    """
    facts = _empty_verify_envelope(70)["agent_contract"]["facts"]

    assert "PASS" not in facts, f"agent_contract asserts a bare PASS having run no checks; got {facts!r}"
    assert "score 100" not in facts, f"agent_contract advertises an unqualified score; got {facts!r}"

    joined = " ".join(facts).lower()
    assert any(marker in joined for marker in _NOT_RUN_MARKERS), (
        f"agent_contract must state that nothing ran; got {facts!r}"
    )
    # A fact may still report the wire score (it is pinned, see below) but only
    # while carrying its disclaimer in the SAME fact -- an agent reading one
    # line must not be able to take "score 100" as a measurement.
    for fact in facts:
        if "score 100" in fact:
            assert "not-run default" in fact, f"score fact lacks its disclaimer: {fact!r}"


def test_agent_contract_names_the_checks_that_did_not_run():
    """``0 of N checks`` must track ``_ALL_CHECKS``, not a hardcoded N.

    Measured: ``len(_ALL_CHECKS)`` is 35 today. A literal in the fact string
    would quietly become a false count the next time a check is registered.
    """
    facts = _empty_verify_envelope(70)["agent_contract"]["facts"]

    assert any(f"0 of {len(_ALL_CHECKS)} checks" in fact for fact in facts), (
        f"agent_contract must count the checks it skipped; got {facts!r}"
    )


def test_explicit_facts_do_not_drop_the_auto_derived_contract_keys():
    """``_merge_agent_contract`` fills gaps; passing facts must not empty it.

    ``next_commands`` and ``confidence`` come from the auto-derivation and are
    what an agent uses to decide the next call. ``risks`` was ``[]`` -- an empty
    risk list beside a PASS reads as "nothing to worry about".
    """
    contract = _empty_verify_envelope(70)["agent_contract"]

    assert set(contract) >= {"facts", "risks", "next_commands", "confidence"}, (
        f"agent_contract lost an auto-derived key; got {sorted(contract)}"
    )
    assert contract["risks"], "empty risks beside a not-run PASS reads as 'no concerns'"


def test_summary_key_set_is_untouched_by_the_agent_contract_fix():
    """compile-code's allowed-key set is CLOSED; adding a summary key breaks it.

    This is the control on the fix, not a defect probe: it is green before and
    after. ``_require_known_shape(summary, allowed=_VERIFY_SUMMARY_KEYS)``
    (compile-code cli.py:2463) rejects an unknown key with
    ``ValueError("summary_schema")``, so the disclosure had to land in
    ``agent_contract`` -- which cli.py:3257 only requires to be a dict.
    """
    assert set(_empty_verify_envelope(70)["summary"]) == _EMPTY_VERIFY_SUMMARY_KEYS
