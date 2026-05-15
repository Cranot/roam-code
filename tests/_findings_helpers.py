"""W934: shared assertion helper for the ``test_findings_<detector>_*`` cluster.

The ``W855`` rename-invariant clone detector flagged ~24 near-identical
test bodies of the form
``test_<detector>_findings_visible_via_cmd_findings_count``. Every one
runs the same four-line assertion after a detector-specific
fixture/persist setup:

1. exit code 0
2. ``envelope["summary"]["state"] == "populated"``
3. detector_kind appears in ``envelope["counts"]``
4. count is ``>= expected_min_count`` (or ``== expected_exact_count``)

This helper lifts the shared assertion. Each per-detector test keeps
its bespoke project + persist setup (those genuinely diverge) and
shrinks the assertion block to one call.

Important: parametrizing the whole pattern into a single file was NOT
viable — every detector owns a private ``_xxx_project(tmp_path)`` factory
and ``_persist_xxx(proj)`` helper inside its own test module, plus a
mix of conftest fixtures (``indexed_project``, ``laravel_gap_project``,
``php_project``, ``runtime_project``, ``dark_matter_project``,
``vuln_project``) and direct-DB-write paths (``doctor``, ``n1``). Strategy
C (shared helper, per-detector tests stay) preserves those fixtures
and stays reversible.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from click.testing import CliRunner

from roam.cli import cli


def assert_detector_visible_in_findings_count(
    proj: Path,
    detector_kind: str,
    *,
    expected_min_count: int = 1,
    expected_exact_count: int | None = None,
) -> Mapping[str, Any]:
    """Assert ``roam findings count`` exposes ``detector_kind`` after setup.

    Parameters
    ----------
    proj:
        Project root that was indexed + had the detector persisted.
        The helper ``chdir``s into it for the CLI invocation and
        restores the original cwd in ``finally``.
    detector_kind:
        Key expected under ``envelope["counts"]`` (e.g. ``"smells"``,
        ``"auth-gaps"``, ``"audit-trail-verify"``).
    expected_min_count:
        Lower bound on the count. Defaults to ``1``. Ignored when
        ``expected_exact_count`` is set.
    expected_exact_count:
        When set, require ``count == expected_exact_count`` (used by
        the ``doctor`` test, which injects exactly 2 blocking failures).

    Returns
    -------
    Mapping[str, Any]
        The parsed JSON envelope, for callers that want extra
        per-detector assertions on top of the shared shape.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["--json", "findings", "count"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["summary"]["state"] == "populated"
    assert detector_kind in envelope["counts"], (
        f"{detector_kind!r} missing from counts={envelope['counts']!r}"
    )

    actual = envelope["counts"][detector_kind]
    if expected_exact_count is not None:
        assert actual == expected_exact_count, (
            f"{detector_kind} count: expected exact {expected_exact_count}, "
            f"got {actual}"
        )
    else:
        assert actual >= expected_min_count, (
            f"{detector_kind} count: expected >= {expected_min_count}, "
            f"got {actual}"
        )

    return envelope
