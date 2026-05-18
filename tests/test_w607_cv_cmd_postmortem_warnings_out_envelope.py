"""W607-CV -- additive aggregation-phase plumbing for ``cmd_postmortem``.

cmd_postmortem is the post-incident analyzer; it walks a commit range,
runs the current critique detector set against each commit's outgoing
diff, and aggregates per-commit findings into a retrospective report.
With W607-CV landed, the full postmortem path is now dual-bucket
plumbed via:

  - substrate-CALL layer: W607-AN (6 substrate boundaries:
    load_run_ledger / parse_event_payload / classify_failure /
    aggregate_by_phase / compute_root_cause / aggregate_by_actor)
  - aggregation-phase layer: W607-CV (4 aggregation boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)

Both layers share the canonical ``postmortem_*`` marker family
and the ``postmortem_<phase>_failed:<exc_class>:<detail>`` shape
contract. The two buckets (``_w607an_warnings_out`` substrate-CALL +
``_w607cv_warnings_out`` aggregation-phase) are combined at envelope-
emit time so consumers see the full degradation lineage in marker-
emission order.

W978 first-hypothesis check (5+ recurring traps)
------------------------------------------------

cmd_taint W607-CJ codified the 5th W978 discipline: move ``len()``
INSIDE the wrapped closure rather than at the kwarg-bind site. Every
W607-CV ``default=`` MUST be a literal constant, AND every ``len()``
/ ``sum()`` over the wrapped input MUST live inside the closure. The
defensive test below exercises the floor on a corrupt-input sentinel
mirroring cmd_sbom's ``_BadDeps(list)`` shape.

cmd_audit_trail_export W607-CR codified the 7th W978 discipline: use
bare ``dict[key]`` lookup when a floor dict guarantees the key, NOT
``dict.get(key, expensive_default)`` -- ``.get`` evaluates default
eagerly at call site, re-raising on a poisoned upstream input. The
AST audit below pins this discipline at the W607-CV layer.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import ast
import json as _json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Canonical W607-CV phase enumeration
# ---------------------------------------------------------------------------


_CV_PHASES = (
    "score_classify",
    "compute_predicate",
    "compute_verdict",
    "serialize_envelope",
)


# ---------------------------------------------------------------------------
# Helpers -- build a tiny git repo with two commits so postmortem has
# something to walk against. cmd_postmortem shells out to ``git log`` /
# ``git show``; we need a real on-disk repo for the integration calls.
# ---------------------------------------------------------------------------


def _make_git_repo(tmp_path: Path) -> Path:
    """Initialise a minimal git repo with two commits."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(tmp_path),
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, env=env)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "c1"], cwd=repo, check=True, env=env)
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "c2"], cwd=repo, check=True, env=env)
    return repo


def _invoke_postmortem(runner: CliRunner, repo: Path, commit_range: str, *extra):
    """Invoke ``roam --json postmortem <range>`` inside ``repo``."""
    import os

    from roam.cli import cli

    args = ["--json", "postmortem", commit_range]
    args.extend(extra)
    cwd = os.getcwd()
    try:
        os.chdir(repo)
        return runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(cwd)


def _extract_json(output: str) -> dict:
    """Pull the JSON envelope out of mixed indexer-progress stdout.

    cmd_postmortem runs ``ensure_index()`` which prints progress lines
    before the envelope. The envelope is the last (or only) parseable
    JSON object in ``output``. Scan from the rightmost ``{`` backward
    for a parseable substring.
    """
    # Try the whole output first (clean case)
    try:
        return _json.loads(output)
    except _json.JSONDecodeError:
        pass
    # Scan for an opening brace and try to parse the rest.
    for idx, ch in enumerate(output):
        if ch != "{":
            continue
        try:
            return _json.loads(output[idx:])
        except _json.JSONDecodeError:
            # Try just up to a newline (single-line envelope)
            tail = output[idx:]
            for end in (tail.find("\n"), len(tail)):
                if end <= 0:
                    continue
                try:
                    return _json.loads(tail[:end])
                except _json.JSONDecodeError:
                    continue
    raise _json.JSONDecodeError("no parseable JSON envelope in output", output, 0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture
def postmortem_repo(tmp_path):
    """Two-commit git repo so postmortem has a non-empty range to walk."""
    return _make_git_repo(tmp_path)


# ---------------------------------------------------------------------------
# (1) Happy path -- envelope omits W607-CV aggregation markers
# ---------------------------------------------------------------------------


def test_postmortem_happy_path_no_w607cv_markers(cli_runner, postmortem_repo):
    """Clean postmortem on a populated range -> no W607-CV aggregation
    markers.

    Hash-stable: an empty W607-CV bucket on the success path must
    produce an envelope without any
    ``postmortem_score_classify_failed:`` /
    ``postmortem_compute_predicate_failed:`` /
    ``postmortem_compute_verdict_failed:`` /
    ``postmortem_serialize_envelope_failed:`` markers (from the CV
    layer).
    """
    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    assert data["command"] == "postmortem"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _CV_PHASES:
        prefix = f"postmortem_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean postmortem must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_cv`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_postmortem_carries_w607cv_accumulator():
    """AST-level guard: cmd_postmortem source carries the W607-CV
    accumulator.

    Pins the canonical W607-CV anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AN) fails
    this guard rather than silently regressing the aggregation-phase
    marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    assert src_path.exists(), f"cmd_postmortem.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "_w607cv_warnings_out" in src, (
        "W607-CV accumulator missing from cmd_postmortem; the additive "
        "aggregation-phase marker plumbing has been removed."
    )
    assert "_run_check_cv" in src, (
        "W607-CV helper ``_run_check_cv`` missing from cmd_postmortem; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_cv is defined inside the command.
    tree = ast.parse(src)
    found_run_check_cv = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_cv":
            found_run_check_cv = True
            break
    assert found_run_check_cv, (
        "W607-CV ``_run_check_cv`` helper not found in cmd_postmortem "
        "AST; the additive aggregation-phase wrapper has been refactored "
        "away."
    )

    # W607-AN must still be present (additive layer does NOT replace it)
    assert "_w607an_warnings_out" in src, (
        "W607-AN accumulator vanished alongside the W607-CV add; the "
        "additive plumbing must preserve the W607-AN substrate-CALL "
        "layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every aggregation-phase boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_aggregation_phase_wrapped_in_run_check_cv():
    """Source-grep guard: every aggregation-phase boundary calls
    ``_run_check_cv(...)`` with the canonical phase name.

    The four phases must appear inside a ``_run_check_cv("<phase>", ...)``
    call inside cmd_postmortem.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _CV_PHASES:
        same_line = f'_run_check_cv("{phase}"' in src
        multi_line = any(f'_run_check_cv(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"postmortem_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-CV wrap missing for phase {phase!r}; aggregation boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) serialize_envelope failure -> floor envelope still ships with marker
# ---------------------------------------------------------------------------


def test_serialize_envelope_failure_marker_format(cli_runner, postmortem_repo, monkeypatch):
    """If ``json_envelope`` raises on the success path, the wrap floors
    to a parseable envelope stub and surfaces
    ``postmortem_serialize_envelope_failed:``.

    A downstream schema-shape refactor that breaks
    ``json_envelope("postmortem", ...)`` would otherwise crash AFTER
    all substrate + aggregation signals were already gathered. The
    consumer must still receive a parseable JSON object with the
    marker attached + the canonical command name.
    """
    from roam.commands import cmd_postmortem as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-serialize-envelope-from-W607-CV")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output

    # Parse the stub document -- must remain parseable JSON.
    data = _extract_json(result.output)
    assert data.get("command") == "postmortem", (
        f"envelope stub must carry the canonical command name on raise; got {data!r}"
    )
    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("postmortem_serialize_envelope_failed:")]
    assert markers, f"expected ``postmortem_serialize_envelope_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) compute_verdict floor is a literal constant -- W978 first-hypothesis
# ---------------------------------------------------------------------------


def test_compute_verdict_floor_is_literal_constant():
    """Pin the W978 discipline anchor: compute_verdict floor must be a
    literal string, NOT an f-string re-interpolating the same values
    that just raised.

    The canonical floor for cmd_postmortem is ``"postmortem completed"``
    (mirror of cmd_audit_trail_export W607-CR's
    ``"audit-trail-export completed"``).
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    src = src_path.read_text(encoding="utf-8")

    assert 'default="postmortem completed"' in src, (
        "W978 compute_verdict floor must be a literal string per W607-CV "
        "discipline; the canonical floor literal 'postmortem completed' "
        "is missing from cmd_postmortem.py"
    )


# ---------------------------------------------------------------------------
# (6) ANY W607-CV marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_marker_flips_partial_success(cli_runner, postmortem_repo, monkeypatch):
    """ANY W607-CV or W607-AN marker must flip
    summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    postmortem" from "postmortem ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_postmortem as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-partial-success-from-W607-CV")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-CV warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607cv_warnings_out_in_both_top_and_summary(cli_runner, postmortem_repo, monkeypatch):
    """Non-empty W607-CV bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-AN contract: top-level is needed because the
    preserved-list field survives ``strip_list_payloads`` in default-
    detail mode; summary mirror gives consumers reading only the summary
    block visibility too.
    """
    from roam.commands import cmd_postmortem as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-mirror-from-W607-CV")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-CV raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-CV raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("postmortem_serialize_envelope_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("postmortem_serialize_envelope_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the serialize_envelope marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) Marker-prefix discipline -- W607-CV uses the SAME ``postmortem_*`` family
# ---------------------------------------------------------------------------


def test_w607cv_marker_prefix_postmortem_family(cli_runner, postmortem_repo, monkeypatch):
    """W607-CV markers use the canonical ``postmortem_*`` prefix (same
    family as W607-AN; W607-CV is ADDITIVE, not a separate prefix).

    Hard guard: any W607-CV marker that leaks into a sibling W607-*
    family (e.g. ``audit_trail_verify_*`` / ``critique_*`` /
    ``pr_replay_*``) breaks the closed-enum marker-family contract.
    """
    from roam.commands import cmd_postmortem as _mod

    def _raise_envelope(*args, **kwargs):
        raise RuntimeError("synthetic-prefix-from-W607-CV")

    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("postmortem_"), (
            f"every W607-CV marker must use the ``postmortem_*`` prefix; got {marker!r}"
        )


# ---------------------------------------------------------------------------
# (9) W607-AN COEXISTENCE -- substrate-CALL + aggregation-phase markers
# coexist in the same family but flow through different buckets
# ---------------------------------------------------------------------------


def test_w607an_substrate_markers_coexist_with_w607cv_aggregation(cli_runner, postmortem_repo, monkeypatch):
    """Confirm ``postmortem_<substrate-phase>_failed:`` markers (W607-AN
    layer) coexist with ``postmortem_<agg-phase>_failed:`` markers
    (W607-CV layer) -- both in same family, but threaded through
    different buckets at envelope-emit.

    The additive aggregation-phase layer must NOT shadow the pre-
    existing substrate-CALL layer; both buckets must combine into the
    same warnings_out channel with marker-prefix disambiguation
    (``postmortem_<substrate-phase>_failed:`` vs
    ``postmortem_<agg-phase>_failed:``).
    """
    from roam.commands import cmd_postmortem as _mod

    # W607-AN substrate boundary -- _diff_for_commit raises
    def _raise_diff(*a, **kw):
        raise RuntimeError("synthetic-an-coexist-diff")

    # W607-CV aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-cv-coexist-envelope")

    monkeypatch.setattr(_mod, "_diff_for_commit", _raise_diff)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-AN (parse_event_payload wraps
    # _diff_for_commit per the cmd_postmortem call site).
    an_markers = [m for m in top_wo if m.startswith("postmortem_parse_event_payload_failed:")]
    # Aggregation-phase from W607-CV
    cv_markers = [m for m in top_wo if m.startswith("postmortem_serialize_envelope_failed:")]

    assert an_markers, f"W607-AN substrate-CALL marker (postmortem_parse_event_payload_failed) missing; got {top_wo!r}"
    assert cv_markers, (
        f"W607-CV aggregation-phase marker (postmortem_serialize_envelope_failed) missing; got {top_wo!r}"
    )

    # Both share the canonical ``postmortem_*`` family
    assert all(m.startswith("postmortem_") for m in (an_markers + cv_markers)), (
        f"all markers must share the canonical ``postmortem_*`` family; got an = {an_markers!r}, cv = {cv_markers!r}"
    )


# ---------------------------------------------------------------------------
# (10) W978 5-discipline AST audit -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants():
    """W978 kwarg-default audit: every W607-CV ``default=`` must be a
    literal constant, NOT computed from upstream values.

    cmd_sbom W607-CG sealed this axis after a regression where
    ``len(_BadDeps())`` defaults eagerly raised inside the ``default=``
    expression -- BEFORE the wrap call entered the try-block. cmd_taint
    W607-CJ added the 5th discipline: ``len()`` lives INSIDE the
    closure, not at the kwarg-bind site.

    AST audit: walk every ``_run_check_cv(...)`` call, extract the
    ``default=`` keyword argument's AST node, confirm it is a Constant
    (literal int/str/bool/None) or a Dict/List/Set/Tuple of Constants.
    Reject any Call, Attribute, Subscript, BinOp, Compare, IfExp, or
    f-string node in the default expression -- these compute from
    upstream values at kwarg-bind time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
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
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cv"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_cv(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_postmortem.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ / cmd_audit_trail_export "
        "W607-CR for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (11) W978 closed-loop -- closures call len() INSIDE, not at kwarg-bind site
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_closures_not_at_kwarg_bind_site():
    """W978 5th-discipline AST guard (cmd_taint W607-CJ anchor): every
    ``len()`` call on a wrapped input MUST live INSIDE the wrapped
    closure, NOT at the ``_run_check_cv(...)`` call site as a positional
    or keyword argument expression.

    A ``_run_check_cv("compute_verdict", _build, len(commits))``-style
    call would evaluate ``len(commits)`` BEFORE the wrap's try-block
    enters; a ``__len__``-poisoned sentinel would escape the wrap and
    crash the command. Source-level audit: confirm no ``_run_check_cv``
    call carries a ``len(...)`` expression in its positional args.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_cv"):
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
                        f"_run_check_cv positional-arg site -- W978 "
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
                        f"_run_check_cv kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_postmortem.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure. See cmd_taint W607-CJ "
        "for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (12) Clean envelope carries run_state from score_classify
# ---------------------------------------------------------------------------


def test_clean_envelope_carries_run_state(cli_runner, postmortem_repo):
    """W607-CV surfaces run_state on the envelope.

    The score_classify closure returns a state label (EMPTY /
    FINDINGS_SURFACED / NO_FINDINGS) which the envelope surfaces so
    consumers can read the run classification without re-deriving from
    raw counts. On a clean populated-range run with no critique findings:
      - summary.run_state == "NO_FINDINGS"
    """
    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    summary = data["summary"]

    assert summary.get("run_state") in {
        "NO_FINDINGS",
        "FINDINGS_SURFACED",
        "EMPTY",
    }, f"run_state missing or invalid on clean envelope; got {summary.get('run_state')!r}"
