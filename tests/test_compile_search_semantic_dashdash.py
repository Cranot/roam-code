"""Regression: the fallback semantic query is passed after a ``--`` delimiter.

Both compiler subprocess call sites that shell out to ``roam search-semantic``
(``_probe_find_by_description_for_task`` and ``_likely_files_from_search``)
MUST insert a ``--`` between the subcommand and the task text. Without it, a
task beginning with ``--help`` or ``--backend=...`` is parsed by Click as a
``search-semantic`` option — ``--help`` aborts the subprocess and
``--backend=...`` silently alters the retrieval backend — so the likely-file
prefetch is dropped or corrupted.

The fix is a one-token argv change; this test pins it so a future refactor
that rebuilds the argv list cannot quietly drop the delimiter.
"""

from __future__ import annotations

import roam.plan.compiler as compiler


def test_likely_files_from_search_uses_dashdash(monkeypatch):
    # Option-like task with no explicit file paths and no cache hit, so the
    # subprocess fallback is reached.
    task = "--backend=onnx where does authentication happen"
    captured: list[list[str]] = []

    monkeypatch.setattr(compiler, "_extract_file_paths", lambda t, cwd=None: [])
    monkeypatch.setattr(compiler, "_symbol_resolution_cache_lookup", lambda t, c: None)
    monkeypatch.setattr(compiler, "_symbol_resolution_cache_store", lambda *a, **k: None)

    def fake_run_roam(args, *posargs, **kwargs):
        captured.append(list(args))
        return None

    monkeypatch.setattr(compiler, "_run_roam", fake_run_roam)

    files, invoked = compiler._likely_files_from_search(task, cwd="/nonexistent")

    assert invoked is True
    assert captured, "subprocess fallback was never reached"
    assert captured[0] == ["search-semantic", "--", task], captured[0]


def test_find_by_description_probe_uses_dashdash(monkeypatch):
    # Matches _FIND_BY_DESC_RE ("find code about ...") AND starts option-like.
    task = "--backend=onnx find code about caching"
    captured: list[list[str]] = []

    def fake_run_roam(args, *posargs, **kwargs):
        captured.append(list(args))
        return None

    monkeypatch.setattr(compiler, "_run_roam", fake_run_roam)

    compiler._probe_find_by_description_for_task(task, cwd="/nonexistent")

    assert captured, "probe did not fire on a find-by-description task"
    assert captured[0] == ["search-semantic", "--", task], captured[0]
