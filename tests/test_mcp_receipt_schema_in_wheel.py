"""Drift guard: the MCP receipt JSON Schema export is wheel-reachable.

``scripts/export_mcp_receipt_schema.py`` lives OUTSIDE the ``roam``
package, so it is not shipped by ``pip install roam-code``. A wheel user
must be able to export the gateway-pinned schema. The wheel-reachable
mechanism is the module entrypoint:

    python -m roam.evidence.mcp_receipt_schema

This test exercises that exact invocation through a subprocess (the same
path an installed-wheel user takes — ``runpy`` on a package module) and
asserts it emits valid JSON carrying the versioned ``$id`` that gateway
integrators pin against.

If this test breaks, a ``pip install`` user has lost the ability to
export the schema. The in-repo ``scripts/`` delegator is convenience
only — it is NOT a substitute for the module entrypoint.
"""

from __future__ import annotations

import json
import subprocess
import sys

from roam.evidence.mcp_receipt_schema import (
    SCHEMA_ID,
    mcp_receipt_json_schema,
)
from tests._helpers.repo_root import repo_root


def _run_module() -> subprocess.CompletedProcess[str]:
    """Run ``python -m roam.evidence.mcp_receipt_schema`` in a subprocess.

    ``cwd`` is the repo root so the editable install resolves; an actual
    wheel install would resolve ``roam`` from ``site-packages``
    regardless of cwd — the ``-m`` invocation is the wheel-reachable
    path either way.
    """
    return subprocess.run(
        [sys.executable, "-m", "roam.evidence.mcp_receipt_schema"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo_root()),
    )


def test_module_entrypoint_emits_valid_json() -> None:
    result = _run_module()
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)  # raises if not valid JSON
    assert isinstance(parsed, dict)


def test_module_entrypoint_id_is_versioned() -> None:
    """A wheel user must get the same versioned ``$id`` gateways pin."""
    result = _run_module()
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["$id"].endswith("/mcp-receipt/v1.json")
    assert parsed["$id"] == SCHEMA_ID


def test_module_entrypoint_matches_in_process_schema() -> None:
    """The subprocess export must equal the in-process generator output."""
    result = _run_module()
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed == mcp_receipt_json_schema()


def test_module_entrypoint_out_flag_writes_file(tmp_path) -> None:
    """``--out PATH`` writes the schema to disk instead of stdout."""
    out = tmp_path / "mcp-receipt.schema.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "roam.evidence.mcp_receipt_schema",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo_root()),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", "stdout must be empty when --out is given"
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["$id"].endswith("/mcp-receipt/v1.json")
