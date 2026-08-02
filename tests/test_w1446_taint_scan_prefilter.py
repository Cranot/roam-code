"""W1446 — the substring pre-filter in ``_scan_hits`` must be free.

``_scan_hits`` ran one whole-file regex scan per (rule, file, name). Measured on
this repo that was 292,221 scans of which 98.1% matched nothing, and it made
``taint`` 87.4s — 94%+ of the whole ``reachability-triage`` service report.

A pre-check (``if name not in masked_text: continue``) took that to 15.6s. It is
only correct because ``_dotted_name_pattern`` compiles to
``(?<!\\w)re.escape(name)(?!\\w)``: the pattern ADDS boundary lookarounds and
never widens what can match, so a literal substring occurrence is a strict
NECESSARY condition for a match.

That soundness argument is the thing worth pinning. These tests prove it by
DIFFERENTIAL EXECUTION against a reference implementation with no pre-check,
rather than asserting the reasoning is right — if anyone ever loosens
``_dotted_name_pattern`` (adds a ``\\b``-style alternation, case-insensitivity,
or a fuzzy separator), the necessary-condition argument silently breaks and the
scanner starts MISSING taint findings. A missed finding is invisible; this test
makes it loud.
"""

from __future__ import annotations

import re

from roam.security import taint_engine
from roam.security.taint_engine import _dotted_name_pattern, _scan_hits


def _reference_scan_hits(masked_text: str, names: list[str]) -> list[tuple[str, int]]:
    """``_scan_hits`` exactly as it was BEFORE the pre-filter — the oracle."""
    hits: list[tuple[str, int]] = []
    for name in names:
        if not name:
            continue
        is_execute_sink = "execute" in name.lower()
        for m in _dotted_name_pattern(name).finditer(masked_text):
            if is_execute_sink and taint_engine._looks_like_parameterized_db_call(masked_text, m.end()):
                continue
            line = masked_text.count("\n", 0, m.start()) + 1
            hits.append((name, line))
    return hits


# Text chosen so names land in every position that matters: bare call, dotted
# call, embedded in a longer identifier, as a substring of another word, at the
# very start of the text, and on the final line with no trailing newline.
CORPUS = """\
import os
value = request.args.get("q")
cursor.execute(sql)
mycursor.execute_batch(sql)
self.cursor.execute(sql, params)
os.system(value)
subprocess_system_helper(value)
system = "not a call"
eval(value)
prefix_eval_suffix(value)
data.eval
os.system(value)"""

NAMES = [
    "os.system",
    "system",
    "cursor.execute",
    "execute",
    "eval",
    "request.args.get",
    "subprocess.run",  # absent entirely
    "nonexistent.sink",  # absent entirely
    "sys",  # substring of nothing here but a prefix of "system"
    "",  # falsy name — skipped by both paths
]


def test_prefilter_is_semantics_preserving_on_a_dense_corpus() -> None:
    """Same hits, same order, same line numbers as the no-prefilter oracle."""
    assert _scan_hits(CORPUS, NAMES) == _reference_scan_hits(CORPUS, NAMES)


def test_prefilter_preserves_semantics_name_by_name() -> None:
    """Per-name equality, so a failure names the input that broke it."""
    for name in NAMES:
        assert _scan_hits(CORPUS, [name]) == _reference_scan_hits(CORPUS, [name]), (
            f"pre-filter changed results for {name!r}"
        )


def test_substring_without_word_boundary_still_does_not_match() -> None:
    """The pre-filter admits it; the regex must still reject it.

    ``system`` occurs inside ``subprocess_system_helper``, so the substring
    check passes and cannot be what rejects the hit. Guards against someone
    "simplifying" the regex away now that a substring test exists.
    """
    text = "subprocess_system_helper(value)\n"
    assert "system" in text  # pre-filter cannot reject this
    assert _scan_hits(text, ["system"]) == []
    assert _scan_hits(text, ["system"]) == _reference_scan_hits(text, ["system"])


def test_absent_name_never_reaches_the_regex() -> None:
    """The point of the change: no compile, no scan, when the name is absent.

    This is the perf guard. It is deterministic — a call count, not a
    wall-clock threshold — so it holds on any machine and in CI.
    """
    scanned: list[str] = []
    real = taint_engine._dotted_name_pattern

    def counting(name: str):  # type: ignore[no-untyped-def]
        scanned.append(name)
        return real(name)

    taint_engine._dotted_name_pattern = counting  # type: ignore[assignment]
    try:
        _scan_hits(CORPUS, ["subprocess.run", "nonexistent.sink", "os.system"])
    finally:
        taint_engine._dotted_name_pattern = real  # type: ignore[assignment]

    assert scanned == ["os.system"], f"absent names must not be scanned; scanned={scanned}"


def test_dotted_name_pattern_keeps_substring_occurrence_necessary() -> None:
    """The soundness precondition the pre-filter depends on.

    If this fails, ``_scan_hits`` is silently MISSING findings and the
    pre-filter must be removed. Asserts the compiled pattern still contains the
    escaped literal and carries no flag that would let it match text not
    containing that literal verbatim.
    """
    for name in ("os.system", "cursor.execute", "eval"):
        pat = _dotted_name_pattern(name)
        assert re.escape(name) in pat.pattern, (
            f"{name!r} no longer appears as a literal in {pat.pattern!r} — a "
            "substring occurrence may no longer be necessary for a match, "
            "which invalidates the pre-filter in _scan_hits"
        )
        assert not pat.flags & re.IGNORECASE, (
            f"{name!r} pattern became case-insensitive — the case-sensitive "
            "substring pre-filter in _scan_hits would now skip real matches"
        )
