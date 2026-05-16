"""W918: surface ``cmd_alerts._resolved_thresholds`` silent fallback as a
Pattern 2 warning on the envelope.

Pre-W918, ``_resolved_thresholds`` defaulted unknown user-supplied
metrics in ``.roam/alerts.yaml`` to ``{"op": ">", "value": 0,
"level": "warning"}`` silently. A user who adds a "worse-when-lower"
metric (e.g. ``coverage``) without specifying ``op`` would get
nonsense alerts (every positive value would trip the ``>0`` rule) AND
have no signal that anything went wrong.

W918 preserves the fallback (backward compat with existing
``.roam/alerts.yaml`` files in the wild) but threads a
``warnings_out`` accumulator through the resolver. The CLI surfaces
the warnings on the JSON envelope's top-level ``warnings_out`` array
AND in text mode prominently before the alert list. This codifies the
Pattern 2 discipline from CLAUDE.md: "name what fails so the user can
fix it."
"""

from __future__ import annotations

from pathlib import Path

from roam.commands.cmd_alerts import _resolved_thresholds


def _make_minimal_alerts_yaml(text: str, root: Path) -> Path:
    """Write ``.roam/alerts.yaml`` under *root* and return the project path."""
    (root / ".roam").mkdir(exist_ok=True)
    (root / ".roam" / "alerts.yaml").write_text(text, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Resolver-level tests: silent fallback now appends to warnings_out
# ---------------------------------------------------------------------------


def test_unknown_metric_emits_warning(tmp_path: Path) -> None:
    """W918: an unknown metric with no ``op`` triggers a warning AND
    still gets the legacy default threshold applied.

    Backward compat: the threshold IS still applied (op='>', value=0,
    level='warning') — Pattern 2 forbids breaking existing configs.
    The fix is that the silent state is now surfaced explicitly.
    """
    _make_minimal_alerts_yaml(
        # ``coverage`` is not in _DEFAULT_THRESHOLDS; the row is missing
        # ``op`` so the fallback path fires. ``level`` is supplied but
        # the row is still incomplete.
        "thresholds:\n  coverage: { value: 80, level: critical }\n",
        tmp_path,
    )

    warnings: list[str] = []
    resolved = _resolved_thresholds(project_root=tmp_path, warnings_out=warnings)

    # The threshold IS still applied (backward compat) ...
    assert "coverage" in resolved, (
        "Backward compat: legacy fallback must still apply the default threshold for unknown metrics."
    )
    # ... AND the warning is surfaced.
    assert len(warnings) == 1, f"Expected exactly one warning for the unknown ``coverage`` metric, got: {warnings}"
    assert "coverage" in warnings[0], f"Warning text must name the offending metric, got: {warnings[0]!r}"


def test_unknown_metric_warning_is_actionable(tmp_path: Path) -> None:
    """W918: warning text names the metric, the config file, and ends on
    an imperative next step (LAW 2 + LAW 4 in CLAUDE.md)."""
    _make_minimal_alerts_yaml(
        "thresholds:\n  test_pass_rate: { value: 95 }\n",
        tmp_path,
    )

    warnings: list[str] = []
    _resolved_thresholds(project_root=tmp_path, warnings_out=warnings)

    assert warnings, "Expected at least one config warning"
    warning = warnings[0]

    # Names the specific metric (LAW 4 — concrete-noun anchor on the
    # actual user input, not an abstract reference like "your config").
    assert "test_pass_rate" in warning, f"Warning text must name the offending metric, got: {warning!r}"
    # Points at the config file the user must edit (LAW 2 — imperative
    # next step; user knows where to go).
    assert ".roam/alerts.yaml" in warning, (
        f"Warning text must point at the config file the user edits, got: {warning!r}"
    )
    # Names the silently-applied defaults so the user understands the
    # current behaviour, not just that something is wrong.
    assert "op='>'" in warning and "value=0" in warning, (
        f"Warning text must disclose the silent fallback parameters (op='>' value=0), got: {warning!r}"
    )
    # Imperative next-step verb in the trailing clause.
    assert "Add" in warning or "add" in warning, (
        f"Warning text must end on an imperative next step (LAW 2: 'Add a threshold entry...'), got: {warning!r}"
    )


def test_known_metric_override_emits_no_warning(tmp_path: Path) -> None:
    """W918: overriding a metric that IS in _DEFAULT_THRESHOLDS (e.g.
    ``cycles``) does NOT trigger a Pattern 2 warning — the user is
    refining a known rule, not introducing a new silent-fallback row.
    """
    _make_minimal_alerts_yaml(
        # ``cycles`` IS in _DEFAULT_THRESHOLDS; partial override is a
        # legitimate refinement, not a silent fallback.
        "thresholds:\n  cycles: { value: 50 }\n",
        tmp_path,
    )

    warnings: list[str] = []
    _resolved_thresholds(project_root=tmp_path, warnings_out=warnings)

    assert warnings == [], (
        f"Override of a KNOWN metric must not emit a Pattern 2 warning "
        f"(known-metric partial-override is intentional refinement, "
        f"not silent fallback), got: {warnings}"
    )


def test_complete_unknown_metric_emits_no_warning(tmp_path: Path) -> None:
    """W918: an unknown metric supplied with a complete ``{op, value,
    level}`` triple is NOT a silent fallback — the user made every
    intent explicit, so no Pattern 2 warning fires.
    """
    _make_minimal_alerts_yaml(
        "thresholds:\n  coverage: { op: '<', value: 80, level: critical }\n",
        tmp_path,
    )

    warnings: list[str] = []
    _resolved_thresholds(project_root=tmp_path, warnings_out=warnings)

    assert warnings == [], (
        f"Complete {{op, value, level}} triple for unknown metric must "
        f"NOT trigger a silent-fallback warning, got: {warnings}"
    )


def test_warnings_out_none_preserves_legacy_signature(tmp_path: Path) -> None:
    """W918: callers that omit ``warnings_out`` still get the legacy
    behaviour (default-merge, no extra return values). Backward compat
    is mandatory — the signature change is additive only.
    """
    _make_minimal_alerts_yaml(
        "thresholds:\n  unknown_metric: { value: 5 }\n",
        tmp_path,
    )

    # No ``warnings_out`` kwarg: must not raise, must still apply the
    # fallback default for the unknown metric.
    resolved = _resolved_thresholds(project_root=tmp_path)

    assert "unknown_metric" in resolved
    assert resolved["unknown_metric"]["op"] == ">"
    assert resolved["unknown_metric"]["level"] == "warning"


# ---------------------------------------------------------------------------
# End-to-end CLI tests: envelope ``warnings_out`` field carries the warning
# ---------------------------------------------------------------------------


def test_cli_envelope_carries_warnings_out_field(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: ``roam --json alerts`` returns ``warnings_out`` in
    the envelope when ``.roam/alerts.yaml`` has an unknown metric."""
    import json
    import sys

    from click.testing import CliRunner

    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import git_init, index_in_process, invoke_cli  # noqa: E402

    proj = tmp_path / "w918_envelope"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def main():\n    return 0\n")
    git_init(proj)

    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"

    # Now write the unknown-metric config AFTER indexing (so .roam/
    # already exists for the index but the file we drop is for the
    # alerts run).
    _make_minimal_alerts_yaml(
        "thresholds:\n  coverage: { value: 80, level: critical }\n",
        proj,
    )

    runner = CliRunner()
    result = invoke_cli(runner, ["alerts"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, f"alerts failed:\n{result.output}"

    data = json.loads(result.output)

    # Envelope MUST carry ``warnings_out`` as a list (Pattern 2:
    # consumers can rely on the key being present even when empty).
    assert "warnings_out" in data, f"Envelope must carry top-level ``warnings_out`` key, got: {list(data.keys())}"
    assert isinstance(data["warnings_out"], list)
    assert len(data["warnings_out"]) == 1, f"Expected exactly one warning for ``coverage``, got: {data['warnings_out']}"
    assert "coverage" in data["warnings_out"][0]

    # ``partial_success`` must be True on the summary when the
    # silent-fallback path fired.
    assert data["summary"].get("partial_success") is True, (
        f"summary.partial_success must be True when silent-fallback warnings fired, got summary: {data['summary']}"
    )
