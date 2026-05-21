#!/usr/bin/env python3
"""Export the :class:`McpDecisionReceipt` JSON Schema to stdout.

MCP-P2.2: gateways (Interlock, Lasso, Portkey) and procurement
reviewers consume this schema to validate roam-emitted receipts
without reading Python source. Output is JSON Schema Draft 2020-12.

Usage::

    python scripts/export_mcp_receipt_schema.py

    # Pin into a vendored copy
    python scripts/export_mcp_receipt_schema.py > mcp-receipt.schema.json

    # Or, from an installed wheel (this script lives outside the
    # package and is NOT shipped to PyPI — the module entrypoint is):
    python -m roam.evidence.mcp_receipt_schema

This file is a thin in-repo convenience delegator to
:func:`roam.evidence.mcp_receipt_schema._main`. The real export logic —
including the ``--out PATH`` flag — lives in that module so it ships in
the wheel. ``--out`` and any future flags pass straight through.

See ``dev/MCP-SECURITY-POSTURE.md`` § "Schema export" for the
gateway-facing description.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a standalone script (``python scripts/...``) without
# installing the package: prepend the in-repo ``src/`` to sys.path when
# the package isn't already importable.
try:
    from roam.evidence.mcp_receipt_schema import _main
except ImportError:
    _SRC = Path(__file__).resolve().parent.parent / "src"
    if _SRC.is_dir():
        sys.path.insert(0, str(_SRC))
    from roam.evidence.mcp_receipt_schema import _main


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
