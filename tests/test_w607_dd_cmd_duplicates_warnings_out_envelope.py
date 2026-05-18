"""W607-DD -- additive aggregation-phase plumbing for ``cmd_duplicates``.

cmd_duplicates detects DRY/architecture debt via AST-similarity of
function metrics -- the structural-debt paired-scoring family (W805
4-way: clones BQ, duplicates BM/DD, smells BN, dark_matter BK/CZ).
With W607-DD landed, the full duplicates path is now dual-bucket
plumbed via:

  - substrate-CALL layer: W607-BM (5 substrate boundaries:
    query_candidates / compute_similarity / classify_role_buckets /
    emit_findings / serialize_to_sarif)
  - aggregation-phase layer: W607-DD (4 aggregation boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``duplicates_*`` marker family
and the ``duplicates_<phase>_failed:<exc_class>:<detail>`` shape
contract. The two buckets (``_w607bm_warnings_out`` substrate-CALL
+ ``_w607dd_warnings_out`` aggregation-phase) are combined at
envelope-emit time so consumers see the full degradation lineage
in marker-emission order.

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

Every W607-DD ``default=`` MUST be a literal constant, AND every
``len()`` / ``sum()`` over the wrapped input MUST live inside the
closure. The AST audit below pins these disciplines at the W607-DD
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
# Canonical W607-DD phase enumeration
# ---------------------------------------------------------------------------


_DD_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)

_BM_PHASES = (
    "query_candidates",
    "compute_similarity",
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
def duplicates_project(project_factory):
    """Small indexed corpus -- enough for cmd_duplicates to emit a
    well-formed envelope. Duplicate clusters may be 0 on this small
    corpus (the functions are too short / dissimilar to cluster) but
    the envelope shape is fully formed either way."""
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


def _invoke_duplicates(cli_runner, project_root, *args, json_mode=True):
    """Invoke ``roam duplicates`` against a project root via top-level CLI."""
    from roam.cli import cli

    full_args: list[str] = []
    if json_mode:
        full_args.append("--json")
    full_args.append("duplicates")
    full_args.extend(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_root))
        return cli_runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-DD aggregation markers
# ---------------------------------------------------------------------------


def test_duplicates_happy_path_no_w607dd_markers(cli_runner, duplicates_project):
    """Clean duplicates run on a populated corpus -> no W607-DD aggregation
    markers.

    Hash-stable: an empty W607-DD bucket on the success path must
    produce an envelope without any
    ``duplicates_score_classify_failed:`` /
    ``duplicates_compute_predicate_failed:`` /
    ``duplicates_compute_verdict_failed:`` /
    ``duplicates_serialize_envelope_failed:`` markers (from the DD
    layer).
    """
    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["command"] == "duplicates"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _DD_PHASES:
        prefix = f"duplicates_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean duplicates must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_dd`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_duplicates_carries_w607dd_accumulator():
    """AST-level guard: cmd_duplicates source carries the W607-DD
    accumulator.

    Pins the canonical W607-DD anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-BM) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_duplicates.py"
    assert src_path.exists(), f"cmd_duplicates.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607dd_warnings_out" in src, (
        "W607-DD accumulator missing from cmd_duplicates; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_dd" in src, (
        "W607-DD helper ``_run_check_dd`` missing from cmd_duplicates; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_dd is defined inside the command.
    tree = ast.parse(src)
    found_run_check_dd = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dd":
            found_run_check_dd = True
            break
    assert found_run_check_dd, (
        "W607-DD ``_run_check_dd`` helper not found in cmd_duplicates "
        "AST; the additive aggregation-phase wrapper has been refactored "
        "away."
    )

    # W607-BM must still be present (additive layer does NOT replace it)
    assert "_w607bm_warnings_out" in src, (
        "W607-BM accumulator vanished alongside the W607-DD add; the "
        "additive plumbing must preserve the W607-BM substrate-CALL "
        "layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_dd():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_dd(...)`` with the canonical phase name.

    The four phases must appear inside a ``_run_check_dd("<phase>", ...)``
    call inside cmd_duplicates.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_duplicates.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _DD_PHASES:
        same_line = f'_run_check_dd("{phase}"' in src
        multi_line = any(f'_run_check_dd(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"duplicates_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DD wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) serialize_envelope failure -> floor envelope still ships with marker
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, duplicates_project, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``duplicates_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("duplicates", ...)`` would otherwise crash AFTER
    all substrate + aggregation signals were already gathered. The
    consumer must still receive a parseable JSON object with the
    marker attached + the canonical command name.
    """
    from roam.commands import cmd_duplicates as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-DD")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _json.loads(result.output)
    assert data.get("command") == "duplicates", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("duplicates_serialize_envelope_failed:")]
    assert markers, f"expected ``duplicates_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict floor is a literal constant -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """Pin the W978 discipline anchor: compute_verdict floor must be a
    literal string, NOT an f-string re-interpolating the same values
    that just raised.

    The canonical floor for cmd_duplicates is ``"duplicates completed"``
    (mirror of cmd_dark_matter W607-CZ's ``"dark-matter completed"``
    and cmd_postmortem W607-CV's ``"postmortem completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_duplicates.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="duplicates completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-DD "
        "discipline; the canonical floor literal 'duplicates completed' "
        "is missing from cmd_duplicates.py"
    )


# ---------------------------------------------------------------------------
# (6) ANY W607-DD marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, duplicates_project, monkeypatch):
    """ANY W607-DD or W607-BM marker must flip
    summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    duplicates" from "duplicates ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_duplicates as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-DD")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-DD warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607dd_warnings_out_in_both_top_and_summary(cli_runner, duplicates_project, monkeypatch):
    """Non-empty W607-DD bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-BM contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_duplicates as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-DD")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DD raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DD raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("duplicates_serialize_envelope_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("duplicates_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-DD uses the SAME ``duplicates_*`` family
# ---------------------------------------------------------------------------


def test_w607dd_marker_prefix_duplicates_family(cli_runner, duplicates_project, monkeypatch):
    """W607-DD markers use the canonical ``duplicates_*`` prefix (same
    family as W607-BM; W607-DD is ADDITIVE, not a separate prefix).

    Hard guard: any W607-DD marker that leaks into a sibling W607-*
    family (e.g. ``clones_*`` / ``dark_matter_*`` / ``smells_*``) breaks
    the closed-enum marker-family contract.
    """
    from roam.commands import cmd_duplicates as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-DD")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("duplicates_"), (
            f"every W607-DD marker must use the ``duplicates_*`` prefix; got {marker!r}"
        )

    # W979 vocabulary regression guard: marker prefix uses underscore
    # form (matches pre-existing duplicates_* family from W607-BM).
    for marker in failure_markers:
        assert not marker.startswith("duplicates-"), (
            f"marker uses hyphenated form -- inconsistent with the "
            f"pre-existing duplicates_* family from W607-BM; "
            f"got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (9) W607-BM COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607bm_substrate_markers_coexist_with_w607dd_aggregation(cli_runner, duplicates_project, monkeypatch):
    """Confirm ``duplicates_<substrate-phase>_failed:`` markers (W607-BM
    layer) coexist with ``duplicates_<agg-phase>_failed:`` markers
    (W607-DD layer) -- both in same family, but threaded through
    different buckets at envelope-emit.

    The additive aggregation-phase layer must NOT shadow the pre-
    existing substrate-CALL layer; both buckets must combine into the
    same warnings_out channel with marker-prefix disambiguation
    (``duplicates_<substrate-phase>_failed:`` vs
    ``duplicates_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_duplicates as _mod

    # W607-BM substrate boundary -- _compute_similarity raises
    def _raise_similarity(*a, **kw):
        raise RuntimeError("synthetic-bm-coexist-similarity")

    # W607-DD aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-dd-coexist-envelope")

    monkeypatch.setattr(_mod, "_compute_similarity", _raise_similarity)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-BM (compute_similarity wraps
    # _compute_similarity per the cmd_duplicates call site).
    bm_markers = [m for m in top_wo if m.startswith("duplicates_compute_similarity_failed:")]
    # Aggregation-phase from W607-DD
    dd_markers = [m for m in top_wo if m.startswith("duplicates_serialize_envelope_failed:")]

    assert bm_markers, f"W607-BM substrate-CALL marker (duplicates_compute_similarity_failed) missing; got {top_wo!r}"
    assert dd_markers, (
        f"W607-DD aggregation-phase marker (duplicates_serialize_envelope_failed) missing; got {top_wo!r}"
    )

    # Both share the canonical ``duplicates_*`` family
    assert all(m.startswith("duplicates_") for m in (bm_markers + dd_markers)), (
        f"all markers must share the canonical ``duplicates_*`` family; got bm = {bm_markers!r}, dd = {dd_markers!r}"
    )


# ---------------------------------------------------------------------------
# (10) W978 7-discipline AST audit -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """W978 kwarg-default audit: every W607-DD ``default=`` must be a
    literal constant, NOT computed from upstream values.

    cmd_sbom W607-CG sealed this axis after a regression where
    ``len(_BadDeps())`` defaults eagerly raised inside the ``default=``
    expression -- BEFORE the wrap call entered the try-block. cmd_taint
    W607-CJ added the 5th discipline: ``len()`` lives INSIDE the
    closure, not at the kwarg-bind site.

    AST audit: walk every ``_run_check_dd(...)`` call, extract the
    ``default=`` keyword argument's AST node, confirm it is a Constant
    (literal int/str/bool/None) or a Dict/List/Set/Tuple of Constants,
    or a bare Name (variable bound BEFORE the wrap call). Reject any
    Call, Attribute, Subscript, BinOp, Compare, IfExp, or f-string node
    in the default expression -- these compute from upstream values at
    kwarg-bind time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_duplicates.py"
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
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dd"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_dd(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_duplicates.py:\n"
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
    closure, NOT at the ``_run_check_dd(...)`` call site as a positional
    or keyword argument expression.

    A ``_run_check_dd("compute_verdict", _build, len(cluster_list))``-style
    call would evaluate ``len(cluster_list)`` BEFORE the wrap's
    try-block enters; a ``__len__``-poisoned sentinel would escape the
    wrap and crash the command. Source-level audit: confirm no
    ``_run_check_dd`` call carries a ``len(...)`` expression in its
    positional args or kwarg expressions.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_duplicates.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dd"):
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
                        f"_run_check_dd positional-arg site -- W978 "
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
                        f"_run_check_dd kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_duplicates.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure. See cmd_taint W607-CJ "
        "for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (12) Clean envelope carries a well-formed summary verdict
# ---------------------------------------------------------------------------


def test_clean_envelope_carries_verdict(cli_runner, duplicates_project):
    """W607-DD surfaces a well-formed verdict on the envelope.

    The compute_verdict closure assembles the verdict string from the
    bucket counts + sampled/truncated qualifiers. On the clean path the
    envelope MUST carry a single-line verdict that satisfies LAW 6
    standalone-parse.
    """
    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    summary = data["summary"]

    verdict = summary.get("verdict")
    assert isinstance(verdict, str) and verdict, f"verdict missing or non-string on clean envelope; got {verdict!r}"
    assert "\n" not in verdict, f"verdict must be a single line (LAW 6 standalone-parse); got {verdict!r}"


# ---------------------------------------------------------------------------
# (13) Cross-prefix isolation -- W607-DD stays in duplicates_* family
# ---------------------------------------------------------------------------


def test_w607dd_cross_prefix_isolation(cli_runner, duplicates_project, monkeypatch):
    """Hard guard: W607-DD markers must NOT leak into sibling W607-*
    prefix families.

    Every other W607-plumbed command in the W805 structural-debt
    paired-scoring 4-way (clones / dark_matter / smells) -- as well as
    the broader W607 family -- owns its own marker prefix. A drift
    here would silently re-attribute a duplicates degradation to a
    sibling detector.
    """
    from roam.commands import cmd_duplicates as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-cross-prefix-isolation")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_duplicates(cli_runner, duplicates_project)
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
            ("clones_", "cmd_clones W805 sibling"),
            ("dark_matter_", "cmd_dark_matter W805 sibling (W607-BK/CZ)"),
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
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (14) STRUCTURAL-DEBT PAIRED-SCORING 4-WAY pairing closure
# ---------------------------------------------------------------------------


def test_w805_structural_debt_4way_aggregation_pairing(cli_runner, duplicates_project, monkeypatch):
    """W805 structural-debt 4-way pairing: confirm the aggregation-phase
    layer (W607-DD) coexists with the substrate-CALL layer (W607-BM)
    on cmd_duplicates and stays distinct from the sibling
    paired-scoring detectors (clones BQ / dark_matter BK/CZ / smells BN).

    The W805 family detects DRY/architecture debt from 4 different
    signal axes on the same corpus:
      cmd_clones      (W607-BQ substrate)        -- AST-similarity axis
      cmd_duplicates  (W607-BM substrate + DD THIS) -- token/metric-similarity axis
      cmd_smells      (W607-BN substrate)        -- smell-pattern axis
      cmd_dark_matter (W607-BK substrate + CZ)   -- co-change axis

    With W607-DD landed, cmd_duplicates joins cmd_dark_matter as the
    SECOND member of the 4-way to ALSO carry an aggregation-phase
    layer. This test confirms both layers coexist on its envelope --
    the structural-debt 4-way pairing closes at the aggregation-phase
    layer here.
    """
    from roam.commands import cmd_duplicates as _mod

    # Force BOTH layers to emit a marker
    def _raise_similarity(*a, **kw):
        raise RuntimeError("synthetic-4way-bm-similarity")

    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-4way-dd-envelope")

    monkeypatch.setattr(_mod, "_compute_similarity", _raise_similarity)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_duplicates(cli_runner, duplicates_project)
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_wo = list(top_wo) + list(summary_wo)

    # Both layer markers present, both share the duplicates_ family
    bm_markers = [m for m in all_wo if any(f"duplicates_{p}_failed:" in m for p in _BM_PHASES)]
    dd_markers = [m for m in all_wo if any(f"duplicates_{p}_failed:" in m for p in _DD_PHASES)]
    assert bm_markers and dd_markers, (
        f"4-way pairing requires BOTH W607-BM substrate-CALL markers AND "
        f"W607-DD aggregation-phase markers on the same envelope; "
        f"got bm = {bm_markers!r}, dd = {dd_markers!r}"
    )

    # Sibling W805 prefix isolation (no leakage into clones / dark_matter / smells)
    for sibling_prefix in ("clones_", "dark_matter_", "smells_"):
        sibling_leak = [m for m in all_wo if m.startswith(sibling_prefix)]
        assert not sibling_leak, (
            f"duplicates envelope leaked into {sibling_prefix}* family "
            f"(W805 paired-scoring sibling scope); got {sibling_leak!r}"
        )


# ---------------------------------------------------------------------------
# (15) Pre-existing W607-BM + W607-DD families both present in source
# ---------------------------------------------------------------------------


def test_w607dd_coexists_with_pre_existing_w607bm_family():
    """W607-DD is ADDITIVE -- the pre-existing W607-BM substrate-CALL
    family must still be present in source.

    Source-level guard: BOTH marker plumbing layers are present in the
    cmd_duplicates source. A future refactor that removes one of them
    must not silently break the contract; both accumulators must coexist
    and combine at envelope-emit time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_duplicates.py"
    src = src_path.read_text(encoding="utf-8")

    # W607-BM substrate-CALL family
    assert "_w607bm_warnings_out" in src, "W607-BM substrate-CALL accumulator has been removed."
    assert "_run_check_bm" in src, "W607-BM helper has been removed."
    # W607-DD aggregation-phase family (THIS wave)
    assert "_w607dd_warnings_out" in src, "W607-DD aggregation-phase accumulator has been removed."
    assert "_run_check_dd" in src, "W607-DD helper has been removed."

    # Both families share the duplicates_* prefix discipline -- the
    # marker-prefix tests above pin the runtime invariant.
