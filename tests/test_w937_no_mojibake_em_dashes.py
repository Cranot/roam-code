"""W937 drift guard — no cp1253-mojibake em-dashes in src/ docstrings.

W929 drive-by: some docstrings under ``src/roam`` carried UTF-8-mangled
arrows / em-dashes from prior edits where a string written in UTF-8 was
re-saved through a cp1253 (Greek codepage) round-trip and back to UTF-8.
The visual shape is ``β€”`` (three characters: greek beta + euro + closing
quote) where the original intent was ``—`` (one em-dash character).

W937 swept the codebase and fixed 28 instances across 5 files
(``catalog/detectors.py`` x22, ``commands/cmd_ws.py`` x1,
``commands/context_helpers.py`` x3, ``index/complexity.py`` x1,
``search/index_embeddings.py`` x1). This test pins the invariant so the
same mojibake can't silently creep back in via a future Greek-codepage
editor session.

The mojibake byte sequence is fixed and unambiguous:
``b'\\xce\\xb2\\xe2\\x82\\xac\\xe2\\x80\\x9d'`` — exactly what the
em-dash UTF-8 bytes (``\\xe2\\x80\\x94``) become when decoded as cp1253
and re-encoded as UTF-8.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests._helpers.repo_root import repo_root

# The signature byte sequence of cp1253-mojibake-ed UTF-8 em-dashes.
# Hard-coded as raw bytes so this test stays decoupled from any
# encoding-related helper.
_MOJIBAKE_EM_DASH: bytes = b"\xce\xb2\xe2\x82\xac\xe2\x80\x9d"

_SRC_ROOT: Path = repo_root() / "src" / "roam"


def test_src_roam_has_no_cp1253_mojibake_em_dashes() -> None:
    """Every ``.py`` under ``src/roam`` is free of cp1253-mojibake em-dashes."""
    hits: list[tuple[str, int]] = []
    for root, _dirs, fns in os.walk(_SRC_ROOT):
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            p = Path(root) / fn
            raw = p.read_bytes()
            count = raw.count(_MOJIBAKE_EM_DASH)
            if count:
                hits.append((str(p), count))
    assert hits == [], (
        "Found cp1253-mojibake em-dashes (the byte sequence "
        f"{_MOJIBAKE_EM_DASH!r} == 'β€”'). Replace with the correct UTF-8 "
        "em-dash (— / b'\\xe2\\x80\\x94'). Hits:\n  " + "\n  ".join(f"{p}: {n} occurrences" for p, n in hits)
    )
