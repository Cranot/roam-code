"""L10 symbol-resolution prefetch: one batched call + dedupe.

`_probe_l10_symbol_resolution` resolves backticked symbols in a task at compile
time. The earlier shape issued up to five sequential `roam search` subprocesses
and could fill its 5-symbol cap with the SAME symbol named repeatedly. These
pin the fixed contract:

  1. Unique backticked symbols are resolved in ONE `roam batch-search`
     subprocess, never N sequential `roam search` calls.
  2. A symbol named K times is resolved once (order-preserving dedupe), so the
     [:5] cap counts distinct symbols — not repeats of one.

Both are regression-invariants for the L10 path (compiler.py:_probe_l10_*).
"""

from __future__ import annotations

import roam.plan.compiler as M


def _fake_batch(monkeypatch):
    """Stub `_run_roam` to record every invocation and return a batch-search
    envelope echoing one src/ hit per queried symbol. Returns the call log."""
    calls: list[list[str]] = []

    def _stub(cli_args, cwd, timeout=6.0):
        calls.append(list(cli_args))
        # batch-search groups rows by query under results[<query>].
        queried = cli_args[1:]  # drop the "batch-search" verb
        return {
            "results": {
                q: [{"name": q, "file_path": f"src/roam/{q}.py", "line_start": 7, "kind": "function"}] for q in queried
            }
        }

    monkeypatch.setattr(M, "_run_roam", _stub)
    return calls


def test_single_batch_search_subprocess_for_many_symbols(monkeypatch) -> None:
    calls = _fake_batch(monkeypatch)
    task = "Compare `alpha`, `beta`, `gamma`, and `delta` then summarize."
    out = M._probe_l10_symbol_resolution(task, cwd=".")

    assert out is not None
    # Exactly one subprocess, and it is the batched verb — not per-symbol search.
    assert len(calls) == 1
    assert calls[0][0] == "batch-search"
    # All four distinct symbols were passed to the single call.
    assert set(calls[0][1:]) == {"alpha", "beta", "gamma", "delta"}
    assert {r["symbol"] for r in out["resolved_symbols"]} == {"alpha", "beta", "gamma", "delta"}


def test_repeated_symbol_resolved_once_order_preserved(monkeypatch) -> None:
    calls = _fake_batch(monkeypatch)
    # `foo` named 6×, `bar` once: without dedupe the [:5] cap would be all `foo`.
    task = "`foo` then `foo` and `foo`; also `bar`; `foo`, `foo`, `foo`."
    out = M._probe_l10_symbol_resolution(task, cwd=".")

    assert out is not None
    assert len(calls) == 1
    # The single batch call carries each symbol exactly once, first-seen order.
    assert calls[0][1:] == ["foo", "bar"]
    syms = [r["symbol"] for r in out["resolved_symbols"]]
    assert syms == ["foo", "bar"]


def test_cap_counts_distinct_symbols_not_repeats(monkeypatch) -> None:
    calls = _fake_batch(monkeypatch)
    # 7 distinct symbols, several repeated — cap keeps the first 5 DISTINCT.
    task = "`a` `b` `a` `c` `d` `b` `e` `f` `g` `a`"
    M._probe_l10_symbol_resolution(task, cwd=".")

    assert len(calls) == 1
    assert calls[0][1:] == ["a", "b", "c", "d", "e"]


def test_no_backticks_skips_subprocess(monkeypatch) -> None:
    calls = _fake_batch(monkeypatch)
    out = M._probe_l10_symbol_resolution("plain task with no symbols", cwd=".")
    assert out is None
    assert calls == []
