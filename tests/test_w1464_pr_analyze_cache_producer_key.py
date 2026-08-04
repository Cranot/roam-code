"""A cached verdict must not outlive the code that computed it.

``roam pr-analyze --cache`` keys its envelope cache on diff text, rules-file
bytes, block threshold, language override, and ``CACHE_VERSION``. Every one of
those is an INPUT. ``CACHE_VERSION`` looks like the exception but is not --
its own comment scopes it to "bump when the envelope shape changes", i.e. it
tags the serialization, not the logic that filled it in.

So nothing in the key named the producer, and the cached value is
``summary.verdict``, which ``_serve_from_cache`` turns into
``sys.exit(EXIT_GATE_BLOCK)``. Upgrade roam, keep ``.roam/pr-analyze-cache/``,
and a CI gate passes or fails a PR on a verdict the running code never
computed.

Measured on HEAD, one set of inputs, a fresh process either side of a change
to the module that derives the verdict::

    key BEFORE analyzer-code change: 03187bc91a144ac4...
    key AFTER  analyzer-code change: 03187bc91a144ac4...   <- identical
    bundle written by OLD logic, read under NEW logic's key:
        {'cache_hit': True, 'summary': {'verdict': 'SAFE'}}

The sibling compile-envelope cache had the same defect and was measured doing
the same thing with real wheels -- 13.9.0's envelope served to 13.10.0 under an
identical key -- and closed it with a two-part producer stamp. This is that fix
applied to the cache that was left behind, using the same two terms rather than
a third private variant of them.

Each key-moves assertion is paired with a key-holds control in the same file:
a fix that merely made the key unstable would destroy the cache while passing
every positive test, so identical inputs on identical code must still collide.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

from roam.commands.pr_analyze import cache as prcache
from roam.commands.pr_analyze.cache import _cache_key, _load_cache, _save_cache

_DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n x\n+y\n"


@pytest.fixture()
def rules(tmp_path):
    p = tmp_path / "rules.yml"
    p.write_text("rules: []\n", encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _clear_memo():
    """The fingerprint is memoized per process; tests model fresh processes."""
    prcache._ANALYZER_FINGERPRINT = None
    yield
    prcache._ANALYZER_FINGERPRINT = None


def _fresh_key(*args):
    """The key a newly-started ``roam`` would compute."""
    prcache._ANALYZER_FINGERPRINT = None
    return _cache_key(*args)


@pytest.fixture()
def bumped_analyzer_mtime():
    """Move the verdict-deriving module's mtime, then put it back.

    Models the dev/editable case: the logic changed, no version metadata did.
    """
    import roam.commands.cmd_pr_analyze as analyzer

    target = analyzer.__file__
    st = os.stat(target)

    def _bump():
        os.utime(target, (st.st_atime + 5000, st.st_mtime + 5000))

    try:
        yield _bump
    finally:
        os.utime(target, (st.st_atime, st.st_mtime))


# ---------------------------------------------------------------------------
# The producer must be in the key.
# ---------------------------------------------------------------------------


def test_key_moves_when_the_analyzer_code_changes(rules, bumped_analyzer_mtime):
    """Same inputs, different producer -> different key.

    Pre-fix these two were byte-identical.
    """
    before = _fresh_key(_DIFF, rules, 85, None)
    bumped_analyzer_mtime()
    after = _fresh_key(_DIFF, rules, 85, None)

    assert before != after


def test_stale_bundle_is_not_served_after_an_analyzer_change(tmp_path, rules, bumped_analyzer_mtime):
    """The consequence the key exists to prevent.

    Pre-fix the OLD bundle -- verdict and all -- came back under the NEW
    code's key, and ``_serve_from_cache`` would have exited on it.
    """
    cache_dir = tmp_path / "pr-analyze-cache"
    old_key = _fresh_key(_DIFF, rules, 85, None)
    _save_cache(cache_dir, old_key, {"cache_hit": True, "summary": {"verdict": "SAFE"}})

    bumped_analyzer_mtime()
    new_key = _fresh_key(_DIFF, rules, 85, None)

    assert _load_cache(cache_dir, new_key) is None


def test_key_moves_when_only_the_installed_version_moves(rules, monkeypatch):
    """Covers the case module mtimes cannot see.

    Nix/Guix/Bazel stores, ``tar --mtime=``, SOURCE_DATE_EPOCH-pinned image
    layers: the files are byte-different but timestamp-identical across
    releases. This is how the sibling compile cache was measured serving
    13.9.0's envelope to 13.10.0, so an mtime-only stamp would inherit the
    exact defect it was added to close.
    """
    import roam.plan.plan_cache as plan_cache

    monkeypatch.setattr(plan_cache, "_roam_version_stamp", lambda: "roam_code-13.9.0.dist-info")
    old = _fresh_key(_DIFF, rules, 85, None)

    monkeypatch.setattr(plan_cache, "_roam_version_stamp", lambda: "roam_code-13.10.0.dist-info")
    new = _fresh_key(_DIFF, rules, 85, None)

    assert old != new


def test_fingerprint_names_every_module_in_the_package(rules):
    """Discovery, not a hand-maintained list.

    A module added to ``roam.commands.pr_analyze`` later must be covered
    without anyone remembering this function exists -- the same
    freedom-from-discipline property ``graph_builder_identity`` prizes in its
    manifest-derived half.
    """
    import pkgutil

    import roam.commands.pr_analyze as pkg

    fp = prcache._analyzer_fingerprint()
    for mod in pkgutil.iter_modules(pkg.__path__):
        assert f"{pkg.__name__}.{mod.name}=" in fp
    assert "roam.commands.cmd_pr_analyze=" in fp


def test_undeterminable_producer_fails_closed_to_a_process_local_key(rules, monkeypatch):
    """When nothing about the producer can be established, do not share a key.

    An unknown producer identity must not resolve to "same producer". A miss
    costs a recomputation; the alternative costs a wrong verdict.
    """
    import roam.plan.plan_cache as plan_cache

    def _no_spec(name):
        raise ImportError(name)

    monkeypatch.setattr(importlib.util, "find_spec", _no_spec)
    monkeypatch.setattr(plan_cache, "_roam_version_stamp", lambda: "")

    first = _fresh_key(_DIFF, rules, 85, None)
    second = _fresh_key(_DIFF, rules, 85, None)

    assert first != second


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS -- the cache must still be a cache.
# ---------------------------------------------------------------------------


def test_identical_inputs_on_identical_code_still_collide(tmp_path, rules):
    """The whole point of a cache. A key that never repeats is not a fix."""
    first = _fresh_key(_DIFF, rules, 85, None)
    second = _fresh_key(_DIFF, rules, 85, None)
    assert first == second

    cache_dir = tmp_path / "pr-analyze-cache"
    _save_cache(cache_dir, first, {"cache_hit": True, "summary": {"verdict": "SAFE"}})
    assert _load_cache(cache_dir, second) is not None


def test_fingerprint_is_stable_within_a_process(rules):
    """Memoized: code cannot change under a running process."""
    assert prcache._analyzer_fingerprint() == prcache._analyzer_fingerprint()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d, r, t, lang: (d + "+extra\n", r, t, lang), id="diff"),
        pytest.param(lambda d, r, t, lang: (d, r, t + 5, lang), id="threshold"),
        pytest.param(lambda d, r, t, lang: (d, r, t, "python"), id="language"),
    ],
)
def test_input_terms_still_move_the_key(rules, mutate):
    """Pre-existing key behaviour must survive the added term."""
    base = _fresh_key(_DIFF, rules, 85, None)
    assert _fresh_key(*mutate(_DIFF, rules, 85, None)) != base


def test_rules_file_content_still_moves_the_key(rules):
    base = _fresh_key(_DIFF, rules, 85, None)
    rules.write_text("rules: [{id: x}]\n", encoding="utf-8")
    assert _fresh_key(_DIFF, rules, 85, None) != base
