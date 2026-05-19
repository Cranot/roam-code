#!/usr/bin/env python3
"""Export the :class:`McpDecisionReceipt` JSON Schema to stdout.

MCP-P2.2: gateways (Interlock, Lasso, Portkey) and procurement
reviewers consume this schema to validate roam-emitted receipts
without reading Python source. Output is JSON Schema Draft 2020-12.

Usage::

    python scripts/export_mcp_receipt_schema.py

    # Pin into a vendored copy
    python scripts/export_mcp_receipt_schema.py > mcp-receipt.schema.json

The schema is constructed from
:func:`roam.evidence.mcp_receipt_schema.mcp_receipt_json_schema`. It
pulls closed-enum vocabulary by reference at build time, so a
vocabulary edit in :mod:`roam.evidence._vocabulary` propagates here
automatically.

See ``dev/MCP-SECURITY-POSTURE.md`` § "Schema export" for the
gateway-facing description.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a standalone script (``python scripts/...``) without
# installing the package: prepend the in-repo ``src/`` to sys.path when
# the package isn't already importable.
try:
    from roam.evidence.mcp_receipt_schema import mcp_receipt_json_schema
except ImportError:
    _SRC = Path(__file__).resolve().parent.parent / "src"
    if _SRC.is_dir():
        sys.path.insert(0, str(_SRC))
    from roam.evidence.mcp_receipt_schema import mcp_receipt_json_schema


def main() -> int:
    schema = mcp_receipt_json_schema()
    # Deterministic: sorted keys + 2-space indent. Easy to diff in PRs.
    json.dump(schema, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
