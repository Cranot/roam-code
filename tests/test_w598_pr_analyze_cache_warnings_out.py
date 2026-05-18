"""W598 — ``_load_cache`` plumbs ``warnings_out`` into pr-analyze cache reader.

W448 added ``warnings_out`` to ``read_lease``. W589/W592 closed the lease
sibling cluster. W593/W595 closed the permits cluster. W596 closed the
runs-ledger cluster (``read_run_meta`` + bonus ``read_run_events``). W597
closed the runtime-daemon cluster (``daemon_state`` + bonus
``daemon_running``). W598 closes the pr-analyze-cache reader:
``_load_cache`` previously swallowed ``(OSError, json.JSONDecodeError)``
with a bare ``return None`` and converted "cache file not on disk"
(legitimate cold-cache sentinel) / "cache file unreadable" / "malformed
JSON" / "top-level not a dict" into one indistinguishable None.

Marker shape mirrors W595's ``read_permit`` / W596's ``read_run_meta`` /
W597's ``daemon_state`` closed-enum shape with a ``pr_analyze_cache_``
prefix so a caller threading the same bucket through multiple substrate
read sites sees a uniform marker vocabulary.

Closed-enum kinds:

  * ``pr_analyze_cache_read_failed:<path>:<exc_class>:<detail>``
  * ``pr_analyze_cache_corrupt:<path>:JSONDecodeError``
  * ``pr_analyze_cache_corrupt:<path>:NotAJsonObject``

Intentional-absence decision (W978 + "Make fallback chains loud"):
missing cache file is the documented cold-cache sentinel and does NOT
emit a warning. This mirrors W597's ``daemon_running`` missing-pidfile
discipline rather than W596's ``read_run_meta`` missing-meta.json
discipline. Cache miss is the common, expected path on first
invocation; warning here would train operators to ignore real
warnings.

The ``None`` return on every drop path is PRESERVED — the existing
caller contract (``cmd_pr_analyze._try_cache_envelope`` projects
cache-miss as "fall through to slow pipeline") is unchanged.
``warnings_out=None`` (default) preserves the pre-W598 silent-drop
behaviour.

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589 / W592 / W593 /
W595 / W596 / W597).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.commands.pr_analyze.cache import _cache_path, _load_cache  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cache_project(tmp_path: Path) -> Path:
    """A minimal cache dir for a single key.

    Tests resolve the key file via ``_cache_path(cache_dir, key)`` so
    the on-disk path is the same one ``_load_cache`` will compute.
    """
    cache_dir = tmp_path / "w598_cache"
    cache_dir.mkdir()
    return cache_dir


# ===========================================================================
# (1) Happy path — clean read emits no warning
# ===========================================================================


def test_clean_emits_no_warning(cache_project: Path) -> None:
    """A normal read on a clean cache entry appends nothing to ``warnings_out``.

    Sanity check that the W598 plumbing only fires on degenerate paths.
    """
    key = "happy"
    _cache_path(cache_project, key).write_text(
        '{"summary": {"verdict": "SAFE"}, "cache_hit": false}',
        encoding="utf-8",
    )

    warnings: list[str] = []
    loaded = _load_cache(cache_project, key, warnings_out=warnings)

    assert loaded is not None, "clean read must return a dict"
    assert loaded == {"summary": {"verdict": "SAFE"}, "cache_hit": False}
    assert warnings == [], f"clean _load_cache must NOT emit warnings; got {warnings!r}"


# ===========================================================================
# (2) Missing file = cold-cache sentinel — NO warning
# ===========================================================================


def test_missing_file_treated_as_cold_cache(cache_project: Path) -> None:
    """Missing cache file is the documented cold-cache sentinel — NO warning.

    Intentional-absence decision (W978 first-hypothesis discipline +
    "Make fallback chains loud" / "Distinguish intentional absence"):
    a cache miss is the common, expected path on first invocation. The
    W597 ``daemon_running`` missing-pidfile discipline is the right
    analogue here (legitimate "not running" sentinel → no warning),
    NOT W596's ``read_run_meta`` missing-meta.json discipline (an
    operational anomaly worth surfacing). Warning every cache miss
    would train operators to ignore real warnings.

    The ``None`` return is preserved — caller contract unchanged.
    """
    key = "never-written"
    path = _cache_path(cache_project, key)
    assert not path.exists()

    warnings: list[str] = []
    result = _load_cache(cache_project, key, warnings_out=warnings)

    assert result is None, "cache miss must still return None (existing contract)"
    assert warnings == [], (
        f"missing cache file is the documented cold-cache sentinel; must NOT emit warnings. got {warnings!r}"
    )


# ===========================================================================
# (3) Corrupt JSON emits ``pr_analyze_cache_corrupt:...:JSONDecodeError``
# ===========================================================================


def test_corrupt_json_emits_corrupt_marker(cache_project: Path) -> None:
    """Malformed JSON emits ``pr_analyze_cache_corrupt:<path>:JSONDecodeError``.

    Marker prefix mirrors W595's ``permit_corrupt:`` / W596's
    ``run_meta_corrupt:`` / W597's ``daemon_state_corrupt:`` shape so a
    caller grepping substrate warnings sees one uniform vocabulary.
    Cache poisoning (corrupt JSON on disk) is a real operational risk:
    disk corruption, partial-write on crash, manual edit.
    """
    key = "corrupt"
    _cache_path(cache_project, key).write_text("{bad json", encoding="utf-8")

    warnings: list[str] = []
    result = _load_cache(cache_project, key, warnings_out=warnings)

    assert result is None, "corrupt cache must return None (existing contract)"
    assert len(warnings) == 1, f"expected one corrupt-cache warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("pr_analyze_cache_corrupt:"), msg
    assert "JSONDecodeError" in msg, msg


# ===========================================================================
# (4) OSError on read emits ``pr_analyze_cache_read_failed:`` marker
# ===========================================================================


def test_oserror_emits_read_failed_marker(cache_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-decode OSError on read_text emits ``pr_analyze_cache_read_failed:``.

    Monkeypatches ``Path.read_text`` to raise ``PermissionError`` for
    the cache file path. The file EXISTS on disk (so we get past the
    ``not p.exists()`` short-circuit) but read fails.
    """
    key = "permission-denied"
    cache_file = _cache_path(cache_project, key)
    cache_file.write_text('{"summary": {}}', encoding="utf-8")
    cache_file_resolved = cache_file.resolve()

    original_read_text = Path.read_text

    def _raising_read_text(self, *args, **kwargs):
        try:
            resolved = self.resolve()
        except OSError:
            resolved = self
        if resolved == cache_file_resolved:
            raise PermissionError("synthetic-EACCES from W598 test")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raising_read_text)

    warnings: list[str] = []
    result = _load_cache(cache_project, key, warnings_out=warnings)

    assert result is None, "read_text failure must preserve None return; got non-None"
    assert len(warnings) == 1, f"expected one pr_analyze_cache_read_failed warning; got {len(warnings)}: {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("pr_analyze_cache_read_failed:"), msg
    assert "PermissionError" in msg, msg
    assert "synthetic-EACCES from W598 test" in msg, msg


# ===========================================================================
# (5) Non-dict top-level (wrong shape) emits ``...:NotAJsonObject``
# ===========================================================================


def test_wrong_shape_emits_corrupt_marker(cache_project: Path) -> None:
    """Top-level JSON string emits ``pr_analyze_cache_corrupt:<path>:NotAJsonObject``.

    Mirrors W595's / W596's / W597's fourth corrupt sub-case (top-level
    value is valid JSON but not a dict). Distinct structured marker so
    an operator can grep the bucket. The downstream
    ``_try_cache_envelope`` indexes ``cached["cache_hit"]`` and
    ``cached.get("summary")``, so a non-dict cached payload is cache
    poisoning, not cold cache — even if it parses as valid JSON.
    """
    key = "wrong-shape-string"
    _cache_path(cache_project, key).write_text('"a string not a dict"', encoding="utf-8")

    warnings: list[str] = []
    result = _load_cache(cache_project, key, warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("pr_analyze_cache_corrupt:"), msg
    assert "NotAJsonObject" in msg, msg


def test_wrong_shape_array_emits_corrupt_marker(cache_project: Path) -> None:
    """Top-level JSON array emits ``pr_analyze_cache_corrupt:<path>:NotAJsonObject``.

    Sibling of the string-shape test — confirms the NotAJsonObject
    marker fires for any non-dict top-level value (string, array,
    number, null), not just strings.
    """
    key = "wrong-shape-array"
    _cache_path(cache_project, key).write_text("[1, 2, 3]", encoding="utf-8")

    warnings: list[str] = []
    result = _load_cache(cache_project, key, warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("pr_analyze_cache_corrupt:"), msg
    assert "NotAJsonObject" in msg, msg


# ===========================================================================
# (6) Default ``warnings_out=None`` preserves silent behaviour
# ===========================================================================


def test_default_none_no_crash(cache_project: Path) -> None:
    """Default ``warnings_out=None`` returns None cleanly, no crash, no warnings.

    Existing callers (``cmd_pr_analyze._try_cache_envelope`` at
    cmd_pr_analyze.py:1356 + the ``test_pr_analyze_cache.py`` suite)
    call ``_load_cache(cache_dir, key)`` with no kwargs — they must
    NOT regress on any failure mode covered by the W598 plumb.
    """
    key = "default-none"
    cache_file = _cache_path(cache_project, key)

    # (a) Missing cache file — the most common silent-None path.
    result = _load_cache(cache_project, key)
    assert result is None

    # (b) Corrupt JSON — the second silent-None path.
    cache_file.write_text("{bad json", encoding="utf-8")
    result = _load_cache(cache_project, key)
    assert result is None

    # (c) Non-dict top-level — the third silent-None path.
    cache_file.write_text('"not a dict"', encoding="utf-8")
    result = _load_cache(cache_project, key)
    assert result is None

    # (d) Happy path with default-None still returns the dict.
    cache_file.write_text('{"summary": {"verdict": "SAFE"}}', encoding="utf-8")
    loaded = _load_cache(cache_project, key)
    assert loaded is not None
    assert loaded == {"summary": {"verdict": "SAFE"}}


# ===========================================================================
# (7) Caller-side audit: AST-check _load_cache caller path is unmodified
# ===========================================================================


def test_callers_unmodified() -> None:
    """AST-check that ``_load_cache``'s caller still works without threading.

    The W598 plumb is additive — a kw-only ``warnings_out`` parameter
    with default ``None``. The single live caller at
    ``cmd_pr_analyze._try_cache_envelope`` (cmd_pr_analyze.py:1356)
    invokes ``_load_cache(cache_dir_path, key)`` with no kwargs and
    must remain unchanged by W598. The audit confirms:

      * exactly one ``_load_cache(...)`` Call node in cmd_pr_analyze.py
      * that Call uses positional args only (no ``warnings_out`` kwarg)

    A future refactor can opt the caller into threading the bucket;
    this test pins the current "audit-only, caller unmodified"
    contract.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "cmd_pr_analyze.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    load_cache_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # Match both ``_load_cache(...)`` and ``module._load_cache(...)``.
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "_load_cache":
                load_cache_calls.append(node)

    assert len(load_cache_calls) == 1, (
        f"expected exactly one _load_cache call in cmd_pr_analyze.py; found {len(load_cache_calls)}"
    )
    call = load_cache_calls[0]
    kwarg_names = [kw.arg for kw in call.keywords if kw.arg is not None]
    assert "warnings_out" not in kwarg_names, (
        f"caller at cmd_pr_analyze.py:{call.lineno} now threads warnings_out; "
        f"W598 was audit-only — update this test if intentionally opted in."
    )


# ===========================================================================
# (8) Source-side audit: confirm closed-enum marker shape
# ===========================================================================


def test_load_cache_signature_carries_kw_only_warnings_out() -> None:
    """AST-check that ``_load_cache`` declares ``warnings_out`` as kw-only.

    Mirrors W597's ``test_callers_unmodified`` shape: the W598 plumb is
    read-only and must declare ``warnings_out`` as a keyword-only
    parameter (positional-arg back-compat) with no yields.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "pr_analyze" / "cache.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_cache":
            found = True
            # No yields — the reader must remain non-generator.
            for child in ast.walk(node):
                if isinstance(child, (ast.Yield, ast.YieldFrom)):
                    raise AssertionError(
                        "_load_cache contains a yield — W598 must not turn the silent-None reader into a generator"
                    )
            kwonly_names = [a.arg for a in node.args.kwonlyargs]
            assert "warnings_out" in kwonly_names, (
                f"_load_cache must declare ``warnings_out`` as a kw-only parameter (got kwonly={kwonly_names!r})"
            )
        elif isinstance(node, ast.AsyncFunctionDef) and node.name == "_load_cache":
            raise AssertionError("_load_cache became async — W598 must not change the synchronous-call contract")

    assert found, "expected to find _load_cache as a plain function def"


# ===========================================================================
# (9) Write paths untouched — W598 is read-only
# ===========================================================================


def test_save_cache_untouched() -> None:
    """W598 is read-only. ``_save_cache`` (the sibling WRITE path) is
    NOT plumbed with ``warnings_out`` — writes have a different failure
    profile (write-then-fsync, partial-write, disk-full) that W598's
    cache-reader vocabulary doesn't cover. Confirm by AST that
    ``_save_cache`` retains its pre-W598 signature.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "pr_analyze" / "cache.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_save_cache":
            found = True
            kwonly_names = [a.arg for a in node.args.kwonlyargs]
            assert "warnings_out" not in kwonly_names, (
                "_save_cache must NOT thread warnings_out — W598 scope is "
                "the cache READER only; write-path plumb is a separate wave"
            )

    assert found, "expected to find _save_cache as a plain function def"
