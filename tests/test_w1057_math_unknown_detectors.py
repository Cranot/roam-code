"""W1057: Pattern-1D + Pattern-2 fix for cmd_math --only/--exclude silent-no-op.

Pre-W1057 bug: `roam math --only typo-name` built `only_set = {"typo-name"}`,
applied a strict `fn_name in only_set` filter against `_DETECTOR_REGISTRY`,
and silently ran zero detectors — emitting a "no findings" success verdict
indistinguishable from a clean codebase (CLAUDE.md Pattern 1 variant D +
Pattern 2 silent fallback).

The fix mirrors the W4719 `framework_unknown` precedent: diff user-supplied
names against the registry-derived authoritative set, surface unknowns in
``meta["only_unknown"]`` / ``meta["exclude_unknown"]``, and flip
``summary.partial_success`` + ``warnings_count`` when any unknown was passed.
"""

from __future__ import annotations

import sqlite3

from roam.catalog.detectors import _DETECTOR_REGISTRY, run_detectors
from roam.db.connection import ensure_schema


def _empty_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _known_detector_names() -> list[str]:
    """Pick two registered detectors so the tests don't bit-rot if names
    change. Registry-derived: same source of truth as the fix uses."""
    names = sorted(_DETECTOR_REGISTRY.keys())
    assert len(names) >= 2, "registry too small for W1057 tests"
    return [names[0], names[1]]


def test_only_all_known_no_warnings_and_no_unknown_keys():
    """--only with all-known names runs the filtered detectors cleanly.

    Happy path: ``meta["only_unknown"]`` is present (because --only was set)
    but is an empty list; no warnings; partial_success not asserted at the
    detector layer (the cmd_math wrapper owns the summary)."""
    conn = _empty_conn()
    try:
        known = _known_detector_names()
        findings, meta = run_detectors(
            conn,
            return_meta=True,
            only=tuple(known),
        )
        assert meta["only_unknown"] == []
        # exclude not supplied → key omitted entirely (byte-stable envelope).
        assert "exclude_unknown" not in meta
        assert isinstance(findings, list)
    finally:
        conn.close()


def test_only_mixed_known_and_unknown_surfaces_unknown_only():
    """--only with mixed names: unknown surfaces in meta; known still run."""
    conn = _empty_conn()
    try:
        known = _known_detector_names()
        findings, meta = run_detectors(
            conn,
            return_meta=True,
            only=(known[0], "totally-not-a-real-detector"),
        )
        assert meta["only_unknown"] == ["totally-not-a-real-detector"]
        # At least one detector still got past the filter (the known name).
        # We can't assert detectors_executed > 0 on an empty in-memory DB
        # without symbol rows, but the executed counter must be >= the
        # number of known names matched (registry-only filter).
        assert meta["detectors_executed"] >= 1
        assert isinstance(findings, list)
    finally:
        conn.close()


def test_only_all_unknown_surfaces_unknown_and_runs_zero_detectors():
    """All-unknown --only: filter-to-zero scenario, surfaced via meta.

    This is the exact silent-no-op the bug allowed. Post-fix: zero detectors
    run AND the unknown names are visible in ``meta["only_unknown"]`` so the
    cmd_math caller can flip ``partial_success`` and emit a warning."""
    conn = _empty_conn()
    try:
        findings, meta = run_detectors(
            conn,
            return_meta=True,
            only=("typo-one", "typo-two"),
        )
        assert sorted(meta["only_unknown"]) == ["typo-one", "typo-two"]
        # Filter-to-zero: every detector was skipped because no name matched
        # the registry. The envelope is still emitted (no crash, no silent
        # zero verdict).
        assert meta["detectors_executed"] == 0
        assert findings == []
    finally:
        conn.close()


def test_exclude_unknown_surfaces_in_meta_but_remaining_detectors_still_run():
    """--exclude with unknown name: surface unknown, but remaining run."""
    conn = _empty_conn()
    try:
        findings, meta = run_detectors(
            conn,
            return_meta=True,
            exclude=("totally-not-a-real-detector",),
        )
        assert meta["exclude_unknown"] == ["totally-not-a-real-detector"]
        # only not supplied → key omitted entirely.
        assert "only_unknown" not in meta
        # Unknown exclude names don't drop ANY real detector, so the full
        # registry runs.
        assert meta["detectors_executed"] >= len(_DETECTOR_REGISTRY)
        assert isinstance(findings, list)
    finally:
        conn.close()


def test_default_path_no_only_or_exclude_is_byte_identical_to_pre_w1057():
    """No --only/--exclude: no new meta keys, no warnings.

    Hard invariant from the W1057 task brief — default path MUST be
    byte-identical to pre-W1057 envelopes for replay determinism."""
    conn = _empty_conn()
    try:
        findings, meta = run_detectors(conn, return_meta=True)
        # Neither key may appear on the default path. If either leaks, the
        # envelope hash changes and downstream replay diffs break.
        assert "only_unknown" not in meta
        assert "exclude_unknown" not in meta
        assert isinstance(findings, list)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI-level verification: ensure the cmd_math wrapper folds the unknown lists
# into ``summary.partial_success`` + ``warnings_count`` and emits NOTE on
# stderr. Mirrors the W706 suppression-warning discipline.
# ---------------------------------------------------------------------------


def test_cli_only_unknown_emits_partial_success_and_warning(tmp_path, monkeypatch):
    """End-to-end: `roam --json math --only typo` produces partial_success."""
    import json
    import subprocess

    from click.testing import CliRunner

    from roam.cli import cli

    # Build a minimal git-tracked repo. `roam init` refuses to run outside
    # a git tree, so we git-init + commit one file before indexing.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
    )

    monkeypatch.chdir(repo)
    runner = CliRunner()
    init_result = runner.invoke(cli, ["init"], catch_exceptions=False)
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(
        cli,
        ["--json", "math", "--only", "totally-not-a-real-detector"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    raw = getattr(result, "stdout", None) or result.output
    payload = json.loads(raw)
    summary = payload["summary"]

    # Unknown name surfaces on the summary AND flips partial_success.
    assert summary.get("only_unknown") == ["totally-not-a-real-detector"]
    assert summary.get("partial_success") is True
    assert summary.get("warnings_count", 0) >= 1
    # Envelope carries the actionable warning string for the agent.
    assert any("--only" in w and "totally-not-a-real-detector" in w for w in payload.get("warnings_out", []))


# ---------------------------------------------------------------------------
# W1064 — difflib closest-match `did you mean` suggestions on unknown names.
# Augments the W1057 warning string; does NOT replace it.
# ---------------------------------------------------------------------------


def _make_indexed_repo(tmp_path):
    """Build a minimal git-tracked + roam-indexed repo for CLI tests.

    Factored so the W1064 CLI tests can reuse the bootstrap that the W1057
    end-to-end test already validated above."""
    import subprocess

    from click.testing import CliRunner

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    runner = CliRunner()
    return repo, runner


def _math_json(runner, *args):
    """Invoke `roam --json math <args>` and parse the envelope."""
    import json

    from roam.cli import cli

    result = runner.invoke(cli, ["--json", "math", *args], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    raw = getattr(result, "stdout", None) or result.output
    return result, json.loads(raw)


def test_w1064_only_close_match_emits_did_you_mean(tmp_path, monkeypatch):
    """`--only detect_busy_waitt` typo surfaces a did-you-mean for the real name.

    Anchors on `detect_busy_wait` which is one character off — well within the
    0.6 cutoff. The suggestion fragment must reference the real detector AND
    quote the unknown name so the agent sees which input it's a hint for."""
    repo, runner = _make_indexed_repo(tmp_path)
    monkeypatch.chdir(repo)
    init = runner.invoke(__import__("roam.cli", fromlist=["cli"]).cli, ["init"], catch_exceptions=False)
    assert init.exit_code == 0, init.output

    _, payload = _math_json(runner, "--only", "detect_busy_waitt")
    warnings = payload.get("warnings_out", [])
    assert warnings, "expected at least one warning for unknown --only name"
    only_warning = next((w for w in warnings if "--only" in w), None)
    assert only_warning is not None, warnings
    # The unknown name + the close match must both appear in the warning.
    assert "detect_busy_waitt" in only_warning
    assert "Did you mean" in only_warning
    assert "detect_busy_wait" in only_warning


def test_w1064_no_close_match_omits_did_you_mean(tmp_path, monkeypatch):
    """Random gibberish far from every detector name yields no suggestion.

    Locks the cutoff in: a name with no near-neighbour above 0.6 must NOT
    spam an irrelevant `did you mean`. The base W1057 warning still fires."""
    repo, runner = _make_indexed_repo(tmp_path)
    monkeypatch.chdir(repo)
    init = runner.invoke(__import__("roam.cli", fromlist=["cli"]).cli, ["init"], catch_exceptions=False)
    assert init.exit_code == 0, init.output

    _, payload = _math_json(runner, "--only", "xyzzy_completely_random_qqq")
    warnings = payload.get("warnings_out", [])
    only_warning = next((w for w in warnings if "--only" in w), None)
    assert only_warning is not None, warnings
    # Base W1057 surface still fires.
    assert "xyzzy_completely_random_qqq" in only_warning
    # But NO did-you-mean fragment — cutoff suppressed it.
    assert "Did you mean" not in only_warning


def test_w1064_exclude_close_match_also_emits_did_you_mean(tmp_path, monkeypatch):
    """`--exclude` mirrors `--only`: closest-match suggestions on typos.

    Regression-guard the parity between the two flags so a future refactor
    that touches only one path doesn't drift them apart."""
    repo, runner = _make_indexed_repo(tmp_path)
    monkeypatch.chdir(repo)
    init = runner.invoke(__import__("roam.cli", fromlist=["cli"]).cli, ["init"], catch_exceptions=False)
    assert init.exit_code == 0, init.output

    _, payload = _math_json(runner, "--exclude", "detect_linerar_search")
    warnings = payload.get("warnings_out", [])
    exclude_warning = next((w for w in warnings if "--exclude" in w), None)
    assert exclude_warning is not None, warnings
    assert "detect_linerar_search" in exclude_warning
    assert "Did you mean" in exclude_warning
    assert "detect_linear_search" in exclude_warning
