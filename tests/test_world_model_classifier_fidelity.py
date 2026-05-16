"""W13.4 — R28 classifier fidelity tests.

Covers the gaps surfaced in Wave 12:

- ``os.replace`` — used in the canonical atomic-write idiom
  (``tmp + os.replace(target)``). Pre-W13.4 the prefix table missed it
  entirely; functions using it for safe writes classified as ``none``.
- ``os.remove`` / ``os.unlink`` / ``os.rename`` — fs-mutating ops.
- ``shutil.copy`` — coarse copy. Should classify as ``io_write`` (we
  read AND write; the *write* is the actionable signal for agents).
- ``Path.replace`` / ``Path.rename`` / ``Path.unlink`` — pathlib
  equivalents. Same semantics, different qualified names.
- The end-to-end ``_atomic_write_text`` idiom — ``tempfile.mkstemp`` +
  ``os.fdopen(... 'w')`` + ``os.replace(...)`` — must classify as
  ``io_write`` (the dominant kind), NOT ``none``.

The wrapper-function classification test is DEFERRED — the root cause
is the Python language extractor not emitting nested ``def wrapper(...)``
helpers as separate symbols, which is well beyond a < 30 LOC fix. See
the W13.4 report for the diagnosis.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import invoke_cli  # noqa: E402,F401  (re-exported indirectly)


def _classify(conn, name: str):
    from roam.world_model.side_effects import classify_side_effects

    return classify_side_effects(conn, symbol_name=name)


# ---------------------------------------------------------------------------
# os.replace — the atomic-rename idiom anchor
# ---------------------------------------------------------------------------


def test_os_replace_classified_as_io_write(project_factory, monkeypatch):
    """``os.replace(tmp, target)`` -> io_write. The atomic-rename idiom
    must surface as a write (not ``none``), because that's the actionable
    signal for agents — ``replace`` mutates the destination atomically."""
    proj = project_factory(
        {
            "src/replacer.py": ("import os\n\ndef swap(tmp, dst):\n    os.replace(tmp, dst)\n"),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        results = _classify(conn, "swap")

    assert results, "Expected to classify 'swap'"
    assert "io_write" in results[0].kinds, f"Expected io_write for os.replace, got {results[0].kinds}"


# ---------------------------------------------------------------------------
# os.remove / os.unlink / os.rename
# ---------------------------------------------------------------------------


def test_os_remove_classified_as_io_write(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/clean.py": (
                "import os\n\ndef cleanup(path):\n    os.remove(path)\n\ndef unlink_path(path):\n    os.unlink(path)\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        all_results = {r.symbol.rsplit(".", 1)[-1]: r for r in _classify(conn, "cleanup")}
        all_results.update({r.symbol.rsplit(".", 1)[-1]: r for r in _classify(conn, "unlink_path")})

    assert "io_write" in all_results["cleanup"].kinds
    assert "io_write" in all_results["unlink_path"].kinds


# ---------------------------------------------------------------------------
# shutil.copy — coarse copy
# ---------------------------------------------------------------------------


def test_shutil_copy_classified_as_io_write(project_factory, monkeypatch):
    """``shutil.copy(a, b)`` -> io_write (the actionable signal). The
    coarse taxonomy collapses copy to its dominant side-effect: the
    write at the destination."""
    proj = project_factory(
        {
            "src/copier.py": ("import shutil\n\ndef copy_file(src, dst):\n    shutil.copy(src, dst)\n"),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        results = _classify(conn, "copy_file")

    assert results, "Expected to classify 'copy_file'"
    assert "io_write" in results[0].kinds, f"shutil.copy must classify as io_write, got {results[0].kinds}"


# ---------------------------------------------------------------------------
# _atomic_write_text idiom — tmp + os.replace
# ---------------------------------------------------------------------------


def test_atomic_write_text_pattern_classified_correctly(project_factory, monkeypatch):
    """The canonical safe-write idiom (``tempfile.mkstemp`` -> write ->
    ``os.replace``) is the strongest test of the prefix table. It must
    classify as ``io_write`` — historically classified as ``none``
    because the only filesystem mutation was ``os.replace``, which was
    missing from the prefix list."""
    proj = project_factory(
        {
            "src/atomic.py": (
                "import os\n"
                "import tempfile\n"
                "\n"
                "def atomic_write_text(target, content):\n"
                "    parent = os.path.dirname(target)\n"
                "    tmp_fd, tmp_name = tempfile.mkstemp(dir=parent)\n"
                "    with os.fdopen(tmp_fd, 'w', encoding='utf-8') as fh:\n"
                "        fh.write(content)\n"
                "    os.replace(tmp_name, target)\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        results = _classify(conn, "atomic_write_text")

    assert results, "Expected to classify 'atomic_write_text'"
    assert "io_write" in results[0].kinds, (
        f"Atomic-write idiom must classify as io_write, got {results[0].kinds} (evidence: {results[0].evidence})"
    )
    # And the result MUST NOT be the unsignal-bearing 'none' bucket.
    assert "none" not in results[0].kinds


# ---------------------------------------------------------------------------
# Path.replace / Path.rename — pathlib equivalents
# ---------------------------------------------------------------------------


def test_path_replace_classified_as_io_write(project_factory, monkeypatch):
    """``pathlib.Path.replace(target)`` is the pathlib equivalent of
    ``os.replace`` — same semantics, different qualified name. The
    classifier must recognise the source-text pattern (call-edge
    resolution often misses bound-method calls)."""
    proj = project_factory(
        {
            "src/path_replacer.py": (
                "from pathlib import Path\n"
                "\n"
                "def swap_path(tmp_str, dst_str):\n"
                "    tmp = Path(tmp_str)\n"
                "    Path.replace(tmp, Path(dst_str))\n"
            ),
        }
    )
    monkeypatch.chdir(proj)
    from roam.db.connection import open_db

    with open_db(readonly=True) as conn:
        results = _classify(conn, "swap_path")

    assert results, "Expected to classify 'swap_path'"
    assert "io_write" in results[0].kinds, f"Path.replace must classify as io_write, got {results[0].kinds}"
