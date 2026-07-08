"""W607-DR -- additional substrate-CALL plumbing for ``cmd_postmortem``.

cmd_postmortem is the post-incident analyzer; it walks a commit range,
runs the current critique detector set against each commit's outgoing
diff, and aggregates per-commit findings into a retrospective report.
With W607-DR landed, the full postmortem path is now triple-bucket
plumbed via:

  - substrate-CALL layer #1: W607-AN (6 substrate boundaries:
    load_run_ledger / parse_event_payload / classify_failure /
    aggregate_by_phase / compute_root_cause / aggregate_by_actor)
  - aggregation-phase layer: W607-CV (4 aggregation boundaries:
    score_classify / compute_predicate / compute_verdict /
    serialize_envelope)
  - substrate-CALL layer #2: W607-DR (6 ADDITIONAL substrate
    boundaries: load_index / extract_commit_fields / accumulate_counts /
    combine_warnings_buckets / format_top_hits / render_verdict_line)

All three layers share the canonical ``postmortem_*`` marker family
and the ``postmortem_<phase>_failed:<exc_class>:<detail>`` shape
contract. The three buckets (``_w607an_warnings_out`` +
``_w607cv_warnings_out`` + ``_w607dr_warnings_out``) are combined at
envelope-emit time so consumers see the full degradation lineage in
marker-emission order.

W978 7-discipline check
-----------------------

cmd_taint W607-CJ codified the 5th W978 discipline: move ``len()``
INSIDE the wrapped closure rather than at the kwarg-bind site.
cmd_audit_trail_export W607-CR codified the 7th discipline: use bare
``dict[key]`` lookup when a floor dict guarantees the key, NOT
``dict.get(key, expensive_default)`` -- ``.get`` evaluates default
eagerly at call site. The AST audit below pins both disciplines at
the W607-DR layer.

Cross-prefix isolation
----------------------

All W607-DR markers use the ``postmortem_*`` prefix family (no
``audit_trail_verify_*`` / ``critique_*`` / ``pr_replay_*`` leakage).
This preserves the canonical cmd_postmortem ledger-reader pairing
with cmd_audit_trail_verify (both walk a record-of-history substrate,
but each uses a distinct marker family).

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
# Canonical W607-DR phase enumeration
# ---------------------------------------------------------------------------


_DR_PHASES = (
    "load_index",
    "extract_commit_fields",
    "accumulate_counts",
    "combine_warnings_buckets",
    "format_top_hits",
    "render_verdict_line",
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


def _invoke_postmortem(runner: CliRunner, repo: Path, commit_range: str, *extra, json_mode: bool = True):
    """Invoke ``roam --json postmortem <range>`` inside ``repo``."""
    import os

    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("postmortem")
    args.append(commit_range)
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
    JSON object in ``output``. Scan from the leftmost ``{`` forward for
    a parseable substring.
    """
    try:
        return _json.loads(output)
    except _json.JSONDecodeError:
        pass
    for idx, ch in enumerate(output):
        if ch != "{":
            continue
        try:
            return _json.loads(output[idx:])
        except _json.JSONDecodeError:
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
# (1) Happy path -- envelope omits W607-DR substrate markers
# ---------------------------------------------------------------------------


def test_postmortem_happy_path_no_w607dr_markers(cli_runner, postmortem_repo):
    """Clean postmortem on a populated range -> no W607-DR substrate
    markers.

    Hash-stable: an empty W607-DR bucket on the success path must
    produce an envelope without any ``postmortem_load_index_failed:`` /
    ``postmortem_extract_commit_fields_failed:`` /
    ``postmortem_accumulate_counts_failed:`` /
    ``postmortem_combine_warnings_buckets_failed:`` /
    ``postmortem_format_top_hits_failed:`` /
    ``postmortem_render_verdict_line_failed:`` markers.
    """
    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    assert data["command"] == "postmortem"

    top_wo = data.get("warnings_out") or []
    summary_wo = data["summary"].get("warnings_out") or []
    all_markers = list(top_wo) + list(summary_wo)
    for phase in _DR_PHASES:
        prefix = f"postmortem_{phase}_failed:"
        leaked = [m for m in all_markers if m.startswith(prefix)]
        assert not leaked, f"clean postmortem must NOT surface {prefix} markers; got {leaked!r}"


# ---------------------------------------------------------------------------
# (2) AST-level guard -- the additive ``_run_check_dr`` helper is present
# ---------------------------------------------------------------------------


def test_cmd_postmortem_carries_w607dr_accumulator():
    """AST-level guard: cmd_postmortem source carries the W607-DR
    accumulator AND both prior W607-AN + W607-CV accumulators.

    Pins the canonical W607-DR anchors so a future refactor that removes
    the additive instrumentation (or merges it back into W607-AN /
    W607-CV) fails this guard rather than silently regressing the
    substrate-CALL marker coverage.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    assert src_path.exists(), f"cmd_postmortem.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")

    # Source-level anchors
    assert "w607dr_warnings_out" in src, (
        "W607-DR accumulator missing from cmd_postmortem; the additive substrate-CALL marker plumbing has been removed."
    )
    assert "_run_check_dr" in src, (
        "W607-DR helper ``_run_check_dr`` missing from cmd_postmortem; the additive wrapper has been refactored away."
    )

    # Parse-tree level: confirm _run_check_dr is defined inside the command.
    tree = ast.parse(src)
    found_run_check_dr = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_check_dr":
            found_run_check_dr = True
            break
    assert found_run_check_dr, (
        "W607-DR ``_run_check_dr`` helper not found in cmd_postmortem "
        "AST; the additive substrate-CALL wrapper has been refactored "
        "away."
    )

    # W607-AN must still be present (additive layer does NOT replace it)
    assert "w607an_warnings_out" in src, (
        "W607-AN accumulator vanished alongside the W607-DR add; the "
        "additive plumbing must preserve the W607-AN substrate-CALL "
        "layer."
    )
    # W607-CV must still be present (additive layer does NOT replace it)
    assert "w607cv_warnings_out" in src, (
        "W607-CV accumulator vanished alongside the W607-DR add; the "
        "additive plumbing must preserve the W607-CV aggregation-phase "
        "layer."
    )


# ---------------------------------------------------------------------------
# (3) Source-grep guard -- every W607-DR substrate boundary is wrapped
# ---------------------------------------------------------------------------


def test_every_dr_substrate_phase_wrapped_in_run_check_dr():
    """Source-grep guard: every W607-DR substrate boundary calls
    ``_run_check_dr(...)`` with the canonical phase name.

    The six phases must appear inside a ``_run_check_dr("<phase>", ...)``
    call inside cmd_postmortem.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    src = src_path.read_text(encoding="utf-8")

    for phase in _DR_PHASES:
        same_line = f'_run_check_dr("{phase}"' in src
        multi_line = any(f'_run_check_dr(\n{" " * indent}"{phase}"' in src for indent in (4, 8, 12, 16, 20, 24, 28))
        marker_grep = f"postmortem_{phase}_failed" in src
        assert same_line or multi_line or marker_grep, (
            f"W607-DR wrap missing for phase {phase!r}; substrate boundary is no longer caught."
        )


# ---------------------------------------------------------------------------
# (4) Per-substrate isolation -- extract_commit_fields raise surfaces marker
# ---------------------------------------------------------------------------


def test_extract_commit_fields_failure_marker_format(cli_runner, postmortem_repo, monkeypatch):
    """If the per-commit field extraction raises, the wrap floors to
    safe-default fields and surfaces
    ``postmortem_extract_commit_fields_failed:``.

    Simulated by returning malformed commit dicts (missing 'sha' key)
    from ``_git_log_in_range``. The W607-DR ``extract_commit_fields``
    closure does ``commit["sha"]`` which raises KeyError; the wrap
    catches it and ships the marker.
    """
    from roam.commands import cmd_postmortem as _mod

    def _malformed_log(*a, **kw):
        # Missing 'sha' key -- KeyError inside _extract_commit_fields
        return [{"short_sha": "abc1234", "subject": "x", "author": "T", "date": "2026-01-01"}]

    monkeypatch.setattr(_mod, "_git_log_in_range", _malformed_log)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("postmortem_extract_commit_fields_failed:")]
    assert markers, f"expected ``postmortem_extract_commit_fields_failed:`` marker; got {top_wo!r}"
    assert any("KeyError" in m for m in markers), markers


# ---------------------------------------------------------------------------
# (5) Substrate + aggregation coexistence -- W607-DR + W607-CV markers
# both surface when both layers fault
# ---------------------------------------------------------------------------


def test_w607dr_substrate_coexists_with_w607cv_aggregation(cli_runner, postmortem_repo, monkeypatch):
    """Confirm ``postmortem_<dr-substrate-phase>_failed:`` markers
    (W607-DR layer) coexist with ``postmortem_<cv-agg-phase>_failed:``
    markers (W607-CV layer) -- both in same family, but threaded through
    different buckets at envelope-emit.

    The additive substrate-CALL layer (DR) must NOT shadow the prior
    aggregation-phase layer (CV); both buckets must combine into the
    same warnings_out channel with marker-prefix disambiguation
    (``postmortem_<dr-phase>_failed:`` vs
    ``postmortem_<cv-phase>_failed:``).
    """
    from roam.commands import cmd_postmortem as _mod

    # W607-DR substrate boundary -- extract_commit_fields raises (via
    # malformed log row)
    def _malformed_log(*a, **kw):
        return [{"short_sha": "abc1234", "subject": "x", "author": "T", "date": "2026-01-01"}]

    # W607-CV aggregation boundary -- json_envelope raises
    def _raise_envelope(*a, **kw):
        raise RuntimeError("synthetic-cv-coexist-envelope")

    monkeypatch.setattr(_mod, "_git_log_in_range", _malformed_log)
    monkeypatch.setattr(_mod, "json_envelope", _raise_envelope)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []

    # Substrate-CALL phase from W607-DR
    dr_markers = [m for m in top_wo if m.startswith("postmortem_extract_commit_fields_failed:")]
    # Aggregation-phase from W607-CV
    cv_markers = [m for m in top_wo if m.startswith("postmortem_serialize_envelope_failed:")]

    assert dr_markers, (
        f"W607-DR substrate-CALL marker (postmortem_extract_commit_fields_failed) missing; got {top_wo!r}"
    )
    assert cv_markers, (
        f"W607-CV aggregation-phase marker (postmortem_serialize_envelope_failed) missing; got {top_wo!r}"
    )

    # Both share the canonical ``postmortem_*`` family
    assert all(m.startswith("postmortem_") for m in (dr_markers + cv_markers)), (
        f"all markers must share the canonical ``postmortem_*`` family; got dr = {dr_markers!r}, cv = {cv_markers!r}"
    )


# ---------------------------------------------------------------------------
# (6) ANY W607-DR marker flips partial_success
# ---------------------------------------------------------------------------


def test_any_dr_marker_flips_partial_success(cli_runner, postmortem_repo, monkeypatch):
    """ANY W607-DR marker must flip summary.partial_success=True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    postmortem" from "postmortem ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_postmortem as _mod

    def _malformed_log(*a, **kw):
        return [{"short_sha": "abc1234", "subject": "x", "author": "T", "date": "2026-01-01"}]

    monkeypatch.setattr(_mod, "_git_log_in_range", _malformed_log)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty W607-DR warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (7) warnings_out lands in BOTH top-level AND summary mirror
# ---------------------------------------------------------------------------


def test_w607dr_warnings_out_in_both_top_and_summary(cli_runner, postmortem_repo, monkeypatch):
    """Non-empty W607-DR bucket -> both top-level AND summary.warnings_out
    populated.

    Mirror parity with W607-AN / W607-CV contract: top-level is needed
    because the preserved-list field survives ``strip_list_payloads`` in
    default-detail mode; summary mirror gives consumers reading only
    the summary block visibility too.
    """
    from roam.commands import cmd_postmortem as _mod

    def _malformed_log(*a, **kw):
        return [{"short_sha": "abc1234", "subject": "x", "author": "T", "date": "2026-01-01"}]

    monkeypatch.setattr(_mod, "_git_log_in_range", _malformed_log)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on W607-DR raise path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on W607-DR raise path; got summary = {data['summary']!r}"
    )

    top_markers = [m for m in data["warnings_out"] if m.startswith("postmortem_extract_commit_fields_failed:")]
    summary_markers = [
        m for m in data["summary"]["warnings_out"] if m.startswith("postmortem_extract_commit_fields_failed:")
    ]
    assert top_markers and summary_markers, (
        f"both mirrors must carry the extract_commit_fields marker; "
        f"top = {data.get('warnings_out')!r}, "
        f"summary = {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (8) Cross-prefix isolation -- W607-DR markers stay in postmortem_* family
# ---------------------------------------------------------------------------


def test_w607dr_marker_prefix_postmortem_family(cli_runner, postmortem_repo, monkeypatch):
    """W607-DR markers use the canonical ``postmortem_*`` prefix (same
    family as W607-AN + W607-CV; W607-DR is ADDITIVE, not a separate
    prefix).

    Hard guard: any W607-DR marker that leaks into a sibling W607-*
    family (e.g. ``audit_trail_verify_*`` / ``critique_*`` /
    ``pr_replay_*``) breaks the closed-enum marker-family contract.
    """
    from roam.commands import cmd_postmortem as _mod

    def _malformed_log(*a, **kw):
        return [{"short_sha": "abc1234", "subject": "x", "author": "T", "date": "2026-01-01"}]

    monkeypatch.setattr(_mod, "_git_log_in_range", _malformed_log)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)
    top_wo = data.get("warnings_out") or []
    failure_markers = [m for m in top_wo if "_failed:" in m]
    assert failure_markers, "expected non-empty failure-marker bucket for prefix-discipline check"
    for marker in failure_markers:
        assert marker.startswith("postmortem_"), (
            f"every W607-DR marker must use the ``postmortem_*`` prefix; got {marker!r}"
        )

    # Verify NO cross-prefix leakage into sibling W607 families
    forbidden_prefixes = (
        "audit_trail_verify_",
        "critique_",
        "pr_replay_",
        "preflight_",
        "diagnose_",
        "dead_",
        "bus_factor_",
    )
    for marker in failure_markers:
        for forbidden in forbidden_prefixes:
            assert not marker.startswith(forbidden), (
                f"W607-DR marker leaked into sibling family {forbidden!r}; got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (9) AST-scan source pinning all three accumulators
# ---------------------------------------------------------------------------


def test_all_three_warnings_out_accumulators_present_in_ast():
    """AST-scan source pinning: cmd_postmortem must carry all three
    accumulators (``_w607an_warnings_out`` / ``_w607cv_warnings_out`` /
    ``_w607dr_warnings_out``) as local-variable assignments inside the
    ``postmortem_cmd`` function body.

    Triple-layer plumbing contract: AN substrate + CV aggregation + DR
    additional substrate. A refactor that drops any of them silently
    must be caught here.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_an = found_cv = found_dr = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if node.target.id == "_w607an_warnings_out":
            found_an = True
        elif node.target.id == "_w607cv_warnings_out":
            found_cv = True
        elif node.target.id == "_w607dr_warnings_out":
            found_dr = True

    assert found_an, "W607-AN substrate-CALL accumulator (``_w607an_warnings_out``) missing from cmd_postmortem AST."
    assert found_cv, "W607-CV aggregation-phase accumulator (``_w607cv_warnings_out``) missing from cmd_postmortem AST."
    assert found_dr, (
        "W607-DR additional substrate-CALL accumulator (``_w607dr_warnings_out``) missing from cmd_postmortem AST."
    )


# ---------------------------------------------------------------------------
# (10) W978 5-discipline AST audit -- floors are literal constants
# ---------------------------------------------------------------------------


def test_w978_kwarg_default_floors_are_literal_constants_dr():
    """W978 kwarg-default audit: every W607-DR ``default=`` must be a
    literal constant, NOT computed from upstream values.

    cmd_sbom W607-CG sealed this axis after a regression where
    ``len(_BadDeps())`` defaults eagerly raised inside the ``default=``
    expression -- BEFORE the wrap call entered the try-block. cmd_taint
    W607-CJ added the 5th discipline: ``len()`` lives INSIDE the
    closure, not at the kwarg-bind site.

    AST audit: walk every ``_run_check_dr(...)`` call, extract the
    ``default=`` keyword argument's AST node, confirm it is a Constant
    (literal int/str/bool/None) or a Dict/List/Set/Tuple of Constants
    or a bare Name reference (variable bound BEFORE the wrap call).
    Reject any Call, Attribute, Subscript, BinOp, Compare, IfExp, or
    f-string node in the default expression -- these compute from
    upstream values at kwarg-bind time.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_literal(node) -> bool:
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
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dr"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not _is_literal(kw.value):
                violations.append(
                    f"line {kw.value.lineno}: non-literal default= expression in _run_check_dr(...) -- W978 violation"
                )

    assert not violations, (
        "W978 kwarg-default eagerness trap detected in cmd_postmortem.py:\n"
        + "\n".join(violations)
        + "\nFloor expressions in default= MUST be literal constants. "
        "See cmd_sbom W607-CG / cmd_taint W607-CJ / cmd_audit_trail_export "
        "W607-CR for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (11) W978 5th-discipline -- len() lives INSIDE the closure, not at
# the kwarg-bind site
# ---------------------------------------------------------------------------


def test_w978_len_calls_live_inside_dr_closures_not_at_kwarg_bind_site():
    """W978 5th-discipline AST guard (cmd_taint W607-CJ anchor): every
    ``len()`` call on a wrapped input MUST live INSIDE the wrapped
    closure, NOT at the ``_run_check_dr(...)`` call site as a positional
    or keyword argument expression.

    A ``_run_check_dr("format_top_hits", _fmt, len(per_commit))``-style
    call would evaluate ``len(per_commit)`` BEFORE the wrap's try-block
    enters; a ``__len__``-poisoned sentinel would escape the wrap and
    crash the command. Source-level audit: confirm no ``_run_check_dr``
    call carries a ``len(...)`` expression in its positional args.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_check_dr"):
            continue
        for sub in node.args:
            for descendant in ast.walk(sub):
                if (
                    isinstance(descendant, ast.Call)
                    and isinstance(descendant.func, ast.Name)
                    and descendant.func.id == "len"
                ):
                    violations.append(
                        f"line {descendant.lineno}: len() call at "
                        f"_run_check_dr positional-arg site -- W978 "
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
                        f"_run_check_dr kwarg={kw.arg!r} -- W978 "
                        f"5th-discipline violation"
                    )
    assert not violations, (
        "W978 5th-discipline violations in cmd_postmortem.py:\n"
        + "\n".join(violations)
        + "\nMove len() INSIDE the wrapped closure. See cmd_taint W607-CJ "
        "for the canonical fix pattern."
    )


# ---------------------------------------------------------------------------
# (12) HMAC-failure-aborts-write invariant preserved
# ---------------------------------------------------------------------------


def test_hmac_invariant_preserved_postmortem_does_not_write_ledger():
    """HMAC-failure-aborts-write invariant: cmd_postmortem is a READER
    of the git-log + critique pipeline, NOT a writer to the runs/ ledger.

    The canonical cmd_runs invariant is "HMAC-chained per-run event
    ledger; a failed verify aborts the write." cmd_postmortem only
    READS history (git log) and runs critique on diffs -- it MUST NOT
    write to ``.roam/runs/``. Verify by AST-scanning the source:
    confirm no ``RunsStore.append`` / ``record_event`` / ``write_event``
    calls exist in cmd_postmortem.
    """
    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_postmortem.py"
    src = src_path.read_text(encoding="utf-8")

    forbidden_writers = (
        "RunsStore.append",
        "record_event",
        "write_event",
        "append_event",
        "runs_store.append",
        ".write_text(",  # would write a ledger file
    )
    for token in forbidden_writers:
        # Acceptable cases: .write_text appears only in test/sample data,
        # not in cmd_postmortem itself. The check guards against new
        # write calls being added inadvertently.
        if token == ".write_text(":
            # cmd_postmortem must not write to disk (no .write_text calls)
            assert token not in src, (
                f"cmd_postmortem must NOT write to disk; found {token!r} "
                f"in source -- HMAC-aborts-write invariant violated."
            )
            continue
        assert token not in src, (
            f"cmd_postmortem must NOT write to the runs/ ledger; "
            f"found {token!r} in source -- HMAC-aborts-write invariant "
            f"violated. cmd_postmortem is a READER, not a writer."
        )


# ---------------------------------------------------------------------------
# (13) Phase-name collision check -- no overlap with W607-AN / W607-CV
# ---------------------------------------------------------------------------


def test_w607dr_phase_names_no_collision_with_w607an_or_w607cv():
    """Phase-name collision check: W607-DR phase names MUST NOT overlap
    with W607-AN substrate phases or W607-CV aggregation phases.

    AN phases:  load_run_ledger / parse_event_payload / classify_failure /
                aggregate_by_phase / compute_root_cause / aggregate_by_actor
    CV phases:  score_classify / compute_predicate / compute_verdict /
                serialize_envelope
    DR phases:  load_index / extract_commit_fields / accumulate_counts /
                combine_warnings_buckets / format_top_hits /
                render_verdict_line

    All three sets must be disjoint so the per-phase marker prefix is
    unambiguous.
    """
    an_phases = frozenset(
        {
            "load_run_ledger",
            "parse_event_payload",
            "classify_failure",
            "aggregate_by_phase",
            "compute_root_cause",
            "aggregate_by_actor",
        }
    )
    cv_phases = frozenset(
        {
            "score_classify",
            "compute_predicate",
            "compute_verdict",
            "serialize_envelope",
        }
    )
    dr_phases = frozenset(_DR_PHASES)

    overlap_an = an_phases & dr_phases
    overlap_cv = cv_phases & dr_phases
    assert not overlap_an, f"W607-DR phase collision with W607-AN substrate phases: {sorted(overlap_an)!r}"
    assert not overlap_cv, f"W607-DR phase collision with W607-CV aggregation phases: {sorted(overlap_cv)!r}"


# ---------------------------------------------------------------------------
# (14) Each substrate phase isolation -- accumulate_counts raise surfaces marker
# ---------------------------------------------------------------------------


def test_accumulate_counts_failure_marker_format(cli_runner, postmortem_repo, monkeypatch):
    """If ``_summarize_finding_count`` returns a __add__-poisoned tuple,
    the W607-DR ``accumulate_counts`` closure raises and the wrap floors
    to the running totals unchanged + surfaces
    ``postmortem_accumulate_counts_failed:``.
    """
    from roam.commands import cmd_postmortem as _mod

    class _PoisonInt:
        def __add__(self, other):
            raise TypeError("synthetic-poison-add")

        def __radd__(self, other):
            raise TypeError("synthetic-poison-radd")

    def _poison_counts(*a, **kw):
        return (_PoisonInt(), _PoisonInt(), 0)

    monkeypatch.setattr(_mod, "_summarize_finding_count", _poison_counts)

    result = _invoke_postmortem(cli_runner, postmortem_repo, "HEAD~1..HEAD")
    assert result.exit_code == 0, result.output
    data = _extract_json(result.output)

    top_wo = data.get("warnings_out") or []
    markers = [m for m in top_wo if m.startswith("postmortem_accumulate_counts_failed:")]
    assert markers, f"expected ``postmortem_accumulate_counts_failed:`` marker; got {top_wo!r}"
    assert any("TypeError" in m for m in markers), markers
