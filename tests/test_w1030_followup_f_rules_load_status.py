"""W1030-followup-F — surface directory-level ``LoadStatus`` rollup on
``cmd_rules`` envelope via the library-level
:func:`roam.rules.engine.load_rules_with_status`.

W1030-followup-A landed on ``cmd_alerts`` + ``cmd_budget``.
W1030-followup-B landed on ``cmd_health --gate``.
W1030-followup-C landed on ``cmd_check_rules`` (single ``.roam-rules.yml``,
3-loader worst-status rollup).
W1030-followup-D landed on ``cmd_fitness`` (single ``.roam/fitness.yaml``).
W1030-followup-E correctly STOPPED on ``cmd_adrs`` (Markdown-frontmatter,
not YAML-config).

This wave (W1030-followup-F) migrates the sixth tier-2 caller —
``rules/engine.py``, which loads a *directory* of YAML rule files
(``.roam/rules/*.yaml``) rather than a single config file. The status
model therefore aggregates per-file ``LoadStatus`` values via a
worst-status rollup (sibling of cmd_check_rules's 3-loader rollup) so
agents reading the ``cmd_rules`` envelope can disambiguate:

* ``missing`` — no ``.roam/rules/`` directory configured (use baseline /
  community rules silently).
* ``empty_file`` — directory exists but contains no ``.yaml`` / ``.yml``
  files (stub directory; user probably meant to write rules).
* ``empty_yaml`` — at least one file is comments-only.
* ``parse_error`` / ``wrong_root_type`` / ``read_error`` /
  ``schema_invalid`` — at least one file is broken (canonical loader
  emitted a warning + ``partial_success=True``).
* ``ok`` — every file parsed cleanly.

Library-level migration: :func:`_load_yaml_with_status` exposes a new
``(data, status)`` return; the legacy :func:`_load_yaml` wraps it and
keeps the bare ``data`` return for byte-identical pre-W1030-followup-F
callers. :func:`load_rules_with_status` is the new directory entry
point; the legacy :func:`load_rules` wraps it. :func:`evaluate_all_with_status`
is the all-in-one (load + evaluate) entry point used by ``cmd_rules``;
the legacy :func:`evaluate_all` wraps it.

Cross-links:
- W1030 — ``return_status=True`` substrate + 14-test contract pin.
- W1030-followup-A / -B / -C / -D — sibling waves.
- W1030-followup-E — cmd_adrs no-op (Markdown-frontmatter, not YAML).
- W1036 — ``_load_yaml`` previous migration to canonical helper.
- ``src/roam/rules/engine.py`` — library-side migration.
- ``src/roam/commands/cmd_rules.py`` — envelope-side wiring.
- ``tests/test_rules_engine_warnings_out.py`` — pre-existing
  ``warnings_out`` regression baseline (MUST stay green).
- CLAUDE.md "Make fallback chains loud" — the lineage rule.
"""

from __future__ import annotations

import ast as _ast
import json as _json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli as roam_cli
from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_git_repo(cwd: Path) -> None:
    """Initialise a minimal git repo so ``roam init`` accepts the tree."""
    subprocess.run(["git", "init", "-q", str(cwd)], check=False)
    subprocess.run(["git", "-C", str(cwd), "config", "user.email", "t@t"], check=False)
    subprocess.run(["git", "-C", str(cwd), "config", "user.name", "t"], check=False)


def _git_add_commit(cwd: Path) -> None:
    subprocess.run(["git", "-C", str(cwd), "add", "-A"], check=False)
    subprocess.run(["git", "-C", str(cwd), "commit", "-m", "init", "-q"], check=False)


def _extract_envelope(output: str) -> dict:
    """Pull the trailing JSON envelope out of mixed stdout."""
    lines = output.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].startswith("{"):
            return _json.loads("\n".join(lines[idx:]))
    raise AssertionError(f"no JSON envelope found in output:\n{output}")


def _make_indexed_rules_project(tmp_path: Path, *, rules_files: dict[str, str] | None = None) -> Path:
    """Create a minimal git+indexed project with optional ``.roam/rules/*.yaml`` files.

    ``rules_files`` is a mapping of filename -> body. Pass ``None`` for the
    "no rules dir" / missing path; pass an empty dict to create an empty
    rules directory (the ``empty_file`` rollup state). Pass actual filenames
    for the ok / parse_error paths.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "sample.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    _setup_git_repo(proj)

    if rules_files is not None:
        rules_dir = proj / ".roam" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        for name, body in rules_files.items():
            (rules_dir / name).write_text(body, encoding="utf-8")

    _git_add_commit(proj)
    # Index in-process via the CliRunner so the CWD is set correctly.
    runner = CliRunner()
    result = runner.invoke(roam_cli, ["init"])
    # ``init`` may exit 0 (fresh index) or non-zero on prior-run schema
    # checks; both are acceptable here -- we only need the index to exist.
    assert result.exit_code in (0, 1), f"init failed:\n{result.output}"
    return proj


def _rules_envelope(proj: Path) -> dict:
    """Invoke ``roam --json rules`` from ``proj`` and parse envelope.

    Without ``--ci`` set, rules exits 0 even on error-severity violations,
    so the envelope is reliably parseable from stdout.
    """
    runner = CliRunner()
    # CliRunner needs CWD set via subprocess for find_project_root to work.
    # Use the with-context-manager pattern from test_rules_engine_warnings_out.
    import os

    old_cwd = os.getcwd()
    try:
        os.chdir(proj)
        result = runner.invoke(roam_cli, ["--json", "rules"])
    finally:
        os.chdir(old_cwd)
    assert result.exit_code in (0, 1), f"rules failed:\n{result.output}"
    return _extract_envelope(result.output)


# ---------------------------------------------------------------------------
# Envelope surfacing: ``roam --json rules``
# ---------------------------------------------------------------------------


def test_envelope_has_config_state_field(tmp_path: Path) -> None:
    """The rules envelope ALWAYS carries ``summary.config_state``.

    Pin the contract: even on the happy path (no rules dir -> missing) the
    field is present so downstream consumers don't have to ``.get(...)``
    with a default.
    """
    proj = _make_indexed_rules_project(tmp_path, rules_files=None)
    data = _rules_envelope(proj)
    summary = data["summary"]
    assert "config_state" in summary, (
        f"summary.config_state field MUST be present on the rules envelope, got summary: {summary!r}"
    )


def test_missing_config_emits_missing_state(tmp_path: Path) -> None:
    """No ``.roam/rules/`` directory -> ``config_state == "missing"``.

    Missing config is the default state — never a degraded run.
    """
    proj = _make_indexed_rules_project(tmp_path, rules_files=None)
    assert not (proj / ".roam" / "rules").exists()
    data = _rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "missing", (
        f"Expected config_state='missing' on absent .roam/rules/, got: {summary!r}"
    )
    assert summary.get("partial_success") is not True, (
        f"Missing rules dir is NOT a degradation; partial_success must not be True, got summary: {summary!r}"
    )


def test_empty_file_emits_empty_file_state(tmp_path: Path) -> None:
    """Empty ``.roam/rules/`` directory -> ``config_state == "empty_file"``.

    User created the directory but didn't write any rule files. Baseline
    rules apply silently — the envelope flags the empty stub.
    """
    proj = _make_indexed_rules_project(tmp_path, rules_files={})  # empty dict -> empty dir
    data = _rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "empty_file", (
        f"Expected config_state='empty_file' on empty .roam/rules/ dir, got: {summary!r}"
    )
    # empty_file is not a degradation -> partial_success stays unset.
    assert summary.get("partial_success") is not True


def test_valid_config_emits_ok_state(tmp_path: Path) -> None:
    """Well-formed ``.roam/rules/*.yaml`` -> ``config_state == "ok"``.

    Happy path: no warnings, no partial_success.
    """
    proj = _make_indexed_rules_project(
        tmp_path,
        rules_files={
            "rule1.yaml": 'name: "Some rule"\nseverity: warning\nmatch:\n  kind: function\n',
        },
    )
    data = _rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "ok", (
        f"Expected config_state='ok' on valid .roam/rules/*.yaml, got: {summary!r}"
    )
    assert summary.get("partial_success") is not True
    # No loader warnings on a well-formed corpus.
    assert not summary.get("warnings_out")


def test_parse_error_emits_parse_error_state(tmp_path: Path) -> None:
    """Malformed ``.roam/rules/*.yaml`` -> ``config_state == "parse_error"`` +
    ``partial_success=True``.

    Verifies W1036 didn't regress: the canonical loader emits a warning,
    the envelope flips partial_success, and ``warnings_out`` carries the
    actionable diagnostic.
    """
    proj = _make_indexed_rules_project(
        tmp_path,
        rules_files={
            "bad.yaml": "name: bad\nmatch: [unterminated\n",
        },
    )
    data = _rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "parse_error", (
        f"Expected config_state='parse_error' on malformed .roam/rules/, got: {summary!r}"
    )
    assert summary.get("partial_success") is True, (
        f"Malformed rule file MUST flip partial_success=True so agents see the config was discarded, got: {summary!r}"
    )
    warnings_field = summary.get("warnings_out", [])
    assert warnings_field, f"Malformed rule file MUST emit a warning on warnings_out, got: {warnings_field!r}"


def test_partial_success_flips_on_degraded_state(tmp_path: Path) -> None:
    """``partial_success`` is True for every degraded ``config_state``.

    Drives the broadened flip rule (parse_error / wrong_root_type /
    read_error / schema_invalid). Cross-checks the cmd_alerts +
    cmd_budget + cmd_health + cmd_check_rules + cmd_fitness vocabulary
    alignment.
    """
    # Use a parse_error fixture; the rollup at the directory level
    # produces "parse_error" status which is in _DEGRADED_LOAD_STATUSES.
    proj = _make_indexed_rules_project(
        tmp_path,
        rules_files={
            "bad.yaml": "name: bad\nmatch: [unterminated\n",
        },
    )
    data = _rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "parse_error"
    assert summary.get("partial_success") is True, (
        f"parse_error MUST flip partial_success=True, got summary: {summary!r}"
    )


def test_agent_contract_facts_includes_state_disclosure(tmp_path: Path) -> None:
    """``agent_contract.facts`` carries the state-disclosure line.

    LAW 4 anchored on the concrete-noun terminal ``"rules"``. Mirrors
    cmd_check_rules (anchors on "rules"), cmd_fitness (anchors on
    "rules"), cmd_alerts (anchors on "defaults"), and cmd_health
    (anchors on "gates") by using the command's own subject-noun.
    """
    proj = _make_indexed_rules_project(tmp_path, rules_files=None)
    data = _rules_envelope(proj)
    contract = data.get("agent_contract")
    assert contract is not None, f"agent_contract MUST be emitted when config_state is disclosed, got: {data!r}"
    facts = contract.get("facts", [])
    assert any("no .roam/rules/" in f for f in facts), (
        f"missing state MUST disclose 'no .roam/rules/ directory configured; "
        f"using baseline rules', got facts: {facts!r}"
    )
    # LAW 4: terminal anchor on concrete noun ("rules").
    state_fact = next(f for f in facts if "no .roam/rules/" in f)
    assert state_fact.rstrip(".").endswith("rules"), (
        f"LAW 4: state fact must terminate on concrete-noun anchor ('rules'), got: {state_fact!r}"
    )


def test_state_field_subset_of_LOAD_STATUSES(tmp_path: Path) -> None:
    """``config_state`` is always a member of :data:`LOAD_STATUSES`. Drift guard.

    Pins the cross-command vocabulary uniformity that
    W1030-followup-A/B/C/D established. cmd_rules joins the cohort here.
    """
    from roam.commands._yaml_loader import LOAD_STATUSES

    proj = _make_indexed_rules_project(tmp_path, rules_files=None)
    data = _rules_envelope(proj)
    state = data["summary"].get("config_state")
    assert state in LOAD_STATUSES, f"config_state {state!r} must be a member of LOAD_STATUSES {LOAD_STATUSES!r}"


def test_legacy_engine_wrapper_returns_rules_only(tmp_path: Path) -> None:
    """Legacy ``load_rules(...) -> list[dict]`` stays byte-identical.

    Pre-W1030-followup-F callers (the W1036 ``warnings_out`` tests,
    every consumer that doesn't care about the directory-level state)
    MUST see a plain ``list`` return — never a tuple. AST-scan to make
    sure the public symbol still exists with the right shape.
    """
    from roam.rules.engine import load_rules

    # Missing directory -> empty list.
    result = load_rules(tmp_path / "nonexistent")
    assert isinstance(result, list)
    assert result == []

    # Present file with a rule -> list of one dict.
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "rule1.yaml").write_text(
        'name: "Some rule"\nseverity: error\nmatch:\n  kind: function\n',
        encoding="utf-8",
    )
    result = load_rules(rules_dir)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "Some rule"

    # AST-scan: load_rules MUST still be defined as a public callable
    # taking a path arg. Catch a future refactor that accidentally
    # makes it tuple-returning.
    src = (repo_root() / "src" / "roam" / "rules" / "engine.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    found_load_rules = False
    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name == "load_rules":
            found_load_rules = True
            # The legacy entry point should not be annotated as a tuple
            # return — that's the W1030-followup-F signal that it's been
            # accidentally re-typed.
            if node.returns is not None:
                ret_src = _ast.unparse(node.returns)
                assert "tuple" not in ret_src.lower(), (
                    f"load_rules return annotation must NOT be a tuple; "
                    f"got {ret_src!r}. The W1030-followup-F wrapper keeps "
                    f"the legacy list[dict] return for byte-identical "
                    f"pre-W1030 callers."
                )
            break
    assert found_load_rules, "load_rules public callable went missing in engine.py"


def test_empty_file_no_spurious_missing_key_warning(tmp_path: Path) -> None:
    """Empty ``.roam/rules/`` directory -> no spurious warnings.

    Mirror of cmd_budget + cmd_health + cmd_check_rules + cmd_fitness
    W1030-followup behaviour: the empty-stub state is its own disclosure
    surface (``config_state=empty_file``), so any "no rules found" /
    "no `rules:` key" warning would just confuse agents.
    """
    proj = _make_indexed_rules_project(tmp_path, rules_files={})
    data = _rules_envelope(proj)
    summary = data["summary"]
    assert summary.get("config_state") == "empty_file"
    warnings_field = summary.get("warnings_out", []) or []
    assert warnings_field == [], f"empty_file short-circuit failed — spurious warning emitted: {warnings_field!r}"


# ---------------------------------------------------------------------------
# Direct library-level: load_rules_with_status + _load_yaml_with_status
# ---------------------------------------------------------------------------


def test_with_status_missing(tmp_path: Path) -> None:
    """Absent directory -> ``([], "missing")``."""
    from roam.rules.engine import load_rules_with_status

    rules, status = load_rules_with_status(tmp_path / "nonexistent")
    assert rules == []
    assert status == "missing"


def test_with_status_empty_directory(tmp_path: Path) -> None:
    """Empty directory (no .yaml/.yml files) -> ``([], "empty_file")``.

    Distinct from missing -- the user created the directory but didn't
    write any rules. Empty stub MUST short-circuit: no spurious warnings.
    """
    from roam.rules.engine import load_rules_with_status

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    warnings_out: list[str] = []
    rules, status = load_rules_with_status(rules_dir, warnings_out=warnings_out)
    assert rules == []
    assert status == "empty_file"
    assert warnings_out == [], f"empty rules dir must not emit a warning, got: {warnings_out!r}"


def test_with_status_ok(tmp_path: Path) -> None:
    """Well-formed rule files -> ``(rules, "ok")``."""
    from roam.rules.engine import load_rules_with_status

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "rule1.yaml").write_text(
        'name: "Some rule"\nseverity: error\nmatch:\n  kind: function\n',
        encoding="utf-8",
    )
    warnings_out: list[str] = []
    rules, status = load_rules_with_status(rules_dir, warnings_out=warnings_out)
    assert status == "ok"
    assert len(rules) == 1
    assert rules[0]["name"] == "Some rule"
    assert warnings_out == []


def test_with_status_parse_error(tmp_path: Path) -> None:
    """Malformed YAML in directory -> ``status == "parse_error"`` + warning emitted."""
    from roam.rules.engine import load_rules_with_status

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "bad.yaml").write_text("name: bad\nmatch: [unterminated\n", encoding="utf-8")
    warnings_out: list[str] = []
    rules, status = load_rules_with_status(rules_dir, warnings_out=warnings_out)
    # The placeholder _error rule still appears for the malformed file.
    assert len(rules) == 1
    assert "_error" in rules[0]
    assert status == "parse_error"
    assert warnings_out, "parse_error MUST emit a canonical-loader warning"


def test_with_status_mixed_ok_and_bad(tmp_path: Path) -> None:
    """Mixed directory (1 OK + 1 bad) -> worst-status wins -> ``"parse_error"``.

    Verifies the per-file rollup elevates the worst per-file status
    rather than silently reporting "ok" because at least one file
    parsed cleanly.
    """
    from roam.rules.engine import load_rules_with_status

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "good.yaml").write_text('name: "Good rule"\nseverity: error\n', encoding="utf-8")
    (rules_dir / "bad.yaml").write_text("name: bad\nmatch: [unterminated\n", encoding="utf-8")
    warnings_out: list[str] = []
    rules, status = load_rules_with_status(rules_dir, warnings_out=warnings_out)
    # 2 records: 1 good + 1 placeholder _error.
    assert len(rules) == 2
    assert status == "parse_error", f"worst-status rollup must elevate parse_error over ok, got: {status!r}"
    assert warnings_out, "mixed directory MUST emit a warning for the bad file"


def test_load_yaml_with_status_ok(tmp_path: Path) -> None:
    """Per-file ``_load_yaml_with_status`` direct entry point.

    Mirrors the W1030-followup-C/-D direct-sub-loader tests so the
    library-side primitive is independently exercised.
    """
    from roam.rules.engine import _load_yaml_with_status

    p = tmp_path / "rule.yaml"
    p.write_text(
        'name: "Some rule"\nseverity: warning\n',
        encoding="utf-8",
    )
    data, status = _load_yaml_with_status(p)
    assert status == "ok"
    assert isinstance(data, dict)
    assert data["name"] == "Some rule"


def test_load_yaml_with_status_missing(tmp_path: Path) -> None:
    """Missing file -> ``(None, "missing")``."""
    from roam.rules.engine import _load_yaml_with_status

    data, status = _load_yaml_with_status(tmp_path / "nope.yaml")
    assert data is None
    assert status == "missing"


# ---------------------------------------------------------------------------
# Pre-W1030-followup-F regression: _load_yaml byte-identical
# ---------------------------------------------------------------------------


def test_legacy_load_yaml_byte_identical(tmp_path: Path) -> None:
    """W1036 callers of bare ``_load_yaml`` stay byte-identical.

    The W1036 ``warnings_out`` tests in
    ``test_rules_engine_warnings_out.py`` depend on the bare-data return
    (None on parse failure, dict on happy path). W1030-followup-F MUST
    NOT change that contract — it only adds a new ``_load_yaml_with_status``
    entry point.
    """
    from roam.rules.engine import _load_yaml

    # Happy path.
    good = tmp_path / "good.yaml"
    good.write_text("name: ok\nseverity: error\n", encoding="utf-8")
    data = _load_yaml(good)
    assert isinstance(data, dict)
    assert data["name"] == "ok"

    # Malformed.
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: bad\nmatch: [unterminated\n", encoding="utf-8")
    data = _load_yaml(bad)
    assert data is None

    # Missing.
    data = _load_yaml(tmp_path / "missing.yaml")
    assert data is None
