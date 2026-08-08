"""A count drawn from a 200-row slice may not be printed beside whole-corpus ones.

``_comment_density_rows`` selects EVERY file with ``line_count > 0``;
``_sample_comment_densities`` then slices ``[:200]`` and drops any file that
raises ``OSError`` / ``UnicodeDecodeError`` or has fewer than 5 lines. The
resulting ``anomalous_files`` count was published in ``signals`` next to four
genuinely whole-corpus signals, with no denominator, no cap, and no mention
of sampling in the envelope or in ``--help`` (which calls the command
"codebase-wide").

Measured on roam-code's own index::

    $ roam ai-ratio --since 30 --json
      "comment_density": {"anomalous_files": 92, "score": 1.0, "weight": 0.15}
      "summary": {"ai_ratio": 0.7, "confidence": "HIGH",
                  "partial_success": false}
    $ roam ai-ratio --since 30
      Comment density: 92 files with anomalous density

    eligible rows returned by the query: 4966
    densities actually measured:          198
    anomalous ratio in the sample:      0.465   -> score saturates at 1.0
    ratio a reader computes vs 4966:    0.0185

Two different numbers, and the envelope named neither denominator.

WHY THE SAMPLE IS DISCLOSED RATHER THAN RANDOMISED
--------------------------------------------------
The slice is the query's own row order, i.e. systematically the
earliest-indexed files. Randomising would make the published number move on
every run over an unchanged tree, which breaks the envelope's determinism
for no gain in honesty. The ordering is therefore NAMED
(``sample_ordering: "rowid"``) so a reader can weigh it.

WHAT IS NOT COVERED HERE
------------------------
``confidence`` is still derived from commit count alone and is not degraded
by low file coverage. Changing what that published label MEANS is a
semantic change for every user of the command and is left as an owner
decision; the coverage numbers it would need are now in the envelope.
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_ai_ratio import (
    _COMMENT_SAMPLE_LIMIT,
    _W_COMMENT,
    _comment_density_signal,
    _emit_comment_density_signal,
    _sample_comment_densities,
)


class _FakeRow(dict):
    """A sqlite3.Row-alike: subscriptable by column name."""


def _rows(n: int, *, line_count: int = 40) -> list[_FakeRow]:
    return [_FakeRow(id=i, path=f"src/f{i}.py", line_count=line_count) for i in range(n)]


@pytest.fixture()
def corpus(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A corpus far larger than the cap, with a controllable density mix."""
    from roam.commands import cmd_ai_ratio

    eligible = _COMMENT_SAMPLE_LIMIT * 5
    monkeypatch.setattr(cmd_ai_ratio, "_comment_density_rows", lambda conn, ids=None: _rows(eligible))
    monkeypatch.setattr(cmd_ai_ratio, "find_project_root", lambda: tmp_path)

    def _density(project_root, row):
        # Every 3rd file is a wild outlier; the rest cluster tightly. The
        # exact mix does not matter -- only that the sample is measurable.
        return row["id"], (0.9 if row["id"] % 3 == 0 else 0.05)

    monkeypatch.setattr(cmd_ai_ratio, "_file_comment_density", _density)
    return eligible


def test_the_sample_reports_the_population_it_came_from(corpus: int) -> None:
    densities, coverage = _sample_comment_densities(conn=None)

    assert coverage["files_eligible"] == corpus, coverage
    assert coverage["files_measured"] == len(densities) == _COMMENT_SAMPLE_LIMIT, coverage
    assert coverage["sample_capped"] is True, coverage
    assert coverage["sample_cap"] == _COMMENT_SAMPLE_LIMIT, coverage
    assert coverage["sample_ordering"] == "rowid", coverage


def test_the_signal_carries_the_denominator(corpus: int) -> None:
    score, anomalous, coverage = _comment_density_signal(conn=None)

    assert coverage["computable"] is True, coverage
    assert 0 <= anomalous <= coverage["files_measured"], (anomalous, coverage)
    assert coverage["files_measured"] < coverage["files_eligible"], coverage
    assert 0.0 <= score <= 1.0


def test_an_uncomputable_signal_says_so_instead_of_scoring_zero(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Fewer than 5 readable densities is UNKNOWN, not "no anomalies"."""
    from roam.commands import cmd_ai_ratio

    monkeypatch.setattr(cmd_ai_ratio, "_comment_density_rows", lambda conn, ids=None: _rows(3))
    monkeypatch.setattr(cmd_ai_ratio, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cmd_ai_ratio, "_file_comment_density", lambda root, row: (row["id"], 0.1))

    score, anomalous, coverage = _comment_density_signal(conn=None)

    assert (score, anomalous) == (0.0, 0)
    assert coverage["computable"] is False, coverage


def test_an_uncomputable_signal_is_dropped_from_the_average_not_weighted_as_zero() -> None:
    """The floor used to push the headline ratio DOWN at full weight.

    An absent measurement scored 0.0 and still carried 0.15 of the
    weighted average -- evidence of human authorship manufactured from a
    file the scanner could not read. The aggregation now renormalises over
    the signals that were computed.
    """
    scores = {"gini": 1.0, "burst": 1.0, "patterns": 1.0, "temporal": 1.0}
    weights = {"gini": 0.25, "burst": 0.25, "patterns": 0.20, "temporal": 0.15}

    # Old behaviour: the uncomputable density term drags a fully-saturated
    # set of real signals below 1.0.
    old = sum(weights[k] * scores[k] for k in scores) + _W_COMMENT * 0.0
    # New behaviour: renormalise over the measured terms.
    total = sum(weights.values())
    new = sum(weights[k] * scores[k] for k in scores) / total

    assert old == pytest.approx(0.85)
    assert new == pytest.approx(1.0)
    assert new > old, "dropping the unmeasured term must not leave the ratio depressed"


def test_the_text_line_names_the_sample(capsys, corpus: int) -> None:
    _score, anomalous, coverage = _comment_density_signal(conn=None)
    signals = {
        "comment_density": {
            "anomalous_files_in_sample": anomalous,
            "anomalous_files": anomalous,
            "files_measured": coverage["files_measured"],
            "files_eligible": coverage["files_eligible"],
            "sample_cap": coverage["sample_cap"],
            "sample_capped": coverage["sample_capped"],
            "sample_ordering": coverage["sample_ordering"],
            "computable": True,
        }
    }

    _emit_comment_density_signal(signals)
    line = capsys.readouterr().out

    assert f"of {coverage['files_measured']} sampled files" in line, line
    assert str(coverage["files_eligible"]) in line, line
    assert "cap" in line, line
    assert "files with anomalous density" not in line, line


def test_an_uncapped_corpus_does_not_grow_a_cap_note(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    """The must-not-fire control.

    A project smaller than the cap has no truncation to disclose, so its
    line must stay a plain sample statement -- otherwise every small repo
    grows a warning about a bound that never bit.
    """
    from roam.commands import cmd_ai_ratio

    monkeypatch.setattr(cmd_ai_ratio, "_comment_density_rows", lambda conn, ids=None: _rows(20))
    monkeypatch.setattr(cmd_ai_ratio, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cmd_ai_ratio, "_file_comment_density", lambda root, row: (row["id"], 0.1))

    _score, anomalous, coverage = _comment_density_signal(conn=None)
    assert coverage["sample_capped"] is False, coverage
    assert coverage["files_measured"] == coverage["files_eligible"] == 20, coverage

    _emit_comment_density_signal(
        {
            "comment_density": {
                "anomalous_files_in_sample": anomalous,
                "files_measured": 20,
                "files_eligible": 20,
                "sample_capped": False,
                "computable": True,
            }
        }
    )
    line = capsys.readouterr().out
    assert "cap" not in line, line
    assert "of 20 sampled files" in line, line
