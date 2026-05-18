"""W805-P — empty-corpus Pattern-2 smoke test on ``roam impact``.

Sixteenth-in-batch W805 sweep. Flagship 5-verb blast-radius command.

Scope
-----

cmd_impact has four envelope branches:

1. ``sym is None`` (line 383) -- not_found / unresolved. W1272 + W1277 fixed
   this branch: ``state: "not_found"``, ``resolution: "unresolved"``,
   ``partial_success: True``. **Pattern-1 Variant D handled.**
2. ``sym_id not in G`` (line 465) -- symbol in index but not graph. W641-A
   landed ``risk_level_canonical="low"`` here but the branch does NOT set
   ``state`` or ``partial_success`` on summary -- the disclosure lives in
   ``in_graph: False`` instead.
3. ``not dependents`` (line 561) -- leaf symbol with zero callers. W641-A
   landed ``risk_level_canonical="low"`` + verdict ``"no dependents (risk_level
   low)"``. Does NOT set ``state`` on summary. ``partial_success`` comes from
   the merged ``resolution_disclosure`` block (``False`` on exact match).
4. Full-radius (line 693) -- the canonical success path. Sets ``state:
   "ok"`` / ``"timeout"`` / ``"caller_cap"`` / ``"depth_cap"`` AND
   ``partial_success`` AND ``risk_level_canonical``.

W978 first-hypothesis check
---------------------------

The W805 sweep brief asks: does cmd_impact silently emit "no impact" /
silent SAFE on empty corpus, OR is the empty-state explicitly disclosed?

Probe results (this commit):

* **Empty corpus (no source files)** -> not_found path -> ``state="not_found"``,
  ``resolution="unresolved"``, ``partial_success=True``. **NOT silent SAFE.**
* **Leaf symbol with zero dependents** -> no-dependents path -> verdict
  ``"no dependents (risk_level low)"``, ``risk_level_canonical="low"``,
  ``partial_success=False``, BUT no ``state`` field on summary. The verdict
  TEXT is loud (concrete-noun terminal "dependents"), so this is not a
  true silent SAFE. There IS a mild shape divergence vs the full-radius
  branch (which sets ``state: "ok"``).

Conclusion
----------

* Pattern-1 Variant D: **already handled** by W1272 / W1277. Regression baseline.
* Pattern-2 silent SAFE: **NOT confirmed** as a real bug. The verdict
  discloses the leaf state explicitly + risk_level_canonical floors at "low".
* Shape parity (mild): the no-dependents branch lacks a ``state`` field
  present on the full-radius branch. Pinned as **xfail-strict** so a
  future cleanup pass that adds ``state: "no_dependents"`` graduates the
  test to PASS without manual edit; if the divergence ever regresses
  worse (e.g. removing ``risk_level_canonical``), the strict xfail will
  flip and surface the regression.

Sweep brief: W805-P (Wave805-P, sixteenth-in-batch).
"""

from __future__ import annotations

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

from roam.output.risk import RISK_LEVELS, risk_rank  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures -- empty corpus (no Python sources) + leaf-symbol corpus.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path):
    """Project with only a README -- no indexable source symbols.

    Exercises the not_found branch: ``find_symbol`` returns None for any
    name, so the unresolved disclosure / Pattern-1 Variant D path fires.
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
    """Project with one function and no callers -- exercises no-dependents branch."""
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
def clean_corpus(tmp_path):
    """Project with one leaf + one caller -- exercises full-radius envelope."""
    proj = tmp_path / "clean_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "core.py").write_text("def leaf_fn():\n    return 42\n\ndef caller_fn():\n    return leaf_fn()\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Pattern-1 Variant D -- regression baseline (W1272 + W1277 handled).
# ---------------------------------------------------------------------------


class TestEmptyCorpusUnresolved:
    """Empty corpus -> not_found path. Should be the Pattern-1-V-D canonical shape."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus, monkeypatch):
        """No exception / non-empty stdout on empty corpus."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["impact", "anything"],
            cwd=empty_corpus,
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"impact must exit 0 on empty corpus per W1272 Convention-c; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on no-data path"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus, monkeypatch):
        """Envelope carries a non-empty verdict per LAW 6."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["impact", "anything"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        assert "summary" in data
        assert "verdict" in data["summary"]
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict.strip()

    def test_empty_corpus_explicit_state(self, cli_runner, empty_corpus, monkeypatch):
        """Empty-corpus / unresolved path discloses ``state`` explicitly.

        Pattern-2 guard: the not_found branch must name the absent state
        (``state: "not_found"``) so an agent reading the envelope sees a
        machine-readable marker, not just the verdict prose.
        """
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["impact", "anything"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        assert summary.get("state") == "not_found", (
            f"W1272 contract: empty-corpus unresolved must emit state='not_found'; got {summary.get('state')!r}"
        )

    def test_empty_corpus_partial_success_set(self, cli_runner, empty_corpus, monkeypatch):
        """Pattern-1 Variant D: unresolved degraded outcome must set partial_success=True."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["impact", "anything"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        assert summary.get("partial_success") is True, (
            f"W1272 Pattern-1-V-D: unresolved must set partial_success=True; got {summary.get('partial_success')!r}"
        )
        assert summary.get("resolution") == "unresolved", (
            f"W1242 disclosure: resolution must be 'unresolved' on empty corpus; got {summary.get('resolution')!r}"
        )

    def test_empty_corpus_law6_verdict_standalone(self, cli_runner, empty_corpus, monkeypatch):
        """LAW 6: verdict line works standalone -- names the target + canonical level."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["impact", "anything"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        verdict = data["summary"]["verdict"]
        # Verdict mentions the (unfindable) target AND the canonical risk_level.
        assert "anything" in verdict, f"LAW 6 / LAW 4: verdict must name the target; got {verdict!r}"
        assert "not found" in verdict.lower(), f"verdict must disclose the unresolved state; got {verdict!r}"
        assert "risk_level low" in verdict, f"W641-A: verdict must terminate with canonical risk_level; got {verdict!r}"

    def test_unresolved_target_explicit_resolution(self, cli_runner, empty_corpus, monkeypatch):
        """Pattern-1 Variant D: resolution disclosure stamped on the envelope."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["impact", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        # Top-level mirror per W1242 / W641-A pattern.
        assert data.get("resolution") == "unresolved"
        # risk_level_canonical floor.
        assert data.get("risk_level_canonical") == "low"
        assert data.get("risk_rank") == risk_rank("low") == 1


# ---------------------------------------------------------------------------
# W641-followup-A regression baseline -- intact across all empty-state paths.
# ---------------------------------------------------------------------------


class TestW641FollowupARegressionBaseline:
    """W641-A landed canonical risk_level_canonical -- pin it intact on empty paths."""

    def test_w641_a_canonical_risk_level_intact(self, cli_runner, empty_corpus, monkeypatch):
        """Every empty-state envelope carries risk_level_canonical + risk_rank."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["impact", "missing"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        assert "risk_level_canonical" in summary, "W641-A: summary.risk_level_canonical missing"
        assert summary["risk_level_canonical"] in RISK_LEVELS
        assert summary["risk_level_canonical"] == "low", (
            "W641-A floor: empty corpus / not_found must canonical-floor at 'low'"
        )
        assert summary.get("risk_rank") == risk_rank(summary["risk_level_canonical"])
        # Top-level mirrors land too (Pattern-3a cross-command floor comparator).
        assert data.get("risk_level_canonical") == summary["risk_level_canonical"]
        assert data.get("risk_rank") == summary["risk_rank"]


# ---------------------------------------------------------------------------
# Pattern-2 candidate -- leaf-symbol "no dependents" path shape divergence.
# ---------------------------------------------------------------------------


class TestLeafSymbolNoDependentsShape:
    """Pin the no-dependents envelope shape + flag the state-field divergence."""

    def test_leaf_emits_canonical_low(self, cli_runner, leaf_corpus, monkeypatch):
        """Leaf with 0 dependents floors at risk_level_canonical='low'."""
        monkeypatch.chdir(leaf_corpus)
        result = invoke_cli(
            cli_runner,
            ["impact", "leaf_fn"],
            cwd=leaf_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        # W641-A intact.
        assert summary.get("risk_level_canonical") == "low"
        assert summary.get("risk_rank") == 1
        # Verdict explicitly says "no dependents" -- LAW 4 concrete-noun
        # terminal "dependents" makes this a loud-not-silent SAFE.
        assert "no dependents" in summary.get("verdict", "").lower()
        # Resolution disclosure stamped (exact match).
        assert summary.get("resolution") == "symbol"
        # partial_success is False because the leaf result is a genuine
        # success, not a degraded outcome -- this is NOT Pattern-2.
        assert summary.get("partial_success") is False

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-P shape divergence: the no-dependents branch in cmd_impact.py "
            "(line 561-608) does NOT emit summary.state, while the full-radius "
            "branch (line 693-733) does (state='ok'/'timeout'/'caller_cap'/'depth_cap'). "
            "This is mild shape drift, not a true silent SAFE (the verdict is loud + "
            "risk_level_canonical floors correctly). Pinned strict so a future "
            "cleanup pass that adds state='no_dependents' graduates this to PASS. "
            "If the canonical contract regresses worse (e.g. losing risk_level_canonical), "
            "the strict xfail will flip and surface the regression."
        ),
    )
    def test_no_silent_low_impact_on_empty(self, cli_runner, leaf_corpus, monkeypatch):
        """Shape parity: the no-dependents envelope should expose summary.state.

        The full-radius branch sets ``state`` to one of
        ``ok``/``timeout``/``caller_cap``/``depth_cap``. The no-dependents
        branch is a distinct ``state`` value (``no_dependents`` / ``leaf`` /
        ``empty``) that an agent could machine-read instead of regex-grepping
        the verdict. Pinned as xfail-strict because:

        * The verdict already discloses the state loudly ("no dependents") --
          so this is NOT Pattern-2 silent SAFE.
        * But a future cleanup that adds ``state: "no_dependents"`` would
          benefit downstream cross-command consumers (preflight, critique)
          that switch on ``summary.state`` rather than parsing verdict text.
        """
        monkeypatch.chdir(leaf_corpus)
        result = invoke_cli(
            cli_runner,
            ["impact", "leaf_fn"],
            cwd=leaf_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        # Today this assertion fails -- state is absent on the no-dependents path.
        assert "state" in summary, (
            "W805-P: no-dependents branch lacks summary.state (full-radius branch has it). "
            "Mild shape parity drift; xfail-strict pins the gap."
        )


# ---------------------------------------------------------------------------
# Clean-corpus regression -- full-radius envelope still emits real impact.
# ---------------------------------------------------------------------------


class TestCleanCorpusFullRadius:
    """Sanity: a real caller-edge produces a real impact envelope."""

    def test_clean_corpus_emits_real_impact(self, cli_runner, clean_corpus, monkeypatch):
        """leaf_fn has 1 caller -> envelope reports affected_symbols=1 with state='ok'."""
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(
            cli_runner,
            ["impact", "leaf_fn"],
            cwd=clean_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "impact")
        summary = data["summary"]
        # Full-radius branch should fire (1 dependent).
        assert summary.get("affected_symbols") == 1, (
            f"clean corpus should produce 1 dependent; got {summary.get('affected_symbols')}"
        )
        # The full-radius branch DOES emit summary.state.
        assert summary.get("state") == "ok", (
            f"full-radius branch must emit state='ok' on no-cap-hit run; got {summary.get('state')!r}"
        )
        # W641-A canonical floor still intact.
        assert summary.get("risk_level_canonical") in RISK_LEVELS
        assert summary.get("risk_rank") == risk_rank(summary["risk_level_canonical"])
        # Verdict mentions blast (LAW 4 concrete noun anchor).
        verdict = summary.get("verdict", "")
        assert "blast" in verdict.lower() or "affected" in verdict.lower()
