"""W805-QQQQ — cmd_sbom cross-ecosystem reachability disclosure (W805 sweep).

Ninety-fifth-in-batch W805 sweep. Completes the **security-gate disclosure
family 4-strong**:

    * W826                — cmd_taint empty-corpus silent SAFE.
    * W805-KKKK           — cmd_taint cross-language source/sink filter.
    * W805-NNNN           — cmd_vuln_reach tri-valued sentinel flattened.
    * W805-QQQQ (this)    — cmd_sbom cross-ecosystem reachability collapse.

The user task mentions ``cmd_supply_chain.py`` but the actual cross-ecosystem
reachability surface lives in ``cmd_sbom.py``. ``cmd_supply_chain`` is a pure
pin-coverage dashboard — it already surfaces an ``ecosystems: {python: N,
javascript: M, java: K}`` map per ecosystem, with no reachability claim, so
its cross-ecosystem behaviour is by-construction disclosed. The W805-QQQQ
axis is the **graph-reachability projection** that ``cmd_sbom`` emits, which
collapses three structurally-distinct states into a single boolean.

Hypothesis (CONFIRMED via probe).
---------------------------------
``cmd_sbom._compute_reachability`` (cmd_sbom.py:116-157) initialises every
declared dep to ``{"reachable": False, ...}``. The matcher walks the
indexed symbol graph and tags ``reachable: True`` only when a dep name
fuzzy-matches a graph node's ``qualified_name`` / ``name`` / ``file_path``.

``roam.security.sbom_reachability.merge_reachability``
(``sbom_reachability.py:761-795``) then merges the graph-based result
with filesystem heuristics via a **boolean OR**::

    entry = {
        "reachable": graph_reachable or fs_reachable,
        ...
    }

There is NO disclosure of WHY a dep is ``reachable: false``. The three
structurally-distinct branches collapse:

    (a) ecosystem-unsupported: the dep's ecosystem has no extractor /
        bridge installed (e.g. an npm ``lodash`` or maven
        ``org.apache:commons`` entry on a Python-only indexer). The
        reachability question CANNOT be answered without a JS / Java
        extractor.

    (b) symbol-name mismatch: the ecosystem IS supported and indexed,
        but the package name doesn't fuzzy-match any indexed symbol
        (e.g. ``click`` import resolved as ``click.echo`` reference but
        the matcher's normalisation didn't bridge to it).

    (c) genuinely unreachable: the package IS indexed and matched, but
        no entry point can reach the matched symbol.

The CycloneDX / SPDX SBOM document emits a per-component
``roam:reachable: "true"|"false"`` property + the envelope summary
emits ``reachable_count`` / ``phantom_count`` integer counts — both
boolean reductions of the tri-valued state above. Downstream consumers
(GitHub Dependency Review, FOSSA, Dependency-Track, CI gates that read
the envelope verdict) cannot distinguish "this is a real phantom dep
worth pruning" (case c) from "the indexer literally cannot answer the
question for this ecosystem" (case a).

Probe transcript.
-----------------
Polyglot fixture (``package.json`` declaring ``lodash``, ``pom.xml``
declaring ``org.apache:commons``, ``requirements.txt`` declaring
``click``) against a pure-Python indexer (no JS / Java extractor wired)
plus ``app.py`` that actually ``import click``-s::

    VERDICT: 0 reachable (0 direct, 0 heuristic), 3 phantom
    reachable_count: 0
    phantom_count: 3
    partial_success: False
    state: None
    python   -> [('click',           'false')]
    javascript -> [('lodash',         'false')]
    java     -> [('org.apache:commons','false')]

All 3 deps emit ``roam:reachable=false``. The Python ``click`` dep is
structurally case (b) — name-mismatch. The npm ``lodash`` dep is case
(a) — no JS extractor. The maven dep is case (a) — no Java extractor.
The envelope cannot tell them apart.

Distinct from the sister W805 pins.
-----------------------------------
* **W826** (cmd_taint empty corpus) gates ``state == "empty_corpus"``
  when ``symbol_count == 0``. The W805-QQQQ fixture has a populated
  Python corpus, so W826 is bypassed.
* **W805-KKKK** (cmd_taint cross-language) covers the
  ``taint_engine._symbols_matching`` ``f.language IN (rule.languages)``
  filter. cmd_sbom has no such language filter — the collapse is at the
  symbol-name matcher in ``_compute_reachability``, NOT a language gate.
  Different file (``cmd_sbom.py``), different mechanism (no language
  filter at all, just structural inability to resolve cross-ecosystem
  package names).
* **W805-NNNN** (cmd_vuln_reach tri-valued sentinel) collapses three
  reachable states (``0`` / ``1`` / ``-1``) to a Python ``bool``. cmd_sbom
  never had a tri-valued sentinel — it was ``bool`` end-to-end. The
  collapse is upstream of the sentinel: there's no "unmatched" /
  "ecosystem-unsupported" state to flatten in the first place. Distinct
  producer-side gap, same family.

Security severity.
------------------
HIGH. The SBOM is the artifact downstream supply-chain tools
(Dependency-Track, FOSSA, GitHub Dependency Review, CI gates) consume
to ask "which declared deps are exercised at runtime?" A silent
``roam:reachable: false`` on every cross-ecosystem dep means agents /
CI consumers green-light a phantom-dependency claim for ANY ecosystem
the local indexer can't resolve. This is exactly the silent-SAFE shape
the security-gate disclosure family pins.

W978 first-hypothesis discipline.
---------------------------------
Verified BEFORE pinning:

  * The W826 ``state == "empty_corpus"`` branch does NOT fire — the
    fixture has indexed Python symbols.
  * The W805-KKKK language-filter collapse is in ``taint_engine``, NOT
    in ``cmd_sbom`` / ``sbom_reachability`` — different file, different
    mechanism (no language filter exists here).
  * The W805-NNNN tri-valued sentinel collapse is in ``vuln_reach``, NOT
    in ``cmd_sbom`` — cmd_sbom never had a tri-valued reachable state.
  * Probed both single-ecosystem (Python deps + Python source) and
    polyglot (Python + npm + maven deps + Python source) fixtures: BOTH
    report all deps as ``reachable: false``. The cross-ecosystem
    fixture's failure mode is structurally identical to the single-eco
    name-mismatch case in the envelope shape, which is exactly the bug
    — the envelope CANNOT distinguish them.

W907 verify-cycle.
------------------
No defensive "duplicated here to avoid cycle" claims in ``cmd_sbom.py``
or ``sbom_reachability.py``. The ``from roam.graph.builder import
build_symbol_graph`` is inside ``_compute_reachability``'s function
body — a legitimate lazy-import for networkx-import-cost reasons,
NOT a false cycle hedge. Clean.

Pinning style: xfail(strict=True).
----------------------------------
HIGH-severity class given the security-gate context. xfail-strict so
the moment ``cmd_sbom`` grows ANY disclosure signal (an
``ecosystems_unsupported`` field on the summary, a tri-valued
``roam:reachable`` per-component property, a ``state`` /
``resolution`` envelope field, OR a ``partial_success: True`` flag when
one or more declared deps' ecosystem cannot be answered), the xfail
flips to XPASS and forces removal of the pin.

Sister-suite parity.
--------------------
``test_w805_nnnn_invariants_preserved`` re-runs the cmd_vuln_reach
cross-language probe and confirms its xfail-strict family still holds.
``test_w805_kkkk_invariants_preserved`` re-runs the cmd_taint cross-
language probe. ``test_w826_invariants_preserved`` re-runs the
cmd_taint empty-corpus probe. All three sister probes are inlined here
so a regression in any sibling pin's axis would fail this suite too.
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_polyglot_sbom_fixture(tmp_path):
    """Polyglot manifest fixture against a pure-Python indexer.

    Carries three declared deps across three ecosystems (npm / pip /
    maven) but only the Python extractor is wired up by default. The
    indexer will populate the symbol graph from ``app.py`` only; the
    npm and maven entries CANNOT be resolved to indexed symbols because
    no JS or Java extractor is present.

    This is the realistic polyglot ingest shape: a monorepo declaring
    npm + pip + maven deps where roam's indexer covers only one
    language.
    """
    proj = tmp_path / "polyglot_proj"
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
    (proj / "pom.xml").write_text(
        "<project>"
        "<dependencies>"
        "<dependency>"
        "<groupId>org.apache</groupId>"
        "<artifactId>commons</artifactId>"
        "<version>1.0</version>"
        "</dependency>"
        "</dependencies>"
        "</project>",
        encoding="utf-8",
    )
    (proj / "requirements.txt").write_text("click==8.0.0\n", encoding="utf-8")
    (proj / "app.py").write_text(
        "import click\n\ndef main():\n    click.echo('hi')\n",
        encoding="utf-8",
    )
    git_init(proj)
    return proj


def _make_python_only_fixture(tmp_path):
    """Single-ecosystem (Python-only) fixture — control case.

    Used by ``test_axis_distinct_from_w805_nnnn`` to confirm the
    silent-phantom shape ALSO appears on a single-ecosystem repo, which
    proves the W805-QQQQ axis is "no disclosure of WHY reachable=false"
    rather than uniquely "cross-language". The envelope's inability to
    distinguish cases (a) / (b) / (c) is the structural gap.
    """
    proj = tmp_path / "py_only_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "requirements.txt").write_text("click==8.0.0\nrequests==2.31.0\n", encoding="utf-8")
    (proj / "app.py").write_text(
        "import click\nimport requests\n\ndef main():\n    click.echo(requests.get('http://x').text)\n",
        encoding="utf-8",
    )
    git_init(proj)
    return proj


def _make_empty_corpus(tmp_path):
    """Empty-corpus fixture — matches W826 shape so the sister-parity
    test can re-assert the empty-corpus invariant verbatim.
    """
    proj = tmp_path / "empty_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "empty.py").write_text("", encoding="utf-8")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_sbom_json(proj, *extra_args):
    """Run ``roam init`` then ``roam --json sbom <extra_args>`` against
    ``proj`` and return the parsed envelope + CliRunner result.
    """
    from roam.cli import cli

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        init_res = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert init_res.exit_code == 0, init_res.output
        sbom_res = runner.invoke(
            cli,
            ["--json", "sbom", *extra_args],
            catch_exceptions=False,
        )
        assert sbom_res.exit_code == 0, sbom_res.output
        return _json.loads(sbom_res.output), sbom_res
    finally:
        os.chdir(old_cwd)


def _components_by_ecosystem(envelope) -> dict[str, list[tuple[str, str]]]:
    """Return ``{ecosystem: [(component_name, roam:reachable_value), ...]}``."""
    out: dict[str, list[tuple[str, str]]] = {}
    components = envelope.get("sbom", {}).get("components", [])
    for c in components:
        props = {p["name"]: p["value"] for p in c.get("properties", [])}
        eco = props.get("roam:ecosystem", "?")
        reach = props.get("roam:reachable", "?")
        out.setdefault(eco, []).append((c["name"], reach))
    return out


# ---------------------------------------------------------------------------
# W978 prerequisite: W826 + W805-NNNN + W805-KKKK branches must NOT fire on
# the W805-QQQQ fixture. Pinned here so a future regression in any neighbour
# pin doesn't silently change the axis this test covers.
# ---------------------------------------------------------------------------


class TestW805QQQQAxisDistinct:
    def test_w826_empty_corpus_branch_does_not_fire(self, tmp_path):
        """The Python corpus is populated, so W826's
        ``state == 'empty_corpus'`` branch must not fire.
        """
        proj = _make_polyglot_sbom_fixture(tmp_path)
        data, _res = _run_sbom_json(proj)
        summary = data.get("summary") or {}
        assert summary.get("state") != "empty_corpus", (
            f"W826 empty-corpus branch is firing on a populated Python corpus: {summary!r}"
        )

    def test_axis_distinct_from_w805_nnnn_tri_valued(self, tmp_path):
        """W805-NNNN's collapse is a tri-valued sentinel (-1/0/1) -> bool.
        cmd_sbom never had a tri-valued sentinel — its collapse is the
        ABSENCE of a state-distinguishing field. Verify the envelope has
        no ``reachable: -1`` / ``reachable: "unmatched"`` shape (i.e. the
        W805-NNNN mechanism isn't present here), so the W805-QQQQ pin is
        targeting a distinct axis.
        """
        proj = _make_polyglot_sbom_fixture(tmp_path)
        data, _res = _run_sbom_json(proj)
        components = data.get("sbom", {}).get("components", [])
        for c in components:
            props = {p["name"]: p["value"] for p in c.get("properties", [])}
            # If the fix lands and a tri-valued reachable enum is
            # introduced, THIS axis-distinct test needs updating in the
            # same patch (and the xfail below flips to XPASS).
            reach = props.get("roam:reachable", "")
            assert reach in ("true", "false"), (
                f"W805-QQQQ axis: expected boolean reachable shape, got {reach!r}. "
                "If a tri-valued enum has been introduced, update this test."
            )

    def test_axis_distinct_from_w805_kkkk_lang_filter(self, tmp_path):
        """W805-KKKK's collapse is a language filter at
        ``taint_engine._symbols_matching``. cmd_sbom has no language
        filter at all — verify by checking the polyglot fixture's
        ``ecosystems`` summary shows 3 ecosystems (not 1), confirming
        the discovery layer is multi-ecosystem and the collapse is at
        the reachability layer, not a language-filter at discovery.
        """
        proj = _make_polyglot_sbom_fixture(tmp_path)
        data, _res = _run_sbom_json(proj)
        # supply-chain-style ecosystems map isn't on sbom envelope, but
        # we can derive it from components:
        by_eco = _components_by_ecosystem(data)
        ecosystems_present = sorted(by_eco.keys())
        assert ecosystems_present == ["java", "javascript", "python"], (
            f"W805-QQQQ axis: fixture must declare 3 ecosystems "
            f"(python+javascript+java) — got {ecosystems_present!r}. "
            "If discovery filters by language now, the W805-KKKK axis "
            "may have leaked here; update the test."
        )


# ---------------------------------------------------------------------------
# The W805-QQQQ pin — Pattern-1 variant D + Pattern-2 silent fallback on a
# security-gate command (cmd_sbom).
# ---------------------------------------------------------------------------


class TestCrossEcosystemSbomReachabilityDisclosure:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-QQQQ: HIGH-severity silent-SAFE on cross-ecosystem SBOM "
            "reachability. cmd_sbom._compute_reachability (cmd_sbom.py:134-135) "
            "initialises every dep to {'reachable': False, ...} and "
            "sbom_reachability.merge_reachability (sbom_reachability.py:788) "
            "merges via boolean OR — no disclosure of WHY reachable=false. "
            "Three structurally-distinct cases collapse: "
            "(a) ecosystem-unsupported (no JS/Java extractor), "
            "(b) symbol-name mismatch, "
            "(c) genuinely indexed-and-unreachable. The envelope summary "
            "carries no partial_success, no state, no ecosystems_unsupported, "
            "no unmatched_count. An npm lodash + maven commons + pip click "
            "polyglot fixture all emit roam:reachable=false against a Python "
            "indexer, indistinguishable from each other and from real "
            "phantom-dependency claims. Pattern-1 variant D + Pattern-2. "
            "Distinct from W826 (empty corpus), W805-KKKK (taint language "
            "filter at engine), W805-NNNN (vuln_reach tri-valued sentinel)."
        ),
    )
    def test_mixed_ecosystem_sbom_disclosure(self, tmp_path):
        """A polyglot SBOM against a single-language indexer produces an
        envelope that is structurally indistinguishable from a clean
        run with genuinely-phantom deps.

        Expected on fix: the envelope must disclose the cross-ecosystem
        / ecosystem-unsupported degradation via at least one of:

        * ``summary.partial_success: True``
        * ``summary.state``: a closed-enum string naming the degraded
          state (e.g. ``"cross_ecosystem_unmatched"`` /
          ``"ecosystems_unsupported"``)
        * ``summary.ecosystems_unsupported`` / ``summary.unmatched_ecosystems``:
          a list / count of declared ecosystems the indexer cannot resolve
        * ``summary.unmatched_count`` / ``summary.unresolved_count``: a
          non-zero integer naming how many declared deps the indexer
          could not answer the reachability question for
        * Per-component ``roam:reach_state`` / ``roam:resolution`` property
          with a closed enum (``matched`` / ``unmatched_name`` /
          ``ecosystem_unsupported`` / ``unreachable``), OR a tri-valued
          ``roam:reachable`` property replacing the boolean.
        * Verdict mentions the unmatched / cross-ecosystem / unresolved
          state directly.
        """
        proj = _make_polyglot_sbom_fixture(tmp_path)
        data, _res = _run_sbom_json(proj)

        summary = data.get("summary") or {}
        by_eco = _components_by_ecosystem(data)

        # Sanity: the fixture is correct — 3 ecosystems declared.
        assert summary.get("total_dependencies") == 3, f"fixture sanity: expected 3 deps ingested, got {summary!r}"
        assert set(by_eco.keys()) == {"python", "javascript", "java"}, (
            f"fixture sanity: expected 3 ecosystems, got {sorted(by_eco.keys())!r}"
        )

        # Locate the cross-ecosystem entries.
        npm_entries = by_eco.get("javascript", [])
        java_entries = by_eco.get("java", [])
        assert len(npm_entries) == 1, f"expected 1 npm dep, got {npm_entries!r}"
        assert len(java_entries) == 1, f"expected 1 maven dep, got {java_entries!r}"

        # === The W805-QQQQ assertion: at least ONE disclosure signal MUST
        # be present. None of them are today.
        partial = summary.get("partial_success") is True
        state_disclosed = bool(summary.get("state"))
        ecosystems_unsupported = bool(
            summary.get("ecosystems_unsupported")
            or summary.get("unmatched_ecosystems")
            or summary.get("unsupported_ecosystems")
        )
        unmatched_summary = bool(
            summary.get("unmatched_count")
            or summary.get("unresolved_count")
            or summary.get("ecosystem_unmatched_count")
        )

        # Per-component: tri-valued roam:reachable OR explicit
        # roam:reach_state / roam:resolution enum.
        components = data.get("sbom", {}).get("components", [])
        per_component_disclosed = False
        for c in components:
            props = {p["name"]: p["value"] for p in c.get("properties", [])}
            reach = props.get("roam:reachable", "")
            if reach not in ("true", "false"):
                per_component_disclosed = True
                break
            if props.get("roam:reach_state") or props.get("roam:resolution"):
                per_component_disclosed = True
                break

        verdict = (summary.get("verdict") or "").lower()
        verdict_discloses = any(
            frag in verdict
            for frag in (
                "unmatched",
                "unresolved",
                "cross-ecosystem",
                "cross ecosystem",
                "ecosystem unsupported",
                "ecosystems unsupported",
                "no extractor",
                "no symbol match",
                "unsupported",
            )
        )

        assert (
            partial
            or state_disclosed
            or ecosystems_unsupported
            or unmatched_summary
            or per_component_disclosed
            or verdict_discloses
        ), (
            "Pattern-1 variant D + Pattern-2 silent-SAFE: a polyglot "
            "SBOM (npm + maven + pip) against a Python indexer emitted "
            f"verdict={summary.get('verdict')!r} with partial_success="
            f"{summary.get('partial_success')!r}, state="
            f"{summary.get('state')!r}, ecosystems_unsupported absent, "
            f"reachable_count={summary.get('reachable_count')!r}, "
            f"phantom_count={summary.get('phantom_count')!r}. "
            "Components: "
            f"npm={npm_entries!r}, java={java_entries!r}. "
            "All emit roam:reachable=false with no distinction between "
            "(a) ecosystem-unsupported / (b) name-mismatch / (c) genuinely "
            "unreachable. Security severity HIGH (silent-SAFE on a "
            "supply-chain artifact consumed by Dependency-Track / FOSSA / "
            "GitHub Dependency Review / --ci gates)."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "W805-QQQQ-B: an SBOM whose every cross-ecosystem dep is "
            "unmatched produces verdict mentioning '0 reachable' / 'N "
            "phantom' — a textbook silent-SAFE on a supply-chain artifact, "
            "even though the ingest path explicitly recorded deps for "
            "ecosystems the indexer cannot resolve."
        ),
    )
    def test_unknown_ecosystem_state_disclosure(self, tmp_path):
        """A polyglot SBOM where 2 of 3 ecosystems are unsupported by the
        indexer (npm + maven on a Python-only indexer).

        Expected on fix: the verdict must NOT collapse the npm/maven
        phantom claims into the generic ``phantom`` count. At minimum the
        verdict should disclose ``N ecosystems unsupported``, OR
        ``partial_success`` should be True, OR ``state`` should name the
        degraded condition.
        """
        proj = _make_polyglot_sbom_fixture(tmp_path)
        data, _res = _run_sbom_json(proj)
        summary = data.get("summary") or {}

        # Forbidden verdict shape — a pure "N phantom" verdict with no
        # ecosystem context is silent-SAFE on a polyglot SBOM where the
        # majority of declared deps belong to ecosystems the indexer
        # cannot answer for.
        verdict = (summary.get("verdict") or "").lower()
        # "phantom" alone is acceptable on a fixed envelope IF the
        # ecosystems_unsupported signal is also present. We require
        # either an ecosystem-context word in the verdict OR an
        # ecosystems_unsupported field on the summary.
        has_eco_context_in_verdict = any(
            frag in verdict for frag in ("ecosystem", "unmatched", "unsupported", "cross-")
        )
        has_eco_field = bool(
            summary.get("ecosystems_unsupported")
            or summary.get("unmatched_ecosystems")
            or summary.get("unsupported_ecosystems")
            or summary.get("partial_success") is True
        )
        assert has_eco_context_in_verdict or has_eco_field, (
            f"silent-SAFE on polyglot SBOM: verdict={verdict!r} reads as a "
            "generic phantom-count without disclosing that 2/3 declared "
            "ecosystems (npm, maven) cannot be answered by the Python "
            "indexer. The npm lodash + maven commons phantom claims "
            "should NOT collapse into the same phantom count as the pip "
            "click name-mismatch case."
        )

    def test_empty_sbom_distinct_from_clean(self, tmp_path):
        """An empty corpus (no manifests, no source) emits a distinct
        verdict — ``No dependencies found -- empty SBOM generated``. This
        is the BASELINE shape we want polyglot-unsupported runs to
        diverge from after the fix.

        Pinned LIVE (not xfail) to assert today's empty-SBOM verdict
        carries no false-clean phrasing. If a fix lands that flips this
        verdict to a different state, update in the same patch.
        """
        proj = _make_empty_corpus(tmp_path)
        data, _res = _run_sbom_json(proj)
        summary = data.get("summary") or {}
        verdict = (summary.get("verdict") or "").lower()
        # Empty corpus today emits "No dependencies found" — we want
        # this to STAY distinct from a polyglot-unsupported run so the
        # fix has somewhere to put the new state.
        assert "no dependencies found" in verdict or summary.get("total_dependencies") == 0, (
            f"empty-corpus baseline drift: verdict={verdict!r}"
        )

    def test_ci_exit_code_does_not_pass_through_cross_eco_collapse(self, tmp_path):
        """Today's exit code on a silent cross-ecosystem collapse is 0
        (clean). This pin documents the present-day ``--ci`` pass-
        through surface: when a fix lands, a future exit-code review
        should consider returning a non-zero advisory code for runs
        where one or more declared ecosystems are unsupported.

        Pinned LIVE (not xfail) — the imported Python dependency is direct,
        while unsupported npm/Maven dependencies remain unresolved. If a future fix
        decides to flip the exit code as part of the disclosure surface
        (rather than just the envelope shape), this test will need
        updating in the same patch.
        """
        proj = _make_polyglot_sbom_fixture(tmp_path)
        data, res = _run_sbom_json(proj)

        assert res.exit_code == 0
        summary = data.get("summary") or {}
        assert summary.get("reachable_count") == 1, (
            "The imported Python dependency must be reachable while the "
            "unsupported npm and Maven dependencies remain unresolved."
        )


# ---------------------------------------------------------------------------
# Sister-suite parity invariants — these MUST pass today and stay green.
# A regression in any of these would fail BOTH the relevant pin and this
# suite, surfacing the cross-axis impact immediately.
# ---------------------------------------------------------------------------


class TestSecurityGateDisclosureFamilyParity:
    def test_w805_nnnn_invariants_preserved(self, tmp_path):
        """Sister: W805-NNNN (cmd_vuln_reach cross-lang) pin still holds.

        Re-runs the cross-language vuln-reach probe and confirms the
        silent-SAFE shape that W805-NNNN already pins. If this test
        EVER fails, it means either (a) the W805-NNNN fix landed without
        removing this test, or (b) W805-NNNN regressed in the opposite
        direction — either way, the pin file should be reviewed in the
        same patch.
        """
        proj = tmp_path / "vuln_reach_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
        (proj / "package.json").write_text(
            _json.dumps({"name": "p", "version": "0.0.1", "dependencies": {"lodash": "4.17.20"}}),
            encoding="utf-8",
        )
        (proj / "app.py").write_text(
            "def handle():\n    return process()\n\ndef process():\n    return merge_data({})\n\ndef merge_data(d):\n    return d\n",
            encoding="utf-8",
        )
        git_init(proj)

        report = [
            {
                "cve": "CVE-2024-NPM-LODASH",
                "package": "lodash",
                "severity": "critical",
                "title": "npm lodash (W805-NNNN parity)",
            }
        ]
        report_path = tmp_path / "vulns.json"
        report_path.write_text(_json.dumps(report), encoding="utf-8")

        from roam.cli import cli

        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner.invoke(cli, ["init"], catch_exceptions=False)
            runner.invoke(cli, ["vuln-map", "--generic", str(report_path)], catch_exceptions=False)
            r = runner.invoke(cli, ["--json", "vuln-reach"], catch_exceptions=False)
            assert r.exit_code == 0, r.output
            data = _json.loads(r.output)
        finally:
            os.chdir(old_cwd)

        summary = data.get("summary") or {}
        # W805-NNNN's shape: critical_count=0 despite a CRITICAL npm CVE
        # being ingested. If this assertion EVER fails, the W805-NNNN
        # fix has landed and this parity test should be updated.
        assert summary.get("critical_count") == 0, (
            f"W805-NNNN regression: critical_count should be 0 on silent-SAFE cross-lang run, got {summary!r}"
        )

    def test_w805_kkkk_invariants_preserved(self, tmp_path):
        """Sister: W805-KKKK (cmd_taint cross-lang) pin still holds.

        Re-runs a Python-corpus + JS-only-rules taint probe and confirms
        the silent-SAFE shape.
        """
        proj = tmp_path / "taint_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
        (proj / "app.py").write_text(
            "def handle():\n    return process()\n\ndef process():\n    return 0\n",
            encoding="utf-8",
        )
        git_init(proj)

        rules_dir = tmp_path / "js_rules"
        rules_dir.mkdir()
        (rules_dir / "js_xss.yaml").write_text(
            "id: js-xss-only\n"
            "description: JS-only rule pack (W805-KKKK parity)\n"
            "severity: error\n"
            "cwe: CWE-79\n"
            "languages:\n"
            "  - javascript\n"
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
            runner.invoke(cli, ["init"], catch_exceptions=False)
            r = runner.invoke(cli, ["--json", "taint", "--rules-dir", str(rules_dir)], catch_exceptions=False)
            assert r.exit_code == 0, r.output
            data = _json.loads(r.output)
        finally:
            os.chdir(old_cwd)

        summary = data.get("summary") or {}
        # W805-KKKK's shape: rules=1, findings=0 on a populated corpus.
        assert summary.get("rules") == 1, f"W805-KKKK regression: expected 1 rule loaded, got {summary!r}"
        assert summary.get("findings") == 0, (
            f"W805-KKKK regression: expected 0 findings on language-mismatched run, got {summary!r}"
        )

    def test_w826_invariants_preserved(self, tmp_path, monkeypatch):
        """Sister: W826 (cmd_vulns empty corpus) pin still holds.

        Re-asserts that ``roam vulns`` on an empty corpus emits
        ``state == 'no_scan'`` + ``partial_success: True``.
        """
        from conftest import index_in_process

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
            f"W826 regression: empty-vulns-table must surface state=='no_scan', got {summary!r}"
        )
        assert summary.get("partial_success") is True, (
            "W826 regression: empty-vulns-table must surface partial_success=True"
        )
