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

from roam.commands.cmd_verify import _emit_empty_verify, _empty_verify_envelope


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
