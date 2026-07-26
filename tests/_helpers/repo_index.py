"""Readiness gate for tests that borrow the roam-code repo's OWN index.

Most tests build a throwaway project via the ``indexed_project`` fixture and
are hermetic. A handful deliberately run against this repo's real index
instead, because the assertion only means something on a large, genuinely
cyclic, genuinely coupled codebase — "the cycles probe returns real SCCs"
cannot be demonstrated on a three-file fixture.

Those tests used to gate on ``os.path.exists(".roam/index.db")``. That checks
EXISTENCE, not READINESS, and the two diverge in exactly the environment that
matters. On CI the index is absent at checkout, so the guard was assumed to
skip; but any earlier step or concurrently-running test that starts an index
materialises ``index.db`` immediately, while population happens afterwards.
A test entering that window sees the file, declines to skip, queries a
half-built or empty database, and fails on empty results — reported not as
"index not ready" but as a product defect:

    test_w12_cycles_dimension_has_native_dispatch
    AssertionError: cycles dispatch returned no items:
      `roam cycles` returned no usable result. Try `roam ask ...` or re-index.

Under ``-n auto --dist loadgroup`` (which ``roam.testing.ci_xdist`` injects on
CI) that window is wide and which test loses is scheduling-dependent, so the
failure looks intermittent and unrelated to the change under test. Combined
with ``-x`` it also masks every later failure, which is how roam-code's main
branch stayed red for three days behind a rotating cast of symptoms.

``repo_index_status`` closes the window by asking three questions instead of
one: the database exists, roam's own lifecycle marker says the last build
COMPLETED, and the database actually contains symbols. The completeness test
delegates to ``roam.index.indexer._decode_index_state`` rather than parsing
the marker here, so there is one definition of "complete" and this helper
cannot drift from the writer.

Generalises to any suite where tests opportunistically reuse a shared,
externally-built artifact — an index, a cache, a fixture database, a compiled
bundle. Gate on readiness, never on the presence of the file, and make the
skip reason say which of the checks failed so a skip is never mistaken for a
pass.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests._helpers.repo_root import repo_root


def repo_index_status(root: Path | str | None = None) -> tuple[bool, str]:
    """Return ``(ready, reason)`` for the repo index under ``root``.

    ``reason`` is always human-readable and names the failing check, so a
    skipped test reports why it was skipped instead of vanishing silently.
    """
    base = Path(root) if root is not None else repo_root()
    roam_dir = base / ".roam"
    db_path = roam_dir / "index.db"

    if not db_path.exists():
        return False, f"no index database at {db_path}"

    try:
        raw_state = (roam_dir / "index.state").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        # A missing or unreadable marker cannot prove the build finished.
        return False, f"index.state unreadable ({exc.__class__.__name__}); build state unknown"

    # Single source of truth: the decoder the writer itself uses.
    from roam.index.indexer import _decode_index_state

    state_kind, _owner = _decode_index_state(raw_state)
    if state_kind != "complete":
        return False, f"index build is {state_kind!r}, not 'complete'"

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            symbol_count = conn.execute("SELECT count(*) FROM symbols").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"index database unreadable: {exc}"

    if not symbol_count:
        return False, "index database holds no symbols"

    return True, f"ready ({symbol_count} symbols)"


def repo_index_ready(root: Path | str | None = None) -> bool:
    """Boolean form of :func:`repo_index_status`."""
    return repo_index_status(root)[0]
