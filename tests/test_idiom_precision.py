"""Precision guards for the python-idiom detectors (2026-06-05 deep-verify
dogfood). Three detectors self-flagged or over-matched when run on their own /
real source; these pin the fixes so the `roam verify --deep` patterns surface
stays high-signal.
"""

from __future__ import annotations

import re

from roam.catalog.python_idioms import (
    _LAMBDA_IN_LOOP_RE,
    _match_in_doc_or_comment,
    _strip_strings_and_comments,
)


def test_match_in_doc_or_comment():
    """The shared helper that keeps RAW-text detectors (logger-fstring,
    regex-alt-join) from flagging their OWN documentation."""
    code = (
        "real = 1\n"
        '    # logger.info(f"a={a}") full-line comment\n'
        "def f():\n"
        '    """docstring mentions re.compile("|".join(xs))."""\n'
        "    return real\n"
    )
    assert _match_in_doc_or_comment(code, code.index("logger.info")) is True  # full-line comment
    assert _match_in_doc_or_comment(code, code.index("re.compile")) is True  # in docstring
    assert _match_in_doc_or_comment(code, code.index("real = 1")) is False  # real code


def _lambda_in_loop_flags(code: str) -> list[str]:
    """Mirror detect_lambda_in_loop's match + capture check on raw code."""
    t = _strip_strings_and_comments(code)
    out = []
    for m in _LAMBDA_IN_LOOP_RE.finditer(t):
        var = m.group(1)
        le = t.find("\n", m.end())
        tail = t[m.end() : (le if le != -1 else len(t))]
        if re.search(rf"\b{re.escape(var)}\b", tail):
            out.append(var)
    return out


def test_lambda_in_loop_skips_non_capturing():
    # SAFE: a sort-key lambda after an unrelated loop never captures `nb`.
    safe = "    for nb in adj:\n        pass\n    xs.sort(key=lambda c: -len(c))\n"
    assert _lambda_in_loop_flags(safe) == []
    # REAL late-binding bug: the lambda captures the loop var `i`.
    bug = "    for i in range(3):\n        cbs.append(lambda: f(i))\n"
    assert _lambda_in_loop_flags(bug) == ["i"]


# ---- applicability gate (content-driven detector selection) ----


def test_applicable_idiom_detectors_is_content_driven():
    """Only detectors whose trigger token is present run; generic ones always
    run. Makes the deep-verify sweep fire just the checks the change can trip."""
    from roam.catalog.python_idioms import PYTHON_IDIOM_DETECTORS, applicable_idiom_detectors

    ids = lambda txt: {t for t, _w, _f in applicable_idiom_detectors(txt)}
    plain = ids("def f(x):\n    return x + 1\n")
    assert "py-pandas-iterrows" not in plain  # no pandas → skipped
    assert "py-django-n1" not in plain  # no django → skipped
    assert "py-regex-alt-join" not in plain  # no re.compile → skipped
    # generic (no trigger) detectors always run
    assert "py-mutable-default-arg" in plain
    # content-present → detector included
    assert "py-regex-alt-join" in ids("p = re.compile(x)")
    assert "py-django-n1" in ids("User.objects.filter(x)")
    assert "py-lambda-in-loop" in ids("g = lambda y: y")
    # plain code runs strictly fewer detectors than the full registry
    assert len(plain) < len(PYTHON_IDIOM_DETECTORS)


# ---- loop-body performance idioms (2026-06-11 wave) ----


def _hits(regex, code):
    from roam.catalog.python_idioms import _strip_strings_and_comments as strip

    return list(regex.finditer(strip(code)))


def test_manual_counter_in_loop():
    from roam.catalog.python_idioms import _MANUAL_COUNTER_IN_LOOP_RE as R

    bug = "    for x in xs:\n        counts[x] = counts.get(x, 0) + 1\n"
    assert _hits(R, bug)
    # different dict on the right side — not the counting idiom
    safe = "    for x in xs:\n        counts[x] = other.get(x, 0) + 1\n"
    assert not _hits(R, safe)
    # outside any loop — single increment is fine
    safe2 = "    counts[x] = counts.get(x, 0) + 1\n"
    assert not _hits(R, safe2)


def test_quadratic_list_concat_in_loop():
    from roam.catalog.python_idioms import _LIST_REASSIGN_CONCAT_IN_LOOP_RE as R

    bug = "    for x in xs:\n        acc = acc + [x]\n"
    assert _hits(R, bug)
    safe = "    for x in xs:\n        acc = other + [x]\n"  # not self-concat
    assert not _hits(R, safe)
    safe2 = "    acc = acc + [x]\n"  # outside a loop
    assert not _hits(R, safe2)


def test_append_then_sort_in_loop():
    from roam.catalog.python_idioms import _APPEND_THEN_SORT_IN_LOOP_RE as R

    bug = "    for x in xs:\n        acc.append(x)\n        acc.sort()\n"
    assert _hits(R, bug)
    bug2 = "    for x in xs:\n        acc.append(x)\n        top = sorted(acc)[:3]\n"
    assert _hits(R, bug2)
    # sorting a DIFFERENT, per-iteration collection is legitimate
    safe = "    for g in groups:\n        acc.append(g)\n        ordered = sorted(g.items())\n"
    assert not _hits(R, safe)


def test_pop0_in_loop():
    from roam.catalog.python_idioms import _POP0_IN_LOOP_RE as R

    bug = "    while q:\n        item = q.pop(0)\n"
    assert _hits(R, bug)
    safe = "    item = q.pop(0)\n"  # one-off dequeue outside a loop
    assert not _hits(R, safe)
    safe2 = "    while q:\n        item = q.pop()\n"  # pop from the END is O(1)
    assert not _hits(R, safe2)


def test_deepcopy_in_loop():
    from roam.catalog.python_idioms import _DEEPCOPY_IN_LOOP_RE as R

    bug = "    for x in xs:\n        y = deepcopy(template)\n"
    assert _hits(R, bug)
    safe = "    y = deepcopy(template)\n    for x in xs:\n        pass\n"
    assert not _hits(R, safe)


def test_frame_concat_in_loop():
    from roam.catalog.python_idioms import _FRAME_CONCAT_IN_LOOP_RE as R

    bug = "    for chunk in chunks:\n        df = pd.concat([df, chunk])\n"
    assert _hits(R, bug)
    bug2 = "    for a in arrays:\n        out = np.vstack([out, a])\n"
    assert _hits(R, bug2)
    safe = "    df = pd.concat(parts)\n"  # single concat after collecting
    assert not _hits(R, safe)


def test_new_perf_idioms_are_registered_with_triggers():
    """Registry + applicability-gate wiring for the six new detectors."""
    from roam.catalog.python_idioms import _IDIOM_TRIGGERS, PYTHON_IDIOM_DETECTORS

    new = {
        "py-manual-counter",
        "py-quadratic-list-concat",
        "py-sort-in-loop",
        "py-pop0-queue",
        "py-deepcopy-in-loop",
        "py-frame-concat-in-loop",
    }
    registered = {t for t, _w, _f in PYTHON_IDIOM_DETECTORS}
    assert new <= registered
    assert new <= set(_IDIOM_TRIGGERS), "every new detector must carry a trigger gate"
