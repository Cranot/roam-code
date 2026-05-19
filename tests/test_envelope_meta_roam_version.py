"""Regression pin for `_meta.roam_version` on every json_envelope.

W210 evidence axis: every envelope must carry producer-version provenance
under ``_meta.roam_version`` so ``ChangeEvidence.roam_version`` consumers
can stamp the producing roam-code build. Pre-fix only ``schema_version``
was carried; ``roam_version`` lived only at top-level ``out["version"]``
which mismatched the W210 ChangeEvidence field name.

Hash-stability discipline: ``_meta`` is already non-deterministic
(``timestamp``), so adding a stable field here cannot regress prompt-cache
hit rates that the timestamp variability already broke. The evidence-packet
content-hash (``test_evidence_schema_migration``) hashes the
``ChangeEvidence`` dataclass output, NOT envelope JSON, so this addition
does NOT affect those golden hashes — verified by running the golden-hash
suite alongside this fix.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam import __version__
from roam.cli import cli
from roam.output.formatter import json_envelope


def test_json_envelope_carries_meta_roam_version_direct():
    """Direct unit-level: ``json_envelope(...)["_meta"]["roam_version"]``
    matches ``roam.__version__``.
    """
    env = json_envelope("health", summary={"verdict": "ok"})
    assert "_meta" in env
    assert "roam_version" in env["_meta"], "_meta.roam_version missing"
    assert env["_meta"]["roam_version"] == __version__


def test_json_envelope_top_level_version_matches_meta_roam_version():
    """Backward-compat: pre-W210 consumers read ``out["version"]``. The
    new ``_meta.roam_version`` must agree byte-for-byte so consumers
    can migrate without behavior change.
    """
    env = json_envelope("health", summary={"verdict": "ok"})
    assert env["version"] == env["_meta"]["roam_version"]


def test_cli_surface_envelope_carries_meta_roam_version():
    """End-to-end: ``roam --json surface`` envelope has the field."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "surface"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "_meta" in data
    assert "roam_version" in data["_meta"]
    assert data["_meta"]["roam_version"] == __version__


def test_meta_roam_version_is_nonempty_string():
    """Pin the type contract — agents that branch on the version string
    expect a non-empty str, not None or int.
    """
    env = json_envelope("doctor", summary={"verdict": "ok"})
    rv = env["_meta"]["roam_version"]
    assert isinstance(rv, str), f"roam_version must be str, got {type(rv).__name__}"
    assert rv, "roam_version must be non-empty"
