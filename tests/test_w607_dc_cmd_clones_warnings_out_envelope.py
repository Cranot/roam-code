"""W607-DC -- additive aggregation-phase plumbing for ``cmd_clones``.

cmd_clones detects DRY/architecture debt via AST-subtree-hash similarity
-- the structural-debt paired-scoring family (W805 4-way: clones BQ/DC,
duplicates BM, smells BN, dark_matter BK/CZ). With W607-DC landed, the
full clones path is now dual-bucket plumbed via:

  - substrate-CALL layer: W607-BQ (5 substrate boundaries:
    query_candidates / apply_test_prod_separation / classify_role_buckets
    / emit_findings / serialize_to_sarif)
  - aggregation-phase layer: W607-DC (4 aggregation boundaries:
    compute_predicate / compute_verdict / score_classify /
    serialize_envelope)

Both layers share the canonical ``clones_*`` marker family and the
``clones_<phase>_failed:<exc_class>:<detail>`` shape contract. The two
buckets (``_w607bq_warnings_out`` substrate-CALL +
``_w607dc_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order.

W978 7-discipline first-hypothesis check
----------------------------------------

cmd_sbom W607-CG sealed the kwarg-default eagerness trap (computed
defaults eval BEFORE the try-block).
cmd_taint W607-CJ codified the 5th discipline: move ``len()`` INSIDE
the wrapped closure rather than at the kwarg-bind site.
cmd_audit_trail_export W607-CR codified the 7th discipline: use bare
``dict[key]`` lookup when a floor dict guarantees the key, NOT
``dict.get(key, expensive_default)`` -- ``.get`` evaluates default
eagerly at call site, re-raising on a poisoned upstream input.

Every W607-DC ``default=`` MUST be a literal constant, AND every
``len()`` / ``sum()`` over the wrapped input MUST live inside the
closure. The AST audit below pins these disciplines at the W607-DC
layer.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Canonical W607-DC phase enumeration
# ---------------------------------------------------------------------------


_DC_PHASES = (
    "compute_predicate",
    "compute_verdict",
    "score_classify",
    "serialize_envelope",
)

_BQ_PHASES = (
    "query_candidates",
    "apply_test_prod_separation",
    "classify_role_buckets",
    "emit_findings",
    "serialize_to_sarif",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def clones_project(project_factory):
    """Small indexed corpus -- enough for cmd_clones to emit a non-empty
    envelope. Clone clusters may be 0 on this small corpus, but the
    envelope is fully formed either way (the W607-DC aggregation-phase
    plumbing runs regardless of cluster count -- aggregation is the
    axis under test, not detection sensitivity)."""
    return project_factory(
        {
            "service.py": (
                "def process_user(user_id):\n"
                "    if user_id is None:\n"
                "        return None\n"
                "    result = compute(user_id)\n"
                "    if result is None:\n"
                "        return None\n"
                "    return result\n"
                "\n"
                "def process_order(order_id):\n"
                "    if order_id is None:\n"
                "        return None\n"
                "    result = compute(order_id)\n"
                "    if result is None:\n"
                "        return None\n"
                "    return result\n"
                "\n"
                "def compute(x):\n"
                "    return x * 2\n"
            ),
            "api.py": (
                "def handle_user(user_id):\n"
                "    if user_id is None:\n"
                "        return None\n"
                "    result = lookup(user_id)\n"
                "    if result is None:\n"
                "        return None\n"
                "    return result\n"
                "\n"
                "def lookup(x):\n"
                "    return x + 1\n"
            ),
        }
    )


def _invoke_clones(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam clones`` against a project root via top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("clones")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DC aggregation markers
# ---------------------------------------------------------------------------


def test_clones_happy_path_no_w607dc_markers(cli_runner, clones_project):
    """Clean clones on a populated corpus -> no W607-DC aggregation
    markers.

    Hash-stable: an empty W607-DC bucket on the success path must
    produce an envelope without any
    ``clones_compute_predicate_failed:`` /
    ``clones_compute_verdict_failed:`` /
    ``clones_score_classify_failed:`` /
    ``clones_serialize_envelope_failed:`` markers (from the DC layer).
    """
    result = _invoke_clones(cli_runner, clones_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "clones"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _DC_PHASES:
        prefix = f"clones_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean clones must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_dc`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_clones_carries_w607dc_accumulator():
    """AST-level guard: cmd_clones source carries the W607-DC
    accumulator.

    Pins the canonical W607-DC anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-BQ) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_clones.py"
    assert src_path.exists(), f"cmd_clones.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607dc_warnings_out" in src, (
        "W607-DC accumulator missing from cmd_clones; the additive aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_dc" in src, (
        "W607-DC helper ``_run_check_dc`` missing from cmd_clones; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_dc is defined inside the command.
    tree = ast.parse(src)
    found_run_check_dc = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dc":
            found_run_check_dc = True
            break
    assert found_run_check_dc, (
        "W607-DC ``_run_check_dc`` helper not found in cmd_clones "
        "AST; the additive aggregation-phase wrapper has been refactored "
        "away."
    )

    # W607-BQ must still be present (additive layer does NOT replace it)
    assert "_w607bq_warnings_out" in src, (
        "W607-BQ accumulator vanished alongside the W607-DC add; the "
        "additive plumbing must preserve the W607-BQ substrate-CALL "
        "layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_dc():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_dc(...)`` with the canonical phase name.

    The four phases must appear inside a ``_run_check_dc("<phase>", ...)``
    call inside cmd_clones.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_clones.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _DC_PHASES:
        same_line = f'_run_check_dc("{phase}"' in src
        multi_line = any(f'_run_check_dc(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"clones_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DC wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) serialize_envelope failure -> floor envelope still ships with marker
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, clones_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``clones_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("clones", ...)`` would otherwise crash AFTER all
    substrate + aggregation signals were already gathered. The consumer
    must still receive a parseable JSON object with the marker attached
    + the canonical command name.
    """
    from roam.commands import cmd_clones as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DC")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_clones(cli_runner, clones_project)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "clones", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("clones_serialize_envelope_failed:")]
    assert markers, f"expected ``clones_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict floor is a literal constant -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """Pin the W978 discipline anchor: compute_verdict floor must be a
    literal string, NOT an f-string re-interpolating the same values
    that just raised.

    The canonical floor for cmd_clones is ``"clones completed"`` (mirror
    of cmd_dark_matter W607-CZ's ``"dark-matter completed"`` and
    cmd_postmortem W607-CV's ``"postmortem completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_clones.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="clones completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-DC "
        "discipline; the canonical floor literal 'clones completed' "
        "is missing from cmd_clones.py"
    )


# ---------------------------------------------------------------------------
# (6) ANY W607-DC marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, clones_project, monkeypatch):
    """ANY W607-DC or W607-BQ marker must flip
    summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    clones" from "clones ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_clones as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-DC")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_clones(cli_runner, clones_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-DC warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607dc_warnings_out_in_both_top_and_summary(cli_runner, clones_project, monkeypatch):
    """Non-empty W607-DC bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-BQ contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_clones as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DC")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_clones(cli_runner, clones_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DC raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DC raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("clones_serialize_envelope_failed:")]
    summary_markers = [m for m in data["summary"]["warnings_out"] if m.startswith("clones_serialize_envelope_failed:")]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-DC uses the SAME ``clones_*`` family
# ---------------------------------------------------------------------------


def test_w607dc_marker_prefix_clones_family(cli_runner, clones_project, monkeypatch):
    """W607-DC markers use the canonical ``clones_*`` prefix (same
    family as W607-BQ; W607-DC is ADDITIVE, not a separate prefix).

    Hard guard: any W607-DC marker that leaks into a sibling W607-*
    family (e.g. ``dark_matter_*`` / ``duplicates_*`` / ``smells_*``)
    breaks the closed-enum marker-family contract.
    """
    from roam.commands import cmd_clones as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-DC")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_clones(cli_runner, clones_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("clones_"), f"every W607-DC marker must use the ``clones_*`` prefix; got {marker!r}"

    # W979 vocabulary regression guard: marker prefix uses underscore
    # form (matches pre-existing clones_* family).
    for marker in failure_markers:
        assert not marker.startswith("clones-"), (
            f"marker uses hyphenated form -- inconsistent with the "
            f"pre-existing clones_* family from W607-BQ; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (9) W607-BQ COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607bq_substrate_markers_coexist_with_w607dc_aggregation(cli_runner, clones_project, monkeypatch):
    """Confirm ``clones_<substrate-phase>_failed:`` markers (W607-BQ
    layer) coexist with ``clones_<agg-phase>_failed:`` markers
    (W607-DC layer) -- both in same family, but threaded through
    different buckets at envelope-emit.

    The additive aggregation-phase layer must NOT shadow the pre-
    existing substrate-CALL layer; both buckets must combine into the
    same warnings_out channel with marker-prefix disambiguation
    (``clones_<substrate-phase>_failed:`` vs
    ``clones_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_clones as _mod
    from roam.graph import clone_detect as _clone_detect

    # W607-BQ substrate boundary -- detect_clones raises
    def _raise_detect(*a, **kw):
        raise RuntimeError("synthetic-bq-coexist-detect")

    # W607-DC aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-dc-coexist-envelope")

    # detect_clones is imported lazily inside the command (``from
    # roam.graph.clone_detect import detect_clones, store_clones``).
    # Patch the source module so the lazy import sees the raise.
    monkeypatch.setattr(_clone_detect, "detect_clones", _raise_detect)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_clones(cli_runner, clones_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-BQ (query_candidates wraps
    # detect_clones per the cmd_clones call site).
    bq_markers = [m for m in top_wo if m.startswith("clones_query_candidates_failed:")]
    # Aggregation-phase from W607-DC
    dc_markers = [m for m in top_wo if m.startswith("clones_serialize_envelope_failed:")]

    assert bq_markers, f"W607-BQ substrate-CALL marker (clones_query_candidates_failed) missing; got {top_wo!r}"
    assert dc_markers, f"W607-DC aggregation-phase marker (clones_serialize_envelope_failed) missing; got {top_wo!r}"

    # Both share the canonical ``clones_*`` family
    assert all(m.startswith("clones_") for m in (bq_markers + dc_markers)), (
        f"all markers must share the canonical ``clones_*`` family; got bq = {bq_markers!r}, dc = {dc_markers!r}"
    )


# ---------------------------------------------------------------------------
# (10) W978 7-discipline AST audit -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """W978 kwarg-default audit: every W607-DC ``default=`` must be a
    literal constant, NOT computed from upstream values.

    cmd_sbom W607-CG sealed this axis after a regression where
    ``len(_BadDeps())`` defaults eagerly raised inside the ``default=``
    expression -- BEFORE the wrap call entered the try-block. cmd_taint
    W607-CJ added the 5th discipline: ``len()`` lives INSIDE the
    closure, not at the kwarg-bind site.

    AST audit: walk every ``_run_check_dc(...)`` call, extract the
    ``default=`` keyword argument's AST node, confirm it is a Constant
    (literal int/str/bool/None) or a Dict/List/Set/Tuple of Constants,
    or a bare Name (variable bound BEFORE the wrap call). Reject any
    Call, Attribute, Subscript, BinOp, Compare, IfExp, or f-string node
    in the default expression -- these compute from upstream values at
    kwarg-bind time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_clones.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
        """True iff ``node`` is a fully-literal AST subtree.

        Allows: Constant, Dict/List/Tuple/Set of literals, unary +/- of
        a constant, and bare Name references (variables bound BEFORE
        the wrap call, e.g. ``default=_envelope_floor``). Rejects Call,
        Attribute, Subscript, BinOp, Compare, IfExp, f-string, etc. --
        these can compute over potentially-poisoned upstream values at
        kwarg-bind time and raise BEFORE the wrap's try-block enters.
        """
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.Dict):
            return all(_is_literal(k) for k in node.keys if k is not None) and all(_is_literal(v) for v in node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_literal(e) for e in node.elts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _is_literal(node.operand)
        return False

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dc"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_dc(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_clones.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ / cmd_audit_trail_export "
        "W607-CR for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (11) W978 5th-discipline -- closures call len() INSIDE, not at kwarg-bind site
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_closures_not_at_kwarg_bind_site():
    """W978 5th-discipline AST guard (cmd_taint W607-CJ anchor): every
    ``len()`` call on a wrapped input MUST live INSIDE the wrapped
    closure, NOT at the ``_run_check_dc(...)`` call site as a positional
    or keyword argument expression.

    A ``_run_check_dc("compute_verdict", _build, len(clusters))``-style
    call would evaluate ``len(clusters)`` BEFORE the wrap's try-block
    enters; a ``__len__``-poisoned sentinel would escape the wrap and
    crash the command. Source-level audit: confirm no ``_run_check_dc``
    call carries a ``len(...)`` expression in its positional args or
    kwarg expressions.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_clones.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dc"):
            continue
        # Walk every positional arg AND every keyword-arg expression;
        # reject Call(Name("len"), ...) appearing as an argument
        # expression at the wrap call site.
        for sub in node.args:
            for descendant in ast.walk(sub):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call at "
                        f"_run_check_dc positional-arg site -- W978 "
                        f"5th-discipline violation"
                    )
        for kw in node.keywords:
            for descendant in ast.walk(kw.value):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call in "
                        f"_run_check_dc kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_clones.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure. See cmd_taint W607-CJ "
        "for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (12) Clean envelope carries run_state from score_classify
# ---------------------------------------------------------------------------


def test_clean_envelope_carries_run_state(cli_runner, clones_project):
    """W607-DC surfaces run_state on the envelope.

    The score_classify closure returns a state label (CLONES_FOUND /
    NO_CLONES) which the envelope surfaces so consumers can read the
    run classification without re-deriving from raw counts.
    """
    result = _invoke_clones(cli_runner, clones_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    assert summary.get("run_state") in {
        "CLONES_FOUND",
        "NO_CLONES",
        "DEGRADED",
    }, f"run_state missing or invalid on clean envelope; got {summary.get('run_state')!r}"


# ---------------------------------------------------------------------------
# (13) Cross-prefix isolation -- W607-DC stays in clones_* family
# ---------------------------------------------------------------------------


def test_w607dc_cross_prefix_isolation(cli_runner, clones_project, monkeypatch):
    """Hard guard: W607-DC markers must NOT leak into sibling W607-*
    prefix families.

    Every other W607-plumbed command in the W805 structural-debt
    paired-scoring 4-way (dark_matter / duplicates / smells) -- as well
    as the broader W607 family -- owns its own marker prefix. A drift
    here would silently re-attribute a clones degradation to a sibling
    detector.
    """
    from roam.commands import cmd_clones as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_clones(cli_runner, clones_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)
    failure_markers = [m for m in all_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for cross-prefix check"
    for marker in failure_markers:
        # W805 structural-debt 4-way sibling prefixes
        for forbidden_prefix, sibling in (
            ("dark_matter_", "cmd_dark_matter W805 sibling"),
            ("duplicates_", "cmd_duplicates W805 sibling"),
            ("smells_", "cmd_smells W805 sibling"),
            # Broader W607 family
            ("postmortem_", "cmd_postmortem W607-AN/CV"),
            ("audit_trail_verify_", "cmd_audit_trail_verify W607-AI"),
            ("audit_trail_conformance_", "cmd_audit_trail_conformance W607-CO"),
            ("audit_trail_export_", "cmd_audit_trail_export W607-CR"),
            ("vulns_", "cmd_vulns W607-AQ / CH"),
            ("taint_", "cmd_taint W607-AY / CJ"),
            ("sbom_", "cmd_sbom W607-AM / CG"),
            ("debt_", "cmd_debt W607-BG"),
            ("health_", "cmd_health W607-M / BA"),
            ("supply_chain_", "cmd_supply_chain W607-AK / CD"),
            ("attest_", "cmd_attest W607-AD / BT"),
            ("diff_", "cmd_diff W607-Z / BP"),
            ("critique_", "cmd_critique W607-Y / BL"),
            ("pr_risk_", "cmd_pr_risk W607-Q / BU"),
            ("impact_", "cmd_impact W607-T / BB"),
            ("retrieve_", "cmd_retrieve W607-B / BI"),
            ("findings_", "cmd_findings W607-C"),
            ("deps_", "cmd_deps W607-V / DB"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (14) STRUCTURAL-DEBT PAIRED-SCORING 4-WAY pairing closure
# ---------------------------------------------------------------------------


def test_w805_structural_debt_4way_aggregation_pairing(cli_runner, clones_project, monkeypatch):
    """W805 structural-debt 4-way pairing: confirm the aggregation-phase
    layer (W607-DC) coexists with the substrate-CALL layer (W607-BQ)
    on cmd_clones and stays distinct from the sibling paired-scoring
    detectors (dark_matter BK/CZ / duplicates BM / smells BN).

    The W805 family detects DRY/architecture debt from 4 different
    signal axes on the same corpus:
      cmd_clones      (W607-BQ substrate + DC THIS) -- AST-similarity axis
      cmd_duplicates  (W607-BM substrate)           -- token-similarity axis
      cmd_smells      (W607-BN substrate)           -- smell-pattern axis
      cmd_dark_matter (W607-BK substrate + CZ)      -- co-change axis

    With W607-DC landed, cmd_clones becomes the SECOND member of the
    4-way to ALSO carry an aggregation-phase layer (cmd_dark_matter
    W607-CZ was the first). This test confirms both layers coexist on
    its envelope -- the structural-debt 4-way pairing closes at the
    aggregation-phase layer here.
    """
    from roam.commands import cmd_clones as _mod
    from roam.graph import clone_detect as _clone_detect

    # Force BOTH layers to emit a marker
    def _raise_detect(*a, **kw):
        raise RuntimeError("synthetic-4way-bq-detect")

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-4way-dc-envelope")

    monkeypatch.setattr(_clone_detect, "detect_clones", _raise_detect)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_clones(cli_runner, clones_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    # Both layer markers present, both share the clones_ family
    bq_markers = [m for m in all_wo if any(f"clones_{p}_failed:" in m for p in _BQ_PHASES)]
    dc_markers = [m for m in all_wo if any(f"clones_{p}_failed:" in m for p in _DC_PHASES)]
    assert bq_markers and dc_markers, (
        f"4-way pairing requires BOTH W607-BQ substrate-CALL markers AND "
        f"W607-DC aggregation-phase markers on the same envelope; "
        f"got bq = {bq_markers!r}, dc = {dc_markers!r}"
    )

    # Sibling W805 prefix isolation (no leakage into dark_matter /
    # duplicates / smells)
    for sibling_prefix in ("dark_matter_", "duplicates_", "smells_"):
        sibling_leak = [m for m in all_wo if m.startswith(sibling_prefix)]
        assert not sibling_leak, (
            f"clones envelope leaked into {sibling_prefix}* family "
            f"(W805 paired-scoring sibling scope); got {sibling_leak!r}"
        )


# ---------------------------------------------------------------------------
# (15) Pre-existing W607-BQ + W607-DC accumulators both coexist
# ---------------------------------------------------------------------------


def test_w607dc_coexists_with_pre_existing_marker_families():
    """W607-DC is ADDITIVE -- the pre-existing W607-BQ substrate-CALL
    family must still be present in source.

    Source-level guard: BOTH marker prefix families (W607-BQ substrate
    + W607-DC aggregation) are present in the cmd_clones source. A
    future refactor that removes one of them must not silently break
    the contract; both accumulators must coexist and combine at
    envelope-emit time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_clones.py"
    src = src_path.read_text(encoding="utf-8")

    # W607-BQ substrate-CALL family
    assert "_w607bq_warnings_out" in src, "W607-BQ substrate-CALL accumulator has been removed."
    assert "_run_check_bq" in src, "W607-BQ helper has been removed."
    # W607-DC aggregation-phase family (THIS wave)
    assert "_w607dc_warnings_out" in src, "W607-DC aggregation-phase accumulator has been removed."
    assert "_run_check_dc" in src, "W607-DC helper has been removed."

    # Both families share the clones_* prefix discipline -- the
    # marker-prefix tests above pin the runtime invariant.
