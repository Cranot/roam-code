"""W805-T -- empty-corpus Pattern-2 smoke test on ``roam uses``.

Twentieth-in-batch W805 sweep. Direct-callers-only sibling of cmd_impact
(see W805-P for the transitive-blast peer). cmd_uses is structurally
simpler than cmd_impact and has had LESS canonical hardening: W641-A
canonical risk_level + W1272 state/resolution disclosure never reached
this command.

Scope
-----

cmd_uses has three envelope-emitting branches (src/roam/commands/cmd_uses.py):

1. ``not targets`` (line 124) -- symbol not found after exact match + LIKE
   fallback. JSON mode emits an envelope with verdict + ``error:
   "symbol_not_found"`` then raises ``SystemExit(1)``. Does NOT set
   ``state``, does NOT set ``resolution``, does NOT set
   ``partial_success: True``, does NOT set ``risk_level_canonical``.
   **Pattern-1 Variant D candidate.**

2. ``not rows`` (line 193) -- target resolved but zero consumer edges.
   JSON mode emits ``verdict: "no consumers of '<name>' found"``,
   ``total_consumers: 0``, ``partial_success: false`` (implicit
   via envelope default), no ``state``, no ``resolution``,
   no ``risk_level_canonical``. **Mild Pattern-2 candidate** -- verdict
   is loud ("no consumers" is concrete-noun-anchored on the LAW 4 list)
   but no machine-readable state field for downstream switching.

3. Full success (line 251) -- consumers grouped by edge kind; emits
   real production/test consumer counts. Same fields-present shape as
   #2 minus ``state`` / ``resolution`` (also absent here).

W978 first-hypothesis check
---------------------------

First hypothesis from the W805-P sibling write-up: cmd_uses is the
less-hardened twin, so empty-corpus (not_found) likely emits silent
SAFE -- no ``state``, no ``resolution``, ``partial_success: false``.

Probe result on the live tree (this commit, isolation run):

* ``--json uses nonexistent_xyz`` on an empty corpus exits 1 with
  ``summary.partial_success: false`` and no ``state`` / ``resolution`` /
  ``risk_level_canonical`` fields. The hint string is loud but the
  machine-readable envelope is **silent SAFE**: an agent switching on
  ``summary.partial_success`` would conclude "no degradation occurred"
  even though the target was unresolved.
* ``--json uses leaf_fn`` (resolved symbol, zero callers) exits 0 with
  a loud verdict (``no consumers of 'leaf_fn' found``) but again no
  ``state`` / ``resolution`` fields.

Conclusion
----------

* **REAL BUG pinned: Pattern-1 Variant D on the not_found branch**
  (src/roam/commands/cmd_uses.py:124-148). The empty-corpus envelope
  does not set ``state`` / ``resolution`` / ``partial_success`` so an
  agent reading only structured fields cannot tell unresolved apart
  from "resolved-with-zero-consumers". Pinned strict so a future
  cleanup that aligns cmd_uses with the cmd_impact W1272 / W1277
  contract graduates the test to PASS without manual edit.
* **Shape parity (mild)**: the no-consumers branch (line 193) also
  lacks ``state`` (e.g. ``state: "no_consumers"``). Loud verdict
  prevents this from being a true silent SAFE, but cross-command
  consumers that switch on ``summary.state`` benefit from parity.
  Pinned strict alongside the not_found case.

Both xfails will surface a regression that strips the verdict text
or the existing ``error: "symbol_not_found"`` shape because the
clean-corpus + resolution probes still assert positive shape.

Sweep brief: W805-T (Wave805-T, twentieth-in-batch).
"""

from __future__ import annotations

import json
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


def _parse_json_any_exit(result, command="uses"):
    """Parse JSON envelope from stdout regardless of exit_code.

    The not_found branch in cmd_uses emits a structured envelope on
    stdout and then raises ``SystemExit(1)``. The shared
    ``parse_json_output`` helper asserts ``exit_code == 0`` so it
    cannot be used here. This local helper mirrors that helper minus
    the exit-code assert.
    """
    raw = getattr(result, "stdout", None) or result.output
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON from {command} (exit {result.exit_code}): {e}\nOutput was:\n{raw[:500]}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def empty_corpus(tmp_path):
    """Project with only a README -- no indexable source symbols.

    Exercises the ``not targets`` branch (line 124): both exact-match
    and LIKE-fallback return empty, so the symbol_not_found path fires.
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
    """Project with one function and no callers.

    Exercises the ``not rows`` branch (line 193): target resolves
    cleanly but the edges table has zero source->target rows.
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
    """The not_found branch must always emit a structured envelope, never
    crash and never emit empty stdout (Pattern-1 Variant C)."""

    def test_empty_corpus_no_crash(self, cli_runner, empty_corpus, monkeypatch):
        """No exception / non-empty stdout on the empty-corpus not_found path."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["uses", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        # cmd_uses raises SystemExit(1) on not_found by design today --
        # the canonical contract is exit=1 + structured envelope on
        # stdout (Pattern-1 Variant B: structured signal preserved
        # across non-zero exit by the inprocess MCP bridge).
        assert result.exit_code == 1, (
            f"uses must exit 1 on symbol_not_found per current contract; got {result.exit_code}\n{result.output}"
        )
        out = getattr(result, "stdout", None) or result.output
        assert out.strip(), "Pattern-1 Variant C: empty stdout on not_found path"

    def test_empty_corpus_envelope_has_verdict(self, cli_runner, empty_corpus, monkeypatch):
        """Envelope carries a non-empty verdict per LAW 6."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["uses", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = _parse_json_any_exit(result)
        assert "summary" in data, f"envelope missing summary: {data}"
        assert "verdict" in data["summary"], f"summary missing verdict: {data['summary']}"
        verdict = data["summary"]["verdict"]
        assert isinstance(verdict, str) and verdict.strip()
        # LAW 6: verdict works standalone -- names the target.
        assert "definitely_not_a_symbol_xyz" in verdict, f"LAW 6 / LAW 4: verdict must name the target; got {verdict!r}"

    def test_empty_corpus_envelope_error_marker_intact(self, cli_runner, empty_corpus, monkeypatch):
        """W805-T regression baseline: the existing ``error: "symbol_not_found"``
        marker stays intact. If the strict-xfail Pattern-1-V-D fix later
        replaces ``error`` with ``state``, update both tests in lockstep."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["uses", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = _parse_json_any_exit(result)
        summary = data["summary"]
        # Today the not_found branch sets `error: "symbol_not_found"` instead
        # of a canonical `state` field. Pin the existing shape so an
        # accidental rename surfaces, while the xfail-strict test below
        # tracks the canonical-shape gap.
        assert summary.get("error") == "symbol_not_found", (
            f"existing error marker missing; got {summary.get('error')!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-1 Variant D + Pattern-2 -- REAL BUG pinned strict.
# ---------------------------------------------------------------------------


class TestEmptyCorpusUnresolved:
    """Pattern-1 Variant D guard: empty-corpus / not_found must disclose
    ``state``, ``resolution``, and ``partial_success: True`` so an agent
    switching on machine-readable fields can tell unresolved apart from
    resolved-with-zero-consumers."""

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-T REAL BUG: src/roam/commands/cmd_uses.py:124-148 (the "
            "``not targets`` branch) emits an envelope with no ``state`` "
            'field. cmd_impact\'s analogous branch sets ``state: "not_found"`` '
            "(W1272 / W1277). cmd_uses missed the W1272 canonical hardening. "
            "Pinned strict so a future cleanup that adds ``state: "
            '"not_found"`` graduates this to PASS; until then, agents '
            "reading ``summary.state`` get None on the unresolved path."
        ),
    )
    def test_empty_corpus_explicit_state(self, cli_runner, empty_corpus, monkeypatch):
        """Empty-corpus / unresolved path discloses ``state`` explicitly."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["uses", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = _parse_json_any_exit(result)
        summary = data["summary"]
        assert summary.get("state") == "not_found", (
            f"W805-T Pattern-1-V-D: empty-corpus unresolved must emit "
            f"state='not_found' (matches cmd_impact W1272 contract); "
            f"got {summary.get('state')!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-T REAL BUG: src/roam/commands/cmd_uses.py:124-148 sets "
            "``partial_success: false`` on the not_found path. The W1272 / "
            "W1277 contract (cmd_impact) sets ``partial_success: True`` on "
            "any degraded outcome including unresolved targets. cmd_uses "
            "currently silent-SAFEs: an agent reading partial_success would "
            "conclude no degradation occurred. Pinned strict so the fix "
            "graduates to PASS; until then, this is the canonical Pattern-2 "
            "silent SAFE on the cmd_uses not_found branch."
        ),
    )
    def test_empty_corpus_partial_success_set(self, cli_runner, empty_corpus, monkeypatch):
        """Pattern-2 guard: unresolved degraded outcome sets partial_success=True."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["uses", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = _parse_json_any_exit(result)
        summary = data["summary"]
        assert summary.get("partial_success") is True, (
            f"W805-T Pattern-2: not_found degraded outcome must set "
            f"partial_success=True; got {summary.get('partial_success')!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-T REAL BUG: src/roam/commands/cmd_uses.py:124-148 does NOT "
            "stamp the ``resolution`` field on the not_found envelope. The "
            "W1242 resolution-disclosure pattern (used by cmd_impact and "
            "the symbol-resolver-shared commands) calls for "
            '``resolution: "unresolved"`` on unresolved paths. Pinned '
            "strict so a future cleanup graduates to PASS."
        ),
    )
    def test_unresolved_target_explicit_resolution(self, cli_runner, empty_corpus, monkeypatch):
        """Pattern-1 Variant D: ``resolution`` disclosure stamped on the envelope."""
        monkeypatch.chdir(empty_corpus)
        result = invoke_cli(
            cli_runner,
            ["uses", "definitely_not_a_symbol_xyz"],
            cwd=empty_corpus,
            json_mode=True,
        )
        data = _parse_json_any_exit(result)
        summary = data["summary"]
        # Either summary-level or top-level disclosure satisfies W1242.
        resolution = summary.get("resolution") or data.get("resolution")
        assert resolution == "unresolved", (
            f"W805-T Pattern-1-V-D: not_found must disclose resolution='unresolved'; got {resolution!r}"
        )


# ---------------------------------------------------------------------------
# Pattern-2 (mild) -- leaf path lacks ``state``. Loud verdict prevents true
# silent SAFE but shape parity gap pinned strict.
# ---------------------------------------------------------------------------


class TestLeafNoConsumersShape:
    """Pin the no-consumers envelope shape + flag the state-field divergence."""

    def test_leaf_emits_verdict_no_consumers(self, cli_runner, leaf_corpus, monkeypatch):
        """Leaf with 0 callers emits a loud verdict (LAW 4 anchor)."""
        monkeypatch.chdir(leaf_corpus)
        result = invoke_cli(
            cli_runner,
            ["uses", "leaf_fn"],
            cwd=leaf_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "uses")
        summary = data["summary"]
        # Loud verdict ("no consumers" anchors on a finding-noun terminal).
        assert "no consumers" in summary.get("verdict", "").lower()
        # Counts are zero -- ``not rows`` branch fired.
        assert summary.get("total_consumers") == 0
        # caller_metric_definition is stamped (Pattern-3a metric clarity).
        assert summary.get("caller_metric_definition") == "raw_edge_rows"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-T shape parity: src/roam/commands/cmd_uses.py:193-216 "
            "(the ``not rows`` branch) does not emit summary.state. The "
            "verdict is loud (``no consumers of '<name>' found``) so this "
            "is NOT a true silent SAFE; pinned strict because cross-command "
            "consumers (preflight, critique) prefer switching on a "
            "machine-readable state field rather than regex-grepping the "
            "verdict prose. Adding state='no_consumers' graduates this to "
            "PASS."
        ),
    )
    def test_no_silent_zero_uses_on_empty(self, cli_runner, leaf_corpus, monkeypatch):
        """Pattern-2 mild: the no-consumers envelope should expose summary.state."""
        monkeypatch.chdir(leaf_corpus)
        result = invoke_cli(
            cli_runner,
            ["uses", "leaf_fn"],
            cwd=leaf_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "uses")
        summary = data["summary"]
        # Today this assertion fails -- state is absent on the no-rows path.
        assert "state" in summary, (
            "W805-T shape parity: no-consumers branch lacks summary.state. "
            "Mild divergence vs canonical envelope shape; xfail-strict pins "
            "the gap until a future cleanup adds state='no_consumers'."
        )


# ---------------------------------------------------------------------------
# Clean-corpus regression -- success branch still emits real consumers.
# ---------------------------------------------------------------------------


class TestCleanCorpusFullSuccess:
    """Sanity: a real caller edge produces a real consumer envelope."""

    def test_clean_corpus_emits_real_uses(self, cli_runner, clean_corpus, monkeypatch):
        """callee_fn has 1 caller -> envelope reports 1 consumer with correct counts."""
        monkeypatch.chdir(clean_corpus)
        result = invoke_cli(
            cli_runner,
            ["uses", "callee_fn"],
            cwd=clean_corpus,
            json_mode=True,
        )
        data = parse_json_output(result, "uses")
        summary = data["summary"]
        # Success branch should fire.
        assert summary.get("total_consumers") >= 1, (
            f"clean corpus should produce >= 1 consumer; got {summary.get('total_consumers')}"
        )
        assert summary.get("production_consumers") >= 1, (
            f"caller_fn lives in src/ (non-test) so production_consumers >= 1; "
            f"got {summary.get('production_consumers')}"
        )
        # caller_metric_definition stamped on success branch too.
        assert summary.get("caller_metric_definition") == "raw_edge_rows"
        # Verdict mentions consumers (LAW 4 anchor).
        verdict = summary.get("verdict", "")
        assert "consumer" in verdict.lower(), f"verdict must anchor on 'consumer(s)'; got {verdict!r}"
        # consumers dict has at least one edge-kind group with non-empty list.
        consumers = data.get("consumers", {})
        flattened = [entry for group in consumers.values() for entry in group]
        assert len(flattened) >= 1, f"consumers dict should have >= 1 entry total; got {consumers}"
