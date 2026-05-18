"""W805-KKKK — cmd_taint cross-language source/sink scoring (W805 sweep).

Eighty-ninth-in-batch W805 sweep. Distinct axis from W825/W826 (empty
corpus) and the paired-scoring family (dark_matter / duplicates / clones
/ smells).

Hypothesis (CONFIRMED).
-----------------------
``taint`` rules carry a ``languages: [...]`` tag. The matcher in
``roam.security.taint_engine._symbols_matching`` filters candidate
symbols by ``f.language IN (<rule.languages>)``. When the indexed
corpus is *populated* (non-empty), but the rule's declared language
does NOT match any indexed file language, the matcher silently returns
``[]``, the engine's per-rule ``if not sources or not sinks: continue``
short-circuits, and the final verdict is::

    "No taint findings across N rule(s)"

This verdict is INDISTINGUISHABLE from the populated-corpus, rule-
matched, no-flows-found case. Two HIGH-severity concerns:

1. **Pattern-1 variant D (silent success on degraded resolution)** —
   the source/sink resolution is degraded by the language filter, but
   the envelope claims a fully-resolved success: ``partial_success`` is
   absent / False, no ``state`` field discloses the degradation, and
   no ``resolution`` field discloses that 0/N rules matched any
   symbols.

2. **Pattern-2 silent fallback** — the verdict ``"No taint findings
   across 1 rule(s)"`` on a Python corpus with a JS-only rule pack
   reads as a clean security verdict. An agent consuming this envelope
   has no way to learn that the rule pack was a no-op against the
   indexed corpus.

Distinct from W826 (empty-corpus invariants).
---------------------------------------------
W826's pin covers the EMPTY-corpus axis: 0 symbols indexed. W825/W826
already gate that with ``state="empty_corpus"`` + ``partial_success``.

W805-KKKK probes the POPULATED-corpus + LANGUAGE-MISMATCH axis: the
graph has symbols, the rules load, the language filter knocks every
candidate to ``[]``. The W826 empty-corpus branch never fires (because
``symbol_count > 0``); execution falls through to ``run_taint`` which
returns ``[]`` for purely structural reasons.

This is a distinct producer-side gap, not a re-pinning of W826.

Security severity.
------------------
HIGH. The taint command is a security gate that downstream consumers
(``cga``, ``pr-bundle``, CI ``--ci`` exit codes) lean on. A silent-
clean verdict on a language-mismatched run looks identical to a clean
run, which means agents will green-light PRs whose taint coverage
collapsed to zero — exactly the failure mode security gates exist to
prevent.

W978 first-hypothesis discipline.
---------------------------------
Verified BEFORE pinning that the W826 empty-corpus branch
(``symbol_count == 0`` early return) does NOT fire on the language-
mismatch fixture: the fixture is a real Python project with indexed
symbols, so the early-return path is bypassed and execution reaches
``run_taint``. Confirmed via probe: ``roam --json taint --rules-dir
<js-only>`` on a Python corpus returns ``rules: 1, findings: 0,
partial_success: False, state: <missing>`` — exactly the silent-SAFE
shape, NOT the W826 empty-corpus shape.

W907 verify-cycle.
------------------
No defensive "duplicated here to avoid cycle" claims in cmd_taint.py
or taint_engine.py. One local import inside ``_emit_taint_findings``
is explicitly labelled "keep the cost out of the readonly path", which
is a legitimate lazy-import for cost, not a false cycle hedge. Clean.

Pinning style: xfail(strict=True).
----------------------------------
HIGH-severity class given the security-gate context — the failure
mode is silent-SAFE on a security command. xfail-strict so the moment
the fix lands (envelope discloses ``partial_success`` /
``resolution`` / a language-mismatch ``state``), the xfail flips to
XPASS and forces removal of the pin.

Sister-test parity.
-------------------
``test_w826_empty_corpus_invariants_preserved`` re-runs the W825
empty-corpus path here so a regression on the empty-corpus pin would
fail BOTH suites — defence in depth.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Fixtures — populated Python corpus + various rule packs.
# ---------------------------------------------------------------------------


def _git_init(proj):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(proj),
        capture_output=True,
        env=env,
    )


def _make_python_corpus(tmp_path):
    """A populated Python project — guarantees symbol_count > 0 so the
    W826 empty-corpus early-return branch does NOT fire."""
    proj = tmp_path / "py_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "app.py").write_text(
        "import os\n"
        "from flask import request\n"
        "\n"
        "def handle():\n"
        "    q = request.args.get('q')\n"
        "    return run_query(q)\n"
        "\n"
        "def run_query(q):\n"
        "    os.system('echo ' + q)\n",
        encoding="utf-8",
    )
    _git_init(proj)
    return proj


def _make_empty_corpus(tmp_path):
    """Matches W825's empty-corpus fixture so the sister-parity test can
    re-assert the W826 invariants verbatim."""
    proj = tmp_path / "empty_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "empty.py").write_text("", encoding="utf-8")
    _git_init(proj)
    return proj


def _make_js_only_rules_dir(tmp_path):
    """Copy a JS-only rule into a fresh directory so taint runs against a
    rule pack whose ``languages`` declaration cannot match Python files."""
    rules_dir = tmp_path / "js_only_rules"
    rules_dir.mkdir()
    js_rule = (
        "id: js-xss-only\n"
        "description: JS-only rule pack for language-mismatch probe.\n"
        "severity: error\n"
        "cwe: CWE-79\n"
        "languages:\n"
        "  - javascript\n"
        "  - typescript\n"
        "sources:\n"
        "  - req.query\n"
        "  - req.body\n"
        "sinks:\n"
        "  - innerHTML\n"
        "  - eval\n"
    )
    (rules_dir / "js_xss_only.yaml").write_text(js_rule, encoding="utf-8")
    return rules_dir


def _make_qualified_only_unmatchable_rules_dir(tmp_path):
    """A ``qualified_only: true`` rule whose bare-name sink list is
    silently a no-op (W479 advisory). The resulting envelope must
    either disclose the lint or signal partial_success — currently
    neither happens, indistinguishable from a clean run."""
    rules_dir = tmp_path / "qualified_only_rules"
    rules_dir.mkdir()
    rule = (
        "id: qo-bare-sink\n"
        "description: qualified_only=true rule with bare-name sinks (silent no-op).\n"
        "severity: error\n"
        "cwe: CWE-89\n"
        "qualified_only: true\n"
        "languages:\n"
        "  - python\n"
        "sources:\n"
        "  - request.args\n"
        "sinks:\n"
        "  - os_system_bare_no_op\n"
    )
    (rules_dir / "qo_bare_sink.yaml").write_text(rule, encoding="utf-8")
    return rules_dir


def _run_taint_json(proj, *args):
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        init_res = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert init_res.exit_code == 0, init_res.output
        res = runner.invoke(cli, ["--json", "taint", *args], catch_exceptions=False)
        assert res.exit_code == 0, res.output
        return json.loads(res.output)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Pins — W805-KKKK distinct from W826.
# ---------------------------------------------------------------------------


class TestCrossLanguageScoringDisclosure:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-KKKK: HIGH-severity silent-SAFE on language-mismatched rule "
            "pack. roam.security.taint_engine._symbols_matching applies "
            "AND f.language IN (...) so a JS-only rule against a Python "
            "corpus matches zero sources, run_taint returns [], and the "
            "envelope emits 'No taint findings across 1 rule(s)' with "
            "partial_success=False and no 'state' / 'resolution' field — "
            "indistinguishable from a clean run. Pattern-1 variant D + "
            "Pattern-2. Distinct from W826 (empty corpus)."
        ),
    )
    def test_cross_lang_unindexed_sink_disclosed(self, tmp_path):
        """JS-only rule pack against a populated Python corpus.

        Expected on fix: the envelope discloses that the rule was a
        language-mismatch no-op via at least one of:

        * ``summary.partial_success: True``
        * ``summary.state``: a closed-enum string naming the degraded
          state (e.g. ``"language_mismatch"`` / ``"no_sources_matched"``)
        * ``summary.resolution`` or per-rule resolution metadata
          showing the rule matched 0 sources / 0 sinks

        Until the fix, this test xfails-strict so the moment the
        envelope grows ANY of those signals, the xfail flips to XPASS
        and forces removal of the pin.
        """
        proj = _make_python_corpus(tmp_path)
        rules_dir = _make_js_only_rules_dir(tmp_path)
        data = _run_taint_json(proj, "--rules-dir", str(rules_dir))

        summary = data.get("summary") or {}
        # Sanity: this is the populated-corpus + language-mismatch path.
        # W826's empty-corpus state must NOT fire here.
        assert summary.get("state") != "empty_corpus", (
            "W978: W826 empty-corpus branch is firing on a populated "
            "corpus — fixture is wrong, this is the W826 axis not W805-KKKK"
        )
        assert summary.get("rules") == 1, f"expected exactly 1 rule loaded, got {summary.get('rules')!r}"
        assert summary.get("findings") == 0

        # === The W805-KKKK assertion: at least ONE of these disclosure
        # signals MUST be present. None of them are today.
        partial = summary.get("partial_success") is True
        state_disclosed = bool(summary.get("state"))
        resolution_disclosed = bool(summary.get("resolution") or data.get("resolution"))
        # Per-rule resolution metadata at the envelope level is also
        # accepted as a fix signal (e.g. rule_match_summary[]).
        per_rule_disclosed = bool(
            data.get("rule_match_summary") or data.get("rules_matched") or data.get("language_mismatch")
        )
        verdict = (summary.get("verdict") or "").lower()
        verdict_discloses = any(
            frag in verdict
            for frag in (
                "language mismatch",
                "language-mismatch",
                "no symbols matched",
                "no sources matched",
                "0 of 1 rule",
                "no rules matched",
            )
        )

        assert partial or state_disclosed or resolution_disclosed or per_rule_disclosed or verdict_discloses, (
            "Pattern-1 variant D + Pattern-2 silent-SAFE: JS-only rule "
            "against Python corpus produced verdict={!r} with "
            "partial_success={!r}, state={!r}, no resolution disclosure, "
            "and no rule-match summary. Indistinguishable from a clean "
            "Python project. Security severity HIGH (silent-SAFE on a "
            "security-gate command).".format(
                summary.get("verdict"),
                summary.get("partial_success"),
                summary.get("state"),
            )
        )

    def test_qualified_only_unqualified_sink_distinct_from_clean(self, tmp_path):
        """qualified_only=true + bare-name sink → already disclosed by W489-A.

        This is the inverse of the W805-KKKK gap: the W489-A
        ``rules_lint`` disclosure already flips ``partial_success=True``
        when the load-time bare-name lint fires. Pinned as a live
        invariant so a regression in W489-A's disclosure would surface
        here (and reframe the language-mismatch xfail above as the
        SOLE remaining gap on the same family).
        """
        proj = _make_python_corpus(tmp_path)
        rules_dir = _make_qualified_only_unmatchable_rules_dir(tmp_path)
        data = _run_taint_json(proj, "--rules-dir", str(rules_dir))

        summary = data.get("summary") or {}
        assert summary.get("rules") == 1
        assert summary.get("findings") == 0
        # W489-A's load-time bare-name lint MUST surface partial_success.
        assert summary.get("partial_success") is True, (
            "W489-A regression: qualified_only=true + bare-name sink lint "
            "must flip summary.partial_success to True. Got summary="
            f"{summary!r}"
        )
        rules_lint = summary.get("rules_lint") or {}
        assert rules_lint.get("qualified_only_violations", 0) >= 1, (
            "W489-A regression: rules_lint.qualified_only_violations must "
            f"count the bare-name sink. Got rules_lint={rules_lint!r}"
        )


# ---------------------------------------------------------------------------
# Live invariants — these MUST pass today and stay green.
# ---------------------------------------------------------------------------


class TestCrossLanguageScoringInvariants:
    def test_bogus_rules_dir_resolution_disclosure(self, tmp_path):
        """A non-existent ``--rules-dir`` is rejected at the Click layer
        with a non-zero exit and a USAGE-style error.

        Click's ``type=click.Path(exists=True, file_okay=False)`` rejects
        the path before the command body runs — that's the correct
        boundary disclosure (CLI-level rejection, not silent-SAFE).
        """
        proj = _make_python_corpus(tmp_path)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            init_res = runner.invoke(cli, ["init"], catch_exceptions=False)
            assert init_res.exit_code == 0, init_res.output
            res = runner.invoke(
                cli,
                ["--json", "taint", "--rules-dir", "/does/not/exist/at/all"],
                catch_exceptions=False,
            )
        finally:
            os.chdir(old_cwd)

        # Click returns a non-zero exit and emits a Usage-style error —
        # NOT a silent-SAFE JSON envelope. That's the correct boundary
        # disclosure for an invalid path.
        assert res.exit_code != 0, (
            f"bogus --rules-dir should be rejected at Click layer; got exit 0 with output: {res.output!r}"
        )
        assert "does not exist" in res.output.lower() or "invalid value" in res.output.lower()

    def test_w826_empty_corpus_invariants_preserved(self, tmp_path):
        """Sister-test parity: W825/W826 empty-corpus pin still holds.

        Re-asserts the populated-vs-empty-corpus boundary so a
        regression in either direction (W826 silently leaks into
        populated-corpus, OR W805-KKKK fix accidentally weakens the
        W826 disclosure) fails BOTH suites.
        """
        proj = _make_empty_corpus(tmp_path)
        data = _run_taint_json(proj)
        summary = data.get("summary") or {}
        # The W826 empty-corpus contract.
        assert summary.get("state") == "empty_corpus", (
            f"W826 regression: empty corpus must surface state=empty_corpus; got {summary!r}"
        )
        assert summary.get("partial_success") is True, "W826 regression: empty corpus must surface partial_success=True"
        verdict = (summary.get("verdict") or "").lower()
        forbidden = ("safe", "secure", "no taint", "all clear")
        for frag in forbidden:
            assert frag not in verdict, (
                f"W826 regression: empty corpus verdict contains forbidden fragment {frag!r}: {verdict!r}"
            )
