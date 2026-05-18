"""W805-NNNN — cmd_vuln_reach cross-language reachability scoring (W805 sweep).

Ninetieth-in-batch W805 sweep. Sister to W805-KKKK (cmd_taint cross-lang)
and W826 (cmd_taint empty corpus). Completes the **security-gate
disclosure family 3-strong**:

    * W826                — cmd_taint empty-corpus silent SAFE.
    * W805-KKKK           — cmd_taint cross-language source/sink filter.
    * W805-NNNN (this)    — cmd_vuln_reach cross-language reachability.

Each pins a distinct producer-side gap on a security-gate command where
a silent-SAFE verdict would let CI / agent consumers green-light a
change whose security coverage collapsed to zero.

Hypothesis (CONFIRMED).
-----------------------
``roam.security.vuln_store.match_vuln_to_symbols`` matches a vulnerable
package to indexed symbols via ``symbols.name = pkg_name`` /
``qualified_name LIKE %pkg%`` and an import-edge cross-check. There is
NO language gate on the symbols query, but the cross-language collapse
is structural rather than filter-based: an npm package such as
``lodash`` simply has no symbol named ``lodash`` in a pure-Python corpus,
so the match returns ``[]`` and ``matched_symbol_id`` is stored as
NULL.

``roam.security.vuln_reach.analyze_reachability`` then reports::

    if symbol_id is None or symbol_id not in G:
        result["reachable"] = 0     # <-- the "unmatched" sentinel
    elif path is not None:
        result["reachable"] = 1
    else:
        result["reachable"] = -1    # <-- the "unreachable" sentinel

The TEXT output discloses ``UNMATCHED`` ("Package not found in codebase
symbols") — that branch IS reached. But ``cmd_vuln_reach._output_all``
flattens the three-valued sentinel to a boolean for the JSON envelope::

    "reachable": r["reachable"] == 1

so ``reachable: false`` covers BOTH the "package not indexed" case AND
the "package indexed but no entry-point can reach it" case. An agent
consuming the JSON envelope cannot distinguish

    (a) "lodash npm CVE that is genuinely safe to deprioritize"
        (reachable == -1, indexed and unreachable)

from

    (b) "lodash npm CVE on a pure-Python repo that has no JavaScript
         indexer or bridge installed, so the reachability question was
         never actually answered"
        (reachable == 0, unmatched-and-silent)

Combined with:

    * ``summary.partial_success: false`` (no degradation signal)
    * ``summary.state`` absent (no closed-enum disclosure)
    * ``summary.resolution`` absent
    * ``summary.unmatched_count`` absent
    * ``summary.critical_count: 0`` despite a CRITICAL npm CVE being ingested
    * ``summary.verdict: "0 reachable vulnerabilities"`` reading as clean

a critical npm CVE silently shows up as not-a-concern in the JSON
projection that downstream tools (cga, pr-bundle, --ci gates) consume.

Distinct from W823, W826, and W805-KKKK.
----------------------------------------
* **W823** (cmd_vulns empty corpus) gates on ``state == "no_scan"``
  when ``COUNT(vulnerabilities) == 0``. Here the table contains 1+
  rows, so W823 is bypassed.
* **W826** (cmd_taint empty corpus) gates on
  ``state == "empty_corpus"`` when no symbols are indexed. Here the
  Python corpus is populated, so W826 is bypassed.
* **W805-KKKK** (cmd_taint cross-lang) covers the source/sink language
  filter in ``taint_engine._symbols_matching``. cmd_vuln_reach has no
  such filter — its collapse is structural at the package-name → symbol-
  name matcher in ``vuln_store.match_vuln_to_symbols``, not a language
  filter at the engine. Distinct producer-side gap, distinct file:line.

Security severity.
------------------
HIGH. ``vuln-reach`` is the projection ``cga`` / ``pr-bundle`` / CI
gates consume to ask "is this CVE actually exploitable from an entry
point?" A silent-clean verdict on a cross-language ingest means the
gate is bypassed for ANY ecosystem (npm / Cargo / Maven / Go module)
that the local indexer cannot resolve, which on a Python-or-other-
single-language repo is every external-language CVE.

W978 first-hypothesis discipline.
---------------------------------
Verified BEFORE pinning that:

  * W823's ``state == "no_scan"`` branch does NOT fire — the
    vulnerabilities table has 1+ rows post-vuln-map.
  * W826's ``state == "empty_corpus"`` branch does NOT fire — the
    Python corpus contains symbols, so ``symbol_count > 0`` at the
    engine entry.
  * W805-KKKK's language-filter collapse is in ``taint_engine``, not
    in ``vuln_reach`` — different file, different mechanism.

Probe transcript (see report) shows the JSON envelope emits
``reachable_count: 0, critical_count: 0, partial_success: false,
verdict: "0 reachable vulnerabilities"`` against a fixture with a
``severity: critical`` npm ``lodash`` CVE ingested — exactly the
silent-SAFE shape.

W907 verify-cycle.
------------------
No "duplicated here to avoid cycle" claims in
``src/roam/commands/cmd_vuln_reach.py`` or ``src/roam/security/``.
The local imports inside ``vuln_reach`` (``build_symbol_graph``,
``analyze_reachability`` / ``reach_for_cve`` / ``reach_from_entry``,
``ensure_vuln_table``) are inside the function body — legitimate
lazy-import for networkx-import-cost reasons, NOT a false cycle hedge.

Pinning style: xfail(strict=True).
----------------------------------
HIGH-severity class given the security-gate context. xfail-strict so
the moment the JSON envelope grows ANY disclosure signal (an
``unmatched_count`` field on the summary, a ``state`` /
``resolution`` field, a tri-valued ``reachable`` enum in the per-vuln
record, OR a ``partial_success: true`` flag when one or more vulns
are unmatched), the xfail flips to XPASS and forces removal of the
pin.

Sister-suite parity.
--------------------
``test_w826_invariants_preserved`` re-asserts the empty-corpus pin
here so a regression in W823's no-scan gate would fail BOTH suites.
``test_w805_kkkk_invariants_preserved`` re-asserts that cmd_taint's
language-mismatch xfail family stays distinct (we re-run a populated-
corpus + JS-only-rules probe and confirm the taint envelope still
emits the silent-SAFE shape that W805-KKKK already pins — so
W805-NNNN does not accidentally claim the same axis).
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_python_corpus_with_npm_package_json(tmp_path):
    """Pure-Python corpus that ALSO carries a package.json declaring an
    ``npm`` dependency. The Python indexer extracts Python symbols only
    (no JS bridge, no JS extractor present by default in this fixture),
    so npm package names cannot resolve to indexed symbols even though
    the dependency manifest is present.

    This is the realistic cross-language ingest shape: a polyglot repo
    where a vulnerability scanner reports CVEs for an ecosystem whose
    extractor isn't installed / wired up.
    """
    proj = tmp_path / "py_with_npm_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "package.json").write_text(
        _json.dumps(
            {
                "name": "probe",
                "version": "0.0.1",
                "dependencies": {"lodash": "4.17.20"},
            }
        ),
        encoding="utf-8",
    )
    (proj / "app.py").write_text(
        "def handle():\n"
        "    return process()\n"
        "\n"
        "def process():\n"
        "    return merge_data({})\n"
        "\n"
        "def merge_data(d):\n"
        "    return d\n",
        encoding="utf-8",
    )
    git_init(proj)
    return proj


def _make_empty_corpus(tmp_path):
    """Empty-corpus fixture — matches W823 / W826 shape so the sister-
    parity test can re-assert the no-scan invariant verbatim."""
    proj = tmp_path / "empty_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "empty.py").write_text("", encoding="utf-8")
    git_init(proj)
    return proj


def _write_cross_lang_vuln_report(tmp_path: Path) -> str:
    """Vulnerability report containing:

    * A CRITICAL npm CVE (``lodash``) — the cross-language case.
    * A HIGH Python CVE (``merge_data``) — a control case that DOES
      resolve, so we can confirm the matched-and-reachable branch
      still works (no false-positive on the pin).
    """
    report = [
        {
            "cve": "CVE-2024-NPM-LODASH",
            "package": "lodash",
            "severity": "critical",
            "title": "npm lodash prototype pollution (cross-lang probe)",
        },
        {
            "cve": "CVE-2024-PY-CONTROL",
            "package": "merge_data",
            "severity": "high",
            "title": "Python sink (control case — should resolve)",
        },
    ]
    p = tmp_path / "vulns.json"
    p.write_text(_json.dumps(report), encoding="utf-8")
    return str(p)


def _write_unknown_only_report(tmp_path: Path) -> str:
    """Vulnerability report containing ONLY a CVE for a package that
    cannot resolve to any indexed symbol. Exercises the path where every
    ingested vuln is unmatched — the worst-case silent-SAFE shape."""
    report = [
        {
            "cve": "CVE-2024-NPM-ONLY",
            "package": "left-pad",
            "severity": "critical",
            "title": "npm-only CVE (no Python symbol can resolve this)",
        }
    ]
    p = tmp_path / "vulns_npm_only.json"
    p.write_text(_json.dumps(report), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ingest_and_reach_json(proj, report_path, *extra_reach_args):
    """Run ``roam init``, ``roam vuln-map --generic <report>``, then
    ``roam --json vuln-reach <extra args>`` against ``proj`` and return
    the parsed envelope.
    """
    from roam.cli import cli

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        init_res = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert init_res.exit_code == 0, init_res.output
        ingest_res = runner.invoke(
            cli,
            ["vuln-map", "--generic", report_path],
            catch_exceptions=False,
        )
        assert ingest_res.exit_code == 0, ingest_res.output
        reach_res = runner.invoke(
            cli,
            ["--json", "vuln-reach", *extra_reach_args],
            catch_exceptions=False,
        )
        assert reach_res.exit_code == 0, reach_res.output
        return _json.loads(reach_res.output), reach_res
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# W978 prerequisite: W823 + W826 branches must NOT fire on the W805-NNNN
# fixture. Pinned here so a future regression in either neighbour pin
# doesn't silently change the axis this test covers.
# ---------------------------------------------------------------------------


class TestW805NNNNAxisDistinct:
    def test_w823_no_scan_branch_does_not_fire(self, tmp_path):
        """The vulnerabilities table HAS rows on the W805-NNNN fixture —
        we ingested a generic report — so W823's ``state == 'no_scan'``
        early-return MUST NOT fire here.
        """
        proj = _make_python_corpus_with_npm_package_json(tmp_path)
        report = _write_cross_lang_vuln_report(tmp_path)
        data, _res = _ingest_and_reach_json(proj, report)
        summary = data.get("summary") or {}
        assert summary.get("state") != "no_scan", (
            f"W823 no-scan branch is firing on W805-NNNN fixture "
            f"(this would mean the fixture is wrong, not the bug): {summary!r}"
        )
        # Affirmative side: 2 vulns are ingested, exactly the W805-NNNN axis.
        assert summary.get("total_vulns") == 2, f"expected 2 vulns ingested, got {summary.get('total_vulns')!r}"

    def test_w826_empty_corpus_branch_does_not_fire(self, tmp_path):
        """The Python corpus is populated, so W826's
        ``state == 'empty_corpus'`` branch must not fire.
        """
        proj = _make_python_corpus_with_npm_package_json(tmp_path)
        report = _write_cross_lang_vuln_report(tmp_path)
        data, _res = _ingest_and_reach_json(proj, report)
        summary = data.get("summary") or {}
        assert summary.get("state") != "empty_corpus", (
            f"W826 empty-corpus branch is firing on a populated Python corpus: {summary!r}"
        )


# ---------------------------------------------------------------------------
# The W805-NNNN pin — Pattern-1 variant D + Pattern-2 silent fallback on
# a security-gate command.
# ---------------------------------------------------------------------------


class TestCrossLangVulnReachableDisclosure:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-NNNN: HIGH-severity silent-SAFE on cross-language "
            "vulnerability reachability. roam.security.vuln_store."
            "match_vuln_to_symbols cannot resolve an npm package name "
            "to any symbol in a pure-Python corpus, so matched_symbol_id "
            "is NULL, vuln_reach.analyze_reachability emits reachable=0 "
            "(unmatched) which cmd_vuln_reach._output_all then flattens "
            "to JSON ``reachable: false`` — indistinguishable from "
            "reachable=-1 (genuinely unreachable). The envelope summary "
            "carries no partial_success, no state, no resolution, no "
            "unmatched_count. A CRITICAL npm CVE reports as critical_count=0. "
            "Pattern-1 variant D + Pattern-2. Distinct from W823 (empty "
            "scan), W826 (empty corpus), W805-KKKK (taint language filter)."
        ),
    )
    def test_npm_vuln_silent_unmatched_on_python_corpus(self, tmp_path):
        """An npm CVE ingested against a pure-Python corpus produces an
        envelope that is structurally indistinguishable from a clean
        run.

        Expected on fix: the envelope discloses the cross-language /
        unmatched degradation via at least one of:

        * ``summary.partial_success: True``
        * ``summary.state``: a closed-enum string naming the degraded
          state (e.g. ``"cross_language_unmatched"`` /
          ``"unmatched_vulns_present"``)
        * ``summary.unmatched_count`` / ``summary.unresolved_count``: a
          non-zero integer naming how many ingested vulns failed to
          resolve to indexed symbols
        * Per-vuln ``resolution`` field on each ``vulnerabilities[]``
          entry with a closed enum (``matched`` / ``unmatched`` /
          ``unreachable``), OR a tri-valued ``reachable`` field
          (``"reachable"`` / ``"unreachable"`` / ``"unmatched"``)
          replacing the boolean.
        * Verdict mentions the unmatched / cross-language / unresolved
          state directly.
        """
        proj = _make_python_corpus_with_npm_package_json(tmp_path)
        report = _write_cross_lang_vuln_report(tmp_path)
        data, _res = _ingest_and_reach_json(proj, report)

        summary = data.get("summary") or {}
        vulns = data.get("vulnerabilities") or []

        # Sanity: the fixture is correct — 2 vulns ingested, the Python
        # control case resolves and is reachable.
        assert summary.get("total_vulns") == 2
        py_entries = [v for v in vulns if (v.get("cve") or "").endswith("PY-CONTROL")]
        assert len(py_entries) == 1, f"expected exactly 1 Python control CVE, got {py_entries!r}"
        assert py_entries[0].get("reachable") is True, (
            "fixture sanity: the Python control case MUST resolve and "
            "be reachable — if not, the test exercises the wrong axis"
        )

        # Locate the npm cross-lang case.
        npm_entries = [v for v in vulns if (v.get("cve") or "").startswith("CVE-2024-NPM-LODASH")]
        assert len(npm_entries) == 1, f"expected exactly 1 npm cross-lang CVE, got {npm_entries!r}"
        npm = npm_entries[0]

        # === The W805-NNNN assertion: at least ONE disclosure signal MUST
        # be present. None of them are today.
        partial = summary.get("partial_success") is True
        state_disclosed = bool(summary.get("state"))
        resolution_envelope = bool(summary.get("resolution") or data.get("resolution"))
        unmatched_summary = bool(
            summary.get("unmatched_count") or summary.get("unresolved_count") or summary.get("cross_language_unmatched")
        )
        # Per-vuln: tri-valued ``reachable`` OR explicit ``resolution`` enum.
        per_vuln_disclosed = (
            isinstance(npm.get("reachable"), str)  # tri-valued enum
            or bool(npm.get("resolution"))
            or bool(npm.get("matched") is False and npm.get("unmatched") is True)
        )
        verdict = (summary.get("verdict") or "").lower()
        verdict_discloses = any(
            frag in verdict
            for frag in (
                "unmatched",
                "unresolved",
                "cross-language",
                "cross language",
                "not indexed",
                "no symbol match",
                "1 unmatched",
                "1 unresolved",
            )
        )

        assert (
            partial
            or state_disclosed
            or resolution_envelope
            or unmatched_summary
            or per_vuln_disclosed
            or verdict_discloses
        ), (
            "Pattern-1 variant D + Pattern-2 silent-SAFE: an npm "
            "lodash CRITICAL CVE against a pure-Python corpus emitted "
            "verdict={!r} with partial_success={!r}, state={!r}, "
            "critical_count={!r}, and npm entry reachable={!r} "
            "(boolean, indistinguishable from genuinely-unreachable). "
            "No envelope-level unmatched_count, no per-vuln resolution "
            "field. Security severity HIGH (silent-SAFE on a security-"
            "gate command consumed by cga / pr-bundle / --ci)."
        ).format(
            summary.get("verdict"),
            summary.get("partial_success"),
            summary.get("state"),
            summary.get("critical_count"),
            npm.get("reachable"),
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-NNNN-B: a report whose every CVE is unmatched produces "
            "verdict=='0 reachable vulnerabilities' with critical_count=0 "
            "— a textbook silent-SAFE on a security gate, even though the "
            "ingest path explicitly recorded a CRITICAL row."
        ),
    )
    def test_all_unmatched_report_is_not_silently_safe(self, tmp_path):
        """A report containing ONLY unmatchable vulns — the worst case.

        Expected on fix: the envelope must NOT read as a clean run when
        100% of ingested vulns are unmatched. At minimum the verdict
        should disclose the unmatched count, OR ``partial_success``
        should be True, OR ``state`` should name the degraded condition.
        """
        proj = _make_python_corpus_with_npm_package_json(tmp_path)
        report = _write_unknown_only_report(tmp_path)
        data, _res = _ingest_and_reach_json(proj, report)
        summary = data.get("summary") or {}

        assert summary.get("total_vulns") == 1, f"fixture sanity: expected 1 vuln ingested, got {summary!r}"
        assert summary.get("reachable_count") == 0, (
            f"fixture sanity: unmatched vuln should have reachable_count=0, got {summary!r}"
        )

        # Forbidden verdict fragments — these would all read as
        # silent-SAFE on a security gate when 100% of vulns are
        # unmatched-not-unreachable.
        verdict = (summary.get("verdict") or "").lower()
        for frag in ("0 reachable vulnerabilities", "no reachable", "safe", "clean"):
            assert frag not in verdict, (
                "silent-SAFE verdict on all-unmatched report: "
                f"verdict={verdict!r} contains forbidden fragment {frag!r}. "
                "The CRITICAL CVE was ingested but could not be resolved "
                "to a symbol — the verdict must say so."
            )

    def test_ci_exit_code_does_not_pass_through_cross_lang_collapse(self, tmp_path):
        """The current exit code on a silent cross-lang collapse is 0
        (clean). This is the ``--ci`` pass-through surface: when fix
        lands, a future exit-code review should consider returning a
        non-zero advisory code for unmatched-vuln runs.

        Pinned LIVE (not xfail) — the asymmetry it documents is real
        today: exit 0 + critical_count: 0 on an ingested critical npm
        CVE. If a future fix decides to flip the exit code as part of
        the disclosure surface (rather than just the envelope shape),
        this test will need updating in the same patch.
        """
        proj = _make_python_corpus_with_npm_package_json(tmp_path)
        report = _write_cross_lang_vuln_report(tmp_path)
        data, res = _ingest_and_reach_json(proj, report)

        # Today: exit 0 even on cross-lang collapse.
        assert res.exit_code == 0
        # And the critical-count is 0 despite a CRITICAL CVE being ingested.
        summary = data.get("summary") or {}
        # We DO NOT assert critical_count > 0 here — the xfail above
        # already pins the silent-SAFE shape. Just record the present-day
        # state so a downstream exit-code change is a visible test diff.
        assert summary.get("critical_count") == 0, (
            "Today the envelope reports critical_count=0 on a cross-lang "
            "ingested CRITICAL CVE. If a fix lands that increments this "
            "count for unmatched-but-CRITICAL rows, update this assertion."
        )


# ---------------------------------------------------------------------------
# Sister-suite parity invariants — these MUST pass today and stay green.
# A regression in any of these would fail BOTH the relevant pin and this
# suite, surfacing the cross-axis impact immediately.
# ---------------------------------------------------------------------------


class TestSecurityGateDisclosureFamilyParity:
    def test_w823_no_scan_invariants_preserved(self, tmp_path, monkeypatch):
        """Sister: W823 (cmd_vulns empty corpus) pin still holds.

        Re-asserts that ``roam vulns`` on an empty corpus emits
        ``state == 'no_scan'`` + ``partial_success: True`` — i.e. the
        existing security-gate disclosure on the empty-vulnerabilities-
        table axis has NOT regressed.
        """
        proj = _make_empty_corpus(tmp_path)
        index_in_process(proj)
        monkeypatch.chdir(proj)

        runner = CliRunner()
        from roam.cli import cli

        res = runner.invoke(cli, ["--json", "vulns"], catch_exceptions=False)
        assert res.exit_code == 0, res.output
        env = _json.loads(res.output)
        summary = env.get("summary") or {}
        assert summary.get("state") == "no_scan", (
            f"W823 regression: empty-vulns-table must surface state=='no_scan', got {summary!r}"
        )
        assert summary.get("partial_success") is True, (
            "W823 regression: empty-vulns-table must surface partial_success=True"
        )
        verdict = (summary.get("verdict") or "").lower()
        for frag in ("safe", "secure", "clean", "all clear"):
            assert frag not in verdict, (
                f"W823 regression: empty-scan verdict contains forbidden fragment {frag!r}: {verdict!r}"
            )

    def test_w805_kkkk_taint_cross_lang_invariants_preserved(self, tmp_path):
        """Sister: W805-KKKK (cmd_taint cross-lang) pin still holds.

        Re-runs a populated-Python corpus + JS-only-rules taint probe and
        confirms the taint envelope still emits the silent-SAFE shape
        that W805-KKKK already pins. If this test EVER fails, it means
        either (a) the W805-KKKK fix landed without removing this test,
        or (b) W805-KKKK regressed in the opposite direction — either
        way, the pin file should be reviewed in the same patch.
        """
        proj = _make_python_corpus_with_npm_package_json(tmp_path)
        rules_dir = tmp_path / "js_only_rules"
        rules_dir.mkdir()
        (rules_dir / "js_xss_only.yaml").write_text(
            "id: js-xss-only\n"
            "description: JS-only rule pack for sister-parity probe.\n"
            "severity: error\n"
            "cwe: CWE-79\n"
            "languages:\n"
            "  - javascript\n"
            "  - typescript\n"
            "sources:\n"
            "  - req.query\n"
            "sinks:\n"
            "  - innerHTML\n",
            encoding="utf-8",
        )

        from roam.cli import cli

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            init_res = runner.invoke(cli, ["init"], catch_exceptions=False)
            assert init_res.exit_code == 0, init_res.output
            res = runner.invoke(
                cli,
                ["--json", "taint", "--rules-dir", str(rules_dir)],
                catch_exceptions=False,
            )
            assert res.exit_code == 0, res.output
            data = _json.loads(res.output)
        finally:
            os.chdir(old_cwd)

        summary = data.get("summary") or {}
        # W805-KKKK's shape: rules loaded, findings=0, no partial_success,
        # no state — confirmed silent-SAFE on language-mismatched run.
        # If W805-KKKK fix has landed, this assertion needs updating.
        assert summary.get("rules") == 1, f"W805-KKKK fixture: expected 1 rule loaded, got {summary!r}"
        assert summary.get("findings") == 0
        # Sanity: this is NOT W826's empty-corpus branch.
        assert summary.get("state") != "empty_corpus", f"W826 leaked into populated corpus: {summary!r}"
