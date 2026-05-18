"""W805-JJ -- empty-corpus Pattern-2 smoke test on ``roam hover``.

Thirty-sixth-in-batch W805 sweep. Symbol-resolver-bearing single-target
hover summary. Cross-axis peer of W805-T (cmd_uses, symbol resolver +
Pattern-1-V-D) and W805-EE (cmd_simulate, fabricated-on-empty metric).

Scope
-----

cmd_hover has three envelope-emitting branches
(src/roam/commands/cmd_hover.py):

1. ``sym is None`` (line 134-159) -- unresolved target. ALREADY canonical
   per W1272 / W1277: emits ``state="not_found"`` + ``resolution="unresolved"``
   + ``partial_success=True`` + verdict naming the target. **No bug.**

2. Resolved + graph_metrics row exists (line 162-220) -- full success.
   Real ``in_degree`` / ``out_degree`` / ``pagerank`` populated from
   the graph_metrics table. **No bug.**

3. Resolved + graph_metrics row MISSING (line 166-168 fallback branch:
   ``in_d = metrics["in_degree"] if metrics else 0``) -- the symbol
   resolves cleanly but its graph_metrics row was never computed
   (incomplete index, partial build, post-delete repair, etc.). The
   fallback silently zero-fills in_d / out_d / pr, then proceeds to
   emit a verdict identical to a legitimate zero-edge leaf:
   ``"fn leaf_fn -- none blast radius (0 in, 0 out)"`` with
   ``blast_bucket="none"`` and ``partial_success=false``. **REAL BUG.**

W978 first-hypothesis discipline
--------------------------------

First hypothesis: cmd_hover is the canonical resolver-bearing single-
target command, structurally analogous to cmd_uses (W805-T) which
silently SAFEd on the not_found path. Probe result on the live tree:

* Empty corpus + unresolved target -> envelope CORRECTLY emits
  ``state="not_found"``, ``resolution="unresolved"``, and
  ``partial_success=true``. cmd_hover passed the W1272 hardening.
  **No bug on this branch.**

* Resolved leaf with real zero edges (legitimate ``graph_metrics`` row
  with all zeros) -> envelope emits a loud verdict
  (``none blast radius (0 in, 0 out)``) and the underlying metric is
  real. **No bug on this branch either** -- the zero IS real data, the
  verdict is honest.

* Resolved leaf with graph_metrics row DELETED (simulates stale /
  partial / out-of-sync index) -> envelope emits the SAME
  ``in_degree: 0, out_degree: 0, pagerank: 0.0, blast_bucket: "none",
  partial_success: false`` shape. **REAL BUG**: an agent reading only
  structured fields cannot distinguish "legitimately-zero metrics" from
  "metrics-not-yet-computed". The verdict reads as confident
  ground-truth when the underlying signal is absent.

W978 re-run check: probed twice (legitimate-zero vs metrics-deleted),
output byte-identical apart from non-determinstic ``_meta`` fields.
Hypothesis stands.

Conclusion
----------

* **Unresolved branch passes the canonical W1272 contract**. cmd_hover
  is one of the earlier adopters of resolution-disclosure -- credit
  preserved with positive regression tests.

* **REAL BUG pinned: Pattern-1-V-D + W805-EE-axis cross on the
  resolved-but-metric-missing branch** (cmd_hover.py:166-168). The
  ``metrics if metrics else 0`` fallback fabricates a healthy-looking
  blast-radius signal from absent state. Pinned xfail-strict so a future
  fix that adds ``state="metrics_unavailable"`` or stamps a
  ``metrics_present: false`` disclosure graduates to PASS.

* **Bug class**: dual-pattern -- Pattern-1 Variant D (silent success on
  degraded resolution: symbol resolves but its metric source is missing)
  AND W805-EE fabrication-axis (auto-zero on empty fabricates a real-
  looking metric). The two patterns are structurally the same bug here:
  silently inferring "zero is fine" when "metric not present" is the
  honest disclosure.

Sweep brief: W805-JJ (Wave805-JJ, thirty-sixth-in-batch).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402 -- relative-to-tests-dir import after sys.path mutation
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path):
    """Project with only a README -- no indexable source symbols.

    Exercises the ``sym is None`` branch (line 134-159): find_symbol
    returns None on every tier of the resolver chain.
    """
    proj = tmp_path / "empty_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("Empty corpus project.\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def leaf_corpus(tmp_path):
    """Project with one function and no callers/callees.

    Exercises the legitimate-zero branch: ``leaf_fn`` resolves and its
    graph_metrics row exists with all zeros.
    """
    proj = tmp_path / "leaf_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "lonely.py").write_text("def leaf_fn():\n    return 42\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def stale_metrics_corpus(tmp_path):
    """Project with one function whose graph_metrics row has been
    deleted post-index to simulate a stale / partial / out-of-sync
    index.

    Exercises the ``metrics is None`` fallback branch (line 166-168):
    find_symbol returns a real row, but the graph_metrics row is
    missing. The fallback silently zero-fills in_d / out_d / pr.
    """
    proj = tmp_path / "stale_metrics_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "lonely.py").write_text("def leaf_fn():\n    return 42\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"

    # Delete the graph_metrics row to simulate a partial / out-of-sync
    # index. The symbols row stays; the resolver still finds leaf_fn.
    db_path = proj / ".roam" / "index.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "DELETE FROM graph_metrics WHERE symbol_id IN (SELECT id FROM symbols WHERE name = ?)", ("leaf_fn",)
        )
        conn.commit()
    finally:
        conn.close()
    return proj


@pytest.fixture
def clean_corpus(tmp_path):
    """Project with one callee + one caller -- exercises full success branch."""
    proj = tmp_path / "clean_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text("def callee_fn():\n    return 42\n\ndef caller_fn():\n    return callee_fn()\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant C -- no crash / no empty stdout on the not_found path.
# ---------------------------------------------------------------------------


class TestEmptyCorpusNoCrash:
    """The unresolved branch must always emit a structured envelope, never
    crash and never emit empty stdout (Pattern-1 Variant C)."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus, monkeypatch):
        """No exception / non-empty stdout on the empty-corpus unresolved path."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["hover", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        # cmd_hover's unresolved branch is W1272-canonical: exit 0 with a
        # structured envelope on stdout. No SystemExit, no crash.
        assert result.exit_code == 0, (
            f"hover unresolved branch must exit 0 per W1272 / W1277 contract; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on unresolved path"


class TestEmptyCorpusEnvelopeShape:
    """The unresolved branch already honours W1272 / W1277 -- positive
    regression tests so a future cleanup doesn't strip the disclosure."""

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus, monkeypatch):
        """Envelope carries a non-empty verdict per LAW 6."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["hover", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "hover")
        assert "summary" in data, f"envelope missing summary: {data}"
        assert "verdict" in data["summary"], f"summary missing verdict: {data['summary']}"
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict.strip()
        # LAW 6: verdict works standalone -- names the target.
        assert "definitely_not_a_symbol_xyz" in verdict, f"LAW 6 / LAW 4: verdict must name the target; got {verdict!r}"

    def test_empty_corpus_state_explicit(self, cli_runner, empty_corpus, monkeypatch):
        """W1272 regression: unresolved branch sets ``state='not_found'``."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["hover", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "hover")
        summary = data["summary"]
        assert summary.get("state") == "not_found", (
            f"W805-JJ regression: cmd_hover unresolved branch must keep "
            f"state='not_found' (W1272 contract); got {summary.get('state')!r}"
        )

    def test_empty_corpus_partial_success_set(self, cli_runner, empty_corpus, monkeypatch):
        """W1272 regression: unresolved branch sets ``partial_success=True``."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["hover", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "hover")
        summary = data["summary"]
        assert summary.get("partial_success") is True, (
            f"W805-JJ regression: cmd_hover unresolved branch must keep "
            f"partial_success=True (W1272 contract); got {summary.get('partial_success')!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus, monkeypatch):
        """LAW 6: verdict works without any other field."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["hover", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "hover")
        verdict = data["summary"]["verdict"]
        # Verdict standalone names target + states failure.
        assert "definitely_not_a_symbol_xyz" in verdict
        assert "not found" in verdict.lower()

    def test_not_found_resolution_disclosed(self, cli_runner, empty_corpus, monkeypatch):
        """W1272 / W1277 regression: unresolved branch stamps ``resolution='unresolved'``."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["hover", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "hover")
        summary = data["summary"]
        # Either summary-level or top-level disclosure satisfies W1272.
        resolution = summary.get("resolution") or data.get("resolution")
        assert resolution == "unresolved", (
            f"W805-JJ regression: cmd_hover unresolved branch must stamp resolution='unresolved'; got {resolution!r}"
        )

    def test_not_found_partial_success_set(self, cli_runner, empty_corpus, monkeypatch):
        """W1272 regression: dual-key for partial_success (top-level + summary)."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["hover", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "hover")
        # Both top-level and summary-level should agree.
        top_ps = data.get("partial_success")
        sum_ps = data["summary"].get("partial_success")
        assert top_ps is True and sum_ps is True, (
            f"W805-JJ regression: cmd_hover must set partial_success=True "
            f"at both top-level and summary scope; "
            f"got top={top_ps!r}, summary={sum_ps!r}"
        )


# ---------------------------------------------------------------------------
# W805-EE-axis fabrication probe -- REAL BUG pinned strict.
# ---------------------------------------------------------------------------


class TestStaleMetricsFabrication:
    """W805-EE fabrication-axis: when a symbol resolves but its
    graph_metrics row is absent, cmd_hover's ``metrics if metrics else 0``
    fallback silently zero-fills the metric and emits a confident verdict
    indistinguishable from a legitimate zero-edge leaf.

    This is Pattern-1 Variant D (silent success on degraded resolution)
    crossed with the W805-EE fabrication-axis (auto-zero on empty
    fabricates a real-looking metric). The fix template per CLAUDE.md
    Pattern-1 Variant D: disclose the resolution state via a field on
    the envelope + ``partial_success=true`` + a distinct verdict
    reflecting the degradation.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-JJ REAL BUG: src/roam/commands/cmd_hover.py:162-168 "
            "(``metrics is None`` fallback) silently zero-fills "
            "in_d / out_d / pr when a symbol resolves but its "
            "graph_metrics row is absent. The envelope carries NO "
            "disclosure -- no metrics_available, no metrics_state, no "
            "partial_success=True. An agent reading the structured "
            "fields concludes 'low-risk leaf with no callers' when the "
            "truth is 'this metric is unavailable'. Pattern-1 Variant D "
            "(silent success on degraded resolution) crossed with the "
            "W805-EE fabrication-axis (auto-zero on empty fabricates a "
            "real-looking metric). Pinned strict so a fix that adds "
            "metrics_available=False / state='metrics_unavailable' / "
            "partial_success=True / metrics_state='missing' graduates "
            "this to PASS."
        ),
    )
    def test_no_fabricated_metrics_on_empty(self, cli_runner, stale_metrics_corpus, monkeypatch):
        """W805-JJ REAL BUG sentinel: stale-metrics envelope is byte-
        indistinguishable from legitimate-zero envelope.

        Today, with graph_metrics deleted, cmd_hover emits the SAME
        ``in_degree=0, out_degree=0, pagerank=0.0, blast_bucket='none',
        partial_success=false`` shape as a leaf with real zero edges.
        An agent reading these fields concludes "low-risk leaf with no
        callers" when the truth is "this metric is unavailable".
        """
        monkeypatch.chdir(stale_metrics_corpus)
        result = invoke_cli(
            cli_runner,
            ["hover", "leaf_fn"],
            cwd=stale_metrics_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "hover")
        summary = data["summary"]
        # Confirm we hit the metrics-missing branch (zero values).
        assert summary.get("in_degree") == 0
        assert summary.get("out_degree") == 0
        assert summary.get("pagerank") == 0.0
        # ASSERT THE FIX HAS BEEN APPLIED: stale-metrics envelope MUST
        # carry a disclosure distinguishing it from a legitimate-zero
        # envelope. Acceptable shapes (any one suffices):
        #   summary.metrics_available is False
        #   summary.state == "metrics_unavailable" / "stale_metrics"
        #   summary.partial_success is True (signals degradation)
        #   summary.metrics_state in ("missing", "unavailable", "stale")
        has_disclosure = (
            summary.get("metrics_available") is False
            or summary.get("state") in ("metrics_unavailable", "stale_metrics", "metrics_missing")
            or summary.get("partial_success") is True
            or summary.get("metrics_state") in ("missing", "unavailable", "stale")
        )
        assert has_disclosure, (
            f"W805-JJ REAL BUG: cmd_hover.py:166-168 silently zero-fills "
            f"in_d/out_d/pr when graph_metrics row is absent, producing "
            f"a verdict indistinguishable from a legitimate zero-edge leaf. "
            f"Pattern-1 Variant D + W805-EE fabrication-axis cross. "
            f"Expected one of: metrics_available=False / "
            f"state in {{metrics_unavailable, stale_metrics, metrics_missing}} / "
            f"partial_success=True / metrics_state in {{missing, unavailable, stale}}. "
            f"Got summary={summary!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-JJ REAL BUG: src/roam/commands/cmd_hover.py:162-168 "
            "(``metrics is None`` fallback) silently zero-fills in_d / "
            "out_d / pr when a symbol resolves but its graph_metrics row "
            "is absent. The resulting envelope is byte-indistinguishable "
            "from a legitimate zero-edge leaf -- same blast_bucket='none', "
            "same partial_success=false, same verdict pattern. This is "
            "Pattern-1 Variant D (silent success on degraded resolution) "
            "crossed with the W805-EE fabrication-axis (auto-zero on "
            "empty fabricates a real-looking metric). Pinned strict so a "
            "future fix that adds metrics_available=False / "
            "state='metrics_unavailable' / partial_success=True / "
            "metrics_state='missing' graduates this to PASS."
        ),
    )
    def test_stale_metrics_envelope_distinguishable_from_legit_zero(
        self, cli_runner, stale_metrics_corpus, leaf_corpus, monkeypatch
    ):
        """Pin the bug: stale-metrics envelope MUST differ on at least one
        machine-readable field from legitimate-zero envelope."""
        # Run on stale_metrics_corpus.
        monkeypatch.chdir(stale_metrics_corpus)
        stale_result = invoke_cli(
            cli_runner,
            ["hover", "leaf_fn"],
            cwd=stale_metrics_corpus,
            json_mode=True,
        )
        stale_summary = parse_json_output(stale_result, "hover")["summary"]

        # Run on leaf_corpus (legitimate zero).
        monkeypatch.chdir(leaf_corpus)
        legit_result = invoke_cli(
            cli_runner,
            ["hover", "leaf_fn"],
            cwd=leaf_corpus,
            json_mode=True,
        )
        legit_summary = parse_json_output(legit_result, "hover")["summary"]

        # Restrict to discriminating fields (drop nondeterministic ones).
        keep_keys = (
            "blast_bucket",
            "in_degree",
            "out_degree",
            "pagerank",
            "partial_success",
            "state",
            "metrics_available",
            "metrics_state",
        )
        stale_keep = {k: stale_summary.get(k) for k in keep_keys}
        legit_keep = {k: legit_summary.get(k) for k in keep_keys}

        assert stale_keep != legit_keep, (
            f"W805-JJ Pattern-1-V-D + W805-EE-axis cross: stale-metrics "
            f"envelope is byte-indistinguishable from legitimate-zero on "
            f"the discriminating fields. An agent cannot tell "
            f"'metric unavailable' from 'metric legitimately zero'. "
            f"stale={stale_keep!r}, legit={legit_keep!r}"
        )


# ---------------------------------------------------------------------------
# Clean-corpus regression -- success branch still emits real data.
# ---------------------------------------------------------------------------


class TestCleanCorpusFullSuccess:
    """Sanity: a real caller edge produces a real blast-radius envelope."""

    def test_clean_corpus_emits_real_hover(self, cli_runner, clean_corpus, monkeypatch):
        """callee_fn has 1 caller -> envelope reports in_degree >= 1."""
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(
            cli_runner,
            ["hover", "callee_fn"],
            cwd=clean_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "hover")
        summary = data["summary"]
        # Success branch should fire with real metrics.
        assert summary.get("in_degree", 0) >= 1, (
            f"clean corpus should produce in_degree >= 1; got {summary.get('in_degree')}"
        )
        assert summary.get("blast_bucket") in ("small", "moderate", "large"), (
            f"non-zero in_degree should yield non-'none' blast_bucket; got {summary.get('blast_bucket')!r}"
        )
        # Resolution disclosure: clean exact match.
        assert summary.get("resolution") == "symbol", (
            f"exact-name match should disclose resolution='symbol'; got {summary.get('resolution')!r}"
        )
        # Verdict mentions blast radius (LAW 4 / LAW 6 anchor).
        verdict = summary.get("verdict", "")
        assert "blast radius" in verdict.lower(), f"verdict must anchor on 'blast radius'; got {verdict!r}"

    def test_clean_corpus_top_caller_populated(self, cli_runner, clean_corpus, monkeypatch):
        """callee_fn's top_caller should be caller_fn (real edge)."""
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(
            cli_runner,
            ["hover", "callee_fn"],
            cwd=clean_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "hover")
        top_caller = data.get("top_caller")
        assert top_caller is not None, (
            f"callee_fn has 1 real caller (caller_fn); top_caller should not be None. Got data={data!r}"
        )
        assert "caller_fn" in (top_caller.get("name") or ""), f"top_caller.name should be caller_fn; got {top_caller!r}"
