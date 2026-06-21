"""Regression: `_freeform_parallel_fetch` must cap the named file at 400 KB
BEFORE `read_text`, not after. The compile path stats the file; a tracked file
of arbitrary size must NOT be slurped into memory at compile time (memory /
latency DoS). An oversized named file yields a `None` full-file payload, which
both consumers already treat as "absent" — `_embed_freeform_symbol_body`
re-stats and re-caps, `_freeform_full_file_body` returns an empty fact set — so
the cap is behavior-preserving for the gates they apply.

Seals the fix in `_freeform_parallel_fetch._do_read_full`.
"""

from __future__ import annotations

import roam.plan.compiler as _c

_CAP = 400 * 1024


def _write_file(path: str, size: int) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("x" * size)


def test_freeform_parallel_fetch_caps_oversized_named_file(tmp_path, monkeypatch):
    """A named file OVER the 400 KB cap must yield a None payload without
    reading the whole file into memory. Previously the code stat'd the file
    and then `read_text`'d all of it, letting a large tracked file dominate
    compile-time memory and latency."""
    monkeypatch.setattr(_c, "_run_roam", lambda *a, **k: None)
    target = "big.py"
    _write_file(str(tmp_path / target), _CAP + 1)

    _d, ffp, _timings = _c._freeform_parallel_fetch(target, cwd=str(tmp_path))

    assert ffp is None, "oversized named file must not be read into the payload"


def test_freeform_parallel_fetch_reads_named_file_at_cap_boundary(tmp_path, monkeypatch):
    """A named file at EXACTLY the 400 KB cap is still read (the guard is a
    strict `>`). Pins the boundary against an off-by-one flip to `>=`."""
    monkeypatch.setattr(_c, "_run_roam", lambda *a, **k: None)
    target = "exact.py"
    _write_file(str(tmp_path / target), _CAP)

    _d, ffp, _timings = _c._freeform_parallel_fetch(target, cwd=str(tmp_path))

    assert isinstance(ffp, dict)
    assert ffp["size"] == _CAP
    assert ffp["raw"] == "x" * _CAP


def test_freeform_parallel_fetch_reads_small_named_file(tmp_path, monkeypatch):
    """A named file well under the cap is read into the payload (positive
    control for the two cap tests above)."""
    monkeypatch.setattr(_c, "_run_roam", lambda *a, **k: None)
    target = "small.py"
    size = 4 * 1024
    _write_file(str(tmp_path / target), size)

    _d, ffp, _timings = _c._freeform_parallel_fetch(target, cwd=str(tmp_path))

    assert isinstance(ffp, dict)
    assert ffp["size"] == size
    assert ffp["raw"] == "x" * size
