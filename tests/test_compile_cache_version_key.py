"""The on-disk compile caches must not be shared across roam versions.

Upgrading roam does not move the target repo's HEAD, and nothing else in the
envelope-cache key moves either: the compiler-file mtime is only a proxy for
the tool version, and deployment schemes that normalize file timestamps (Nix /
Guix / Bazel stores, ``tar --mtime=``, SOURCE_DATE_EPOCH-pinned image layers)
leave it byte-identical across releases.

Measured 2026-08-04 against the real released wheels: with mtimes normalized,
roam 13.9.0 stored an envelope and roam 13.10.0 read it back verbatim under an
identical key — obligations and execution contract included — while every
surface reported success. Released 13.8/13.9/13.10 emit no execution contract
or review obligations at all, so a cross-version hit silently drops them.

Each positive assertion here is paired with a SAME-VERSION negative control:
"disable the cache" must not be able to pass this file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roam.plan import plan_cache
from roam.plan.compiler import (
    _envelope_cache_key,
    _envelope_cache_lookup,
    _envelope_cache_store,
    _plan_persist_key,
    _probe_neg_persist_key,
    _probe_pos_persist_key,
    compile_plan,
)

V_OLD = "roam_code-13.9.0.dist-info"
V_NEW = "roam_code-13.10.0.dist-info"


@pytest.fixture
def stamp(monkeypatch):
    """Drive the memoized installed-version stamp (a simulated upgrade).

    Setting the memo is exactly what an in-place upgrade does to the value:
    the same repo, the same HEAD, the same compiler-file mtime, a different
    installed version.
    """

    def _set(value: str) -> None:
        monkeypatch.setattr(plan_cache, "_ROAM_VERSION_STAMP", value, raising=False)

    _set(V_OLD)
    return _set


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".roam").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def alpha():\n    return 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


# ---- the stamp itself resolves a real install layout ----------------------


def test_version_stamp_found_in_wheel_layout(tmp_path):
    """A stamp that always returned "" would disarm every key below."""
    (tmp_path / "roam").mkdir()
    (tmp_path / "roam_code-13.10.0.dist-info").mkdir()
    assert plan_cache._version_stamp_from_site_dir(str(tmp_path)) == "roam_code-13.10.0.dist-info"


def test_version_stamp_distinguishes_versions(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "roam_code-13.9.0.dist-info").mkdir(parents=True)
    (new / "roam_code-13.10.0.dist-info").mkdir(parents=True)
    assert plan_cache._version_stamp_from_site_dir(str(old)) != plan_cache._version_stamp_from_site_dir(str(new))


def test_version_stamp_absent_is_empty_not_raising(tmp_path):
    """Source checkout / editable install: no dist-info, no crash."""
    assert plan_cache._version_stamp_from_site_dir(str(tmp_path)) == ""
    assert plan_cache._version_stamp_from_site_dir(str(tmp_path / "does-not-exist")) == ""


# ---- envelope cache key ---------------------------------------------------


def test_envelope_key_differs_across_versions(stamp):
    stamp(V_OLD)
    before = _envelope_cache_key("add a retry to src/a.py", "deadbeef", "/repo")
    stamp(V_NEW)
    after = _envelope_cache_key("add a retry to src/a.py", "deadbeef", "/repo")
    assert before != after, "same envelope-cache key across a version change"


def test_envelope_key_stable_at_same_version(stamp):
    """NEGATIVE CONTROL: without this, a key that changed every call would
    pass the assertion above while destroying the cache."""
    stamp(V_NEW)
    a = _envelope_cache_key("add a retry to src/a.py", "deadbeef", "/repo")
    b = _envelope_cache_key("add a retry to src/a.py", "deadbeef", "/repo")
    assert a == b


def test_plan_persist_key_differs_across_versions(stamp):
    stamp(V_OLD)
    before = _plan_persist_key("add a retry to src/a.py", "/repo", "deadbeef")
    stamp(V_NEW)
    after = _plan_persist_key("add a retry to src/a.py", "/repo", "deadbeef")
    assert before != after, "same plan-cache key across a version change"


def test_plan_persist_key_stable_at_same_version(stamp):
    stamp(V_NEW)
    assert _plan_persist_key("t", "/repo", "head") == _plan_persist_key("t", "/repo", "head")


def test_run_roam_persist_key_differs_across_versions(stamp):
    stamp(V_OLD)
    before = plan_cache._run_roam_persist_key(["uses", "alpha"], "/repo")
    stamp(V_NEW)
    after = plan_cache._run_roam_persist_key(["uses", "alpha"], "/repo")
    assert before != after, "same probe-result key across a version change"


def test_run_roam_persist_key_stable_at_same_version(stamp):
    stamp(V_NEW)
    a = plan_cache._run_roam_persist_key(["uses", "alpha"], "/repo")
    b = plan_cache._run_roam_persist_key(["uses", "alpha"], "/repo")
    assert a == b


def test_probe_persist_keys_differ_across_versions(stamp):
    """Probe rows in the same SQLite file: a stale negative verdict from an
    older roam must not suppress a probe the upgraded one now answers."""
    stamp(V_OLD)
    neg_before = _probe_neg_persist_key("L1", "add a retry to src/a.py")
    pos_before = _probe_pos_persist_key("L1", "add a retry to src/a.py", ["src/a.py"])
    stamp(V_NEW)
    assert neg_before != _probe_neg_persist_key("L1", "add a retry to src/a.py")
    assert pos_before != _probe_pos_persist_key("L1", "add a retry to src/a.py", ["src/a.py"])


def test_probe_persist_keys_stable_at_same_version(stamp):
    stamp(V_NEW)
    assert _probe_neg_persist_key("L1", "t") == _probe_neg_persist_key("L1", "t")
    assert _probe_pos_persist_key("L1", "t", ["src/a.py"]) == _probe_pos_persist_key("L1", "t", ["src/a.py"])


# ---- end-to-end: an upgrade must not be SERVED the old envelope ----------


def _store_old_envelope(plan, cwd: str) -> dict:
    env = {
        "plan": {"task": plan.task, "procedure": "structural"},
        "obligations": ["GENERATED-BY-OLD-VERSION"],
        "execution_contract": {"generated_by": "OLD"},
    }
    _envelope_cache_store(plan, env, "contract", cwd)
    return env


def test_upgrade_does_not_serve_pre_upgrade_envelope(tmp_path, stamp):
    repo = _repo(tmp_path)
    cwd = str(repo)
    stamp(V_OLD)
    plan = compile_plan("add a retry to src/a.py", cwd=cwd)
    _store_old_envelope(plan, cwd)

    stamp(V_NEW)  # in-place upgrade: same repo, same HEAD, same file mtimes
    served = _envelope_cache_lookup(plan, cwd)
    assert served is None, f"upgraded roam was served a pre-upgrade envelope: {served}"


def test_same_version_still_hits_the_cache(tmp_path, stamp):
    """NEGATIVE CONTROL for the test above: the cache must still WORK.

    A fix that simply stopped caching (or keyed on something that changes
    every call) would satisfy `served is None` above; it cannot satisfy this.
    """
    repo = _repo(tmp_path)
    cwd = str(repo)
    stamp(V_NEW)
    plan = compile_plan("add a retry to src/a.py", cwd=cwd)
    _store_old_envelope(plan, cwd)

    served = _envelope_cache_lookup(plan, cwd)
    assert served is not None, "same-version lookup missed — the cache is disabled, not fixed"
    env, label = served
    assert env["obligations"] == ["GENERATED-BY-OLD-VERSION"]
    assert label == "contract"
