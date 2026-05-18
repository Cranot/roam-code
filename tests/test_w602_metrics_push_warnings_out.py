"""W602 — ``_load_last_pr_analysis`` + ``_build_last_pr_block`` plumb ``warnings_out``.

The W448 / W589 / W592 / W593 / W595 / W596 / W597 / W598 / W599 / W600
/ W601 Pattern-2 substrate-hardening arc closes the silent-fallback
disclosure gaps on lease + permits + runs-ledger + runtime-daemon +
pr-analyze-cache + trace-ingest + config-hashes + signing substrates.
W602 closes the ``cmd_metrics_push`` CLI command's read-side silent-pass
sites — the pre-W602 ``_load_last_pr_analysis`` collapsed OSError +
JSONDecodeError into a single ``return None``, identical in shape to
the W598 ``_load_cache`` plumb.

W978 first-hypothesis decision (read source IN FULL, then decide):

* W597's hypothesis ("metrics push is fire-and-forget; the swallowed-
  None is about retry semantics") was WRONG. The silent-pass site at
  ``cmd_metrics_push.py:117`` is NOT the HTTP push path; it is the
  read-side ``_load_last_pr_analysis`` JSON-decode silent-None — same
  shape as W598's ``_load_cache``.
* W598's hypothesis ("push to external Prometheus/StatsD") was WRONG.
  ``_post_metrics`` returns a structured ``(False, code, str(exc))``
  tuple on every failure — already loud-by-tuple, no silent-pass to
  plumb.
* ``_capture_audit`` (lines 67-72) catches ``Exception`` and returns
  an explicit ``{"error": ..., "exit_code": ...}`` envelope — already
  loud-by-envelope (the ``audit_failed`` gate at line 388 surfaces it
  on the payload). No plumb needed there.

So the W602 closed enum lands on TWO helpers — ``_load_last_pr_analysis``
(primary target, 3 markers) and ``_build_last_pr_block`` (bonus
timestamp-parse silent-skip at line ~214, 1 marker).

Closed-enum markers (W978 first-hypothesis: only paths that exist):

  * ``metrics_push_last_pr_read_failed:<path>:<exc_class>:<detail>``
    (``_load_last_pr_analysis`` OSError on read — file on disk but
    unreadable; mirrors W598 ``pr_analyze_cache_read_failed``).
  * ``metrics_push_last_pr_corrupt:<path>:JSONDecodeError``
    (``_load_last_pr_analysis`` bytes parsed as something other than
    JSON; mirrors W598 ``pr_analyze_cache_corrupt:JSONDecodeError``).
  * ``metrics_push_last_pr_corrupt:<path>:NotAJsonObject``
    (``_load_last_pr_analysis`` JSON parsed cleanly but top-level
    value not a dict — would crash downstream ``_build_last_pr_block``
    at ``.get("summary")``; mirrors W598
    ``pr_analyze_cache_corrupt:NotAJsonObject``).
  * ``metrics_push_last_pr_timestamp_parse_failed:<ts>:<exc_class>:<detail>``
    (``_build_last_pr_block`` ``datetime.fromisoformat`` raised on a
    malformed timestamp — age_days / stale fields are silently
    absent from the rendered block).

Intentional-absence decisions (W978 + "Make fallback chains loud"):

* ``_load_last_pr_analysis`` missing-file path returns ``None``
  SILENTLY — the common, expected path before ``roam pr-analyze``
  has ever been run. Warning would train operators to ignore real
  signals (mirrors W597 ``daemon_running`` missing-pidfile + W598
  ``_load_cache`` cold-cache discipline).
* ``_capture_audit`` ``except Exception`` already returns an explicit
  ``{"error": ..., "exit_code": ...}`` envelope; not a silent-pass
  and explicitly out of W602 scope.
* ``_post_metrics`` HTTP failure returns a structured
  ``(False, <code>, <text>)`` tuple; already loud-by-tuple,
  explicitly out of W602 scope.

Caller audit (W602 audit-only — no caller modifications):

The 2 plumbed helpers have exactly ONE call-site each, both inside
``cmd_metrics_push.metrics_push`` (the CLI entry point):

  * ``_load_last_pr_analysis()`` at line ~391
  * ``_build_last_pr_block(last_pr_envelope)`` reached via
    ``_build_payload`` at line ~252

Neither caller threads ``warnings_out`` today — W602 is audit-only on
the producer side. A future wave can opt the CLI entry into threading
the bucket and surfacing the markers on the JSON envelope.

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589/W592/W593/W595/
W596/W597/W598/W599/W600/W601).

Network test discipline (per wave brief): every HTTP failure mode is
exercised via monkeypatch ONLY — no real network I/O. ``_post_metrics``
is not in scope (loud-by-tuple), but a sanity test asserts the W602
plumb does not introduce any new ``requests.post`` or
``urllib.request.urlopen`` call paths.
"""

from __future__ import annotations

import ast
import json as _json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.commands.cmd_metrics_push import (  # noqa: E402
    _build_last_pr_block,
    _load_last_pr_analysis,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp_path with no ``.roam/`` directory — clean cold state.

    Chdir to it so the default-path resolution
    (``DEFAULT_LAST_PR_PATH = .roam/last-pr-analysis.json``) lands inside
    the fixture rather than the real repo.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def last_pr_path(fresh_repo: Path) -> Path:
    """The ``.roam/last-pr-analysis.json`` path under the fresh repo.

    Returns the path WITHOUT creating the file or the parent dir, so
    individual tests can choose how to materialise it.
    """
    return fresh_repo / ".roam" / "last-pr-analysis.json"


# ===========================================================================
# (1) Happy path — valid envelope, no warnings
# ===========================================================================


def test_clean_happy_path_no_warnings(last_pr_path: Path) -> None:
    """A valid JSON envelope on disk → no warnings, dict returned.

    Sanity check that W602 plumbing only fires on degenerate paths.
    """
    last_pr_path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"summary": {"verdict": "REVIEW", "blast_radius": 12}}
    last_pr_path.write_text(_json.dumps(envelope), encoding="utf-8")

    warnings: list[str] = []
    result = _load_last_pr_analysis(warnings_out=warnings)

    assert result == envelope
    assert warnings == [], f"clean _load_last_pr_analysis on a valid file must NOT emit warnings; got {warnings!r}"


# ===========================================================================
# (2) Missing file is intentional-absence — SILENT (no marker)
# ===========================================================================


def test_missing_file_is_intentional_silent(fresh_repo: Path) -> None:
    """Cold start (no ``.roam/last-pr-analysis.json``) → None, NO marker.

    Mirrors W598 ``_load_cache`` cold-cache discipline + W597
    ``daemon_running`` missing-pidfile discipline. Warning on the
    common, expected pre-pr-analyze path would train operators to
    ignore real warnings.
    """
    warnings: list[str] = []
    result = _load_last_pr_analysis(warnings_out=warnings)

    assert result is None
    assert warnings == [], (
        f"missing last-pr-analysis.json must be SILENT — it is the expected cold-start path. Got {warnings!r}."
    )


# ===========================================================================
# (3) OSError on read emits read_failed marker
# ===========================================================================


def test_oserror_emits_read_failed_marker(last_pr_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``PermissionError`` on ``Path.read_text`` emits a closed-enum marker.

    The function still returns ``None`` (caller contract preserved).
    Monkeypatches ``Path.read_text`` to raise on the resolved last-pr
    path so only THIS read sees the synthetic error.
    """
    last_pr_path.parent.mkdir(parents=True, exist_ok=True)
    last_pr_path.write_text('{"summary": {"verdict": "REVIEW"}}', encoding="utf-8")
    target_resolved = last_pr_path.resolve()
    original_read_text = Path.read_text

    def _raising_read_text(self, *args, **kwargs):
        try:
            resolved = self.resolve()
        except OSError:
            resolved = self
        if resolved == target_resolved:
            raise PermissionError("synthetic-EACCES from W602 test")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raising_read_text)

    warnings: list[str] = []
    result = _load_last_pr_analysis(warnings_out=warnings)

    # Caller contract preserved — None on read failure.
    assert result is None
    # Exactly one closed-enum marker.
    assert len(warnings) == 1, f"expected one read_failed marker; got {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("metrics_push_last_pr_read_failed:"), msg
    assert "PermissionError" in msg, msg
    assert "synthetic-EACCES from W602 test" in msg, msg


def test_generic_oserror_emits_read_failed_marker(last_pr_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A generic ``OSError`` on read also funnels through the marker.

    Confirms the ``except OSError`` clause catches the broader family
    (IsADirectoryError, IOError, etc.) — not just ``PermissionError``.
    """
    last_pr_path.parent.mkdir(parents=True, exist_ok=True)
    last_pr_path.write_text("{}", encoding="utf-8")
    target_resolved = last_pr_path.resolve()
    original_read_text = Path.read_text

    def _raising_read_text(self, *args, **kwargs):
        try:
            resolved = self.resolve()
        except OSError:
            resolved = self
        if resolved == target_resolved:
            raise OSError("synthetic generic OSError")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raising_read_text)

    warnings: list[str] = []
    result = _load_last_pr_analysis(warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("metrics_push_last_pr_read_failed:"), msg
    assert "OSError" in msg, msg


# ===========================================================================
# (4) JSONDecodeError emits corrupt:JSONDecodeError marker
# ===========================================================================


def test_corrupt_json_emits_corrupt_marker(last_pr_path: Path) -> None:
    """Malformed bytes → ``metrics_push_last_pr_corrupt:...:JSONDecodeError``.

    The function still returns ``None`` (caller contract preserved).
    """
    last_pr_path.parent.mkdir(parents=True, exist_ok=True)
    last_pr_path.write_text("not json {", encoding="utf-8")

    warnings: list[str] = []
    result = _load_last_pr_analysis(warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("metrics_push_last_pr_corrupt:"), msg
    assert "JSONDecodeError" in msg, msg


# ===========================================================================
# (5) Non-dict JSON top level emits corrupt:NotAJsonObject marker
# ===========================================================================


def test_non_dict_top_level_emits_corrupt_marker(last_pr_path: Path) -> None:
    """JSON parses but top-level is a list → ``corrupt:NotAJsonObject``.

    The downstream ``_build_last_pr_block`` indexes ``.get("summary")``
    / ``.get("_meta")`` / ``.get("audit_trail")`` — a non-dict payload
    would crash there. The function returns ``None`` to short-circuit
    that crash AND emits the marker so operators see the cache-poison
    distinct from a JSON-parse failure.
    """
    last_pr_path.parent.mkdir(parents=True, exist_ok=True)
    # Top-level list is valid JSON but the wrong shape.
    last_pr_path.write_text("[1, 2, 3]", encoding="utf-8")

    warnings: list[str] = []
    result = _load_last_pr_analysis(warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("metrics_push_last_pr_corrupt:"), msg
    assert "NotAJsonObject" in msg, msg


def test_non_dict_top_level_string_emits_corrupt_marker(last_pr_path: Path) -> None:
    """JSON parses to a bare string → ``corrupt:NotAJsonObject``.

    Confirms the ``isinstance(raw, dict)`` gate fires on every non-dict
    JSON top-level shape (string / int / null / list).
    """
    last_pr_path.parent.mkdir(parents=True, exist_ok=True)
    last_pr_path.write_text('"just a string"', encoding="utf-8")

    warnings: list[str] = []
    result = _load_last_pr_analysis(warnings_out=warnings)

    assert result is None
    assert len(warnings) == 1, warnings
    assert warnings[0].startswith("metrics_push_last_pr_corrupt:"), warnings[0]
    assert "NotAJsonObject" in warnings[0], warnings[0]


# ===========================================================================
# (6) _build_last_pr_block — timestamp parse silent-skip emits marker
# ===========================================================================


def test_timestamp_parse_fail_emits_marker() -> None:
    """A malformed ``_meta.timestamp`` → ``metrics_push_last_pr_timestamp_parse_failed``.

    Pre-W602 the ``except (TypeError, ValueError): pass`` clause
    silently dropped ``age_days`` / ``stale`` enrichment on a malformed
    timestamp with no operator-visible signal. W602 plumbs disclosure
    while preserving the silent enrichment-drop (caller contract).
    """
    envelope = {
        "summary": {"verdict": "BLOCK"},
        "_meta": {"timestamp": "not-a-real-iso-timestamp"},
    }

    warnings: list[str] = []
    block = _build_last_pr_block(envelope, warnings_out=warnings)

    # Caller contract preserved — block still renders; just no age fields.
    assert block["verdict"] == "BLOCK"
    assert "age_days" not in block, f"age_days must remain absent on parse-fail; got {block!r}"
    assert "stale" not in block, f"stale must remain absent on parse-fail; got {block!r}"

    # Exactly one closed-enum marker.
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("metrics_push_last_pr_timestamp_parse_failed:"), msg
    assert "not-a-real-iso-timestamp" in msg, msg


def test_timestamp_parse_happy_path_no_marker() -> None:
    """A valid ISO timestamp → no marker, age_days populated.

    Sanity check that the bonus plumb only fires on the silent-skip
    path and does not contaminate the success path with spurious
    markers. Mirrors W601's "POSIX happy path no marker" guard.
    """
    envelope = {
        "summary": {"verdict": "REVIEW"},
        "_meta": {"timestamp": "2026-05-15T12:00:00Z"},
    }

    warnings: list[str] = []
    block = _build_last_pr_block(envelope, warnings_out=warnings)

    assert "age_days" in block
    assert isinstance(block["age_days"], int)
    assert "stale" in block
    assert warnings == [], warnings


def test_no_timestamp_no_marker() -> None:
    """A missing ``_meta.timestamp`` → no marker (try-block not entered).

    The pre-W602 ``if ts:`` guard skips the try-block entirely when
    no timestamp is present; no silent-skip path is exercised. W602
    must not emit a marker on the absent-timestamp path.
    """
    envelope = {"summary": {"verdict": "BLOCK"}}

    warnings: list[str] = []
    block = _build_last_pr_block(envelope, warnings_out=warnings)

    assert "age_days" not in block
    assert "stale" not in block
    assert warnings == []


# ===========================================================================
# (7) Default warnings_out=None preserves silent behaviour
# ===========================================================================


def test_default_none_no_crash(last_pr_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling without ``warnings_out`` still works on every degenerate path.

    The 2 live callers of ``_load_last_pr_analysis`` and
    ``_build_last_pr_block`` (both inside ``metrics_push`` at lines
    ~391 / via ``_build_payload`` at line ~252) call with no kwarg
    and must NOT regress on any failure mode covered by the W602 plumb.
    """

    # (a) Default-args missing-file (silent cold-start).
    assert _load_last_pr_analysis() is None

    # (b) Default-args corrupt-JSON (silent).
    last_pr_path.parent.mkdir(parents=True, exist_ok=True)
    last_pr_path.write_text("not json {", encoding="utf-8")
    assert _load_last_pr_analysis() is None

    # (c) Default-args non-dict top-level (silent).
    last_pr_path.write_text("[1, 2]", encoding="utf-8")
    assert _load_last_pr_analysis() is None

    # (d) Default-args valid envelope (silent + dict returned).
    valid = {"summary": {"verdict": "REVIEW"}}
    last_pr_path.write_text(_json.dumps(valid), encoding="utf-8")
    assert _load_last_pr_analysis() == valid

    # (e) Default-args bad timestamp on _build_last_pr_block (silent).
    bad_ts_envelope = {
        "summary": {"verdict": "BLOCK"},
        "_meta": {"timestamp": "garbage"},
    }
    block = _build_last_pr_block(bad_ts_envelope)
    assert block["verdict"] == "BLOCK"
    assert "age_days" not in block


# ===========================================================================
# (8) Caller audit — no caller threads warnings_out today
# ===========================================================================


def test_callers_unmodified() -> None:
    """AST-check live callers of ``_load_last_pr_analysis`` + ``_build_last_pr_block``.

    W602 is additive — kw-only ``warnings_out`` params with default
    ``None``. Both helpers have at least one reachable invocation
    inside ``cmd_metrics_push.py``:

      * ``_load_last_pr_analysis`` is invoked from the CLI entry path.
        W607-DI (2026-05-18) wrapped the call site so the helper is
        now passed as a function REFERENCE to ``_run_check_di``
        rather than called directly (``_run_check_di("load_last_pr_analysis",
        _load_last_pr_analysis, default=None)``).
      * ``_build_last_pr_block()`` reached via ``_build_payload`` —
        the call is at line ~253.

    Neither caller threads the ``warnings_out`` kwarg today; the test
    pins that audit-only contract. A future refactor can opt a caller
    into threading the bucket; this test must be updated when that
    handoff is intentional.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "cmd_metrics_push.py"
    assert src_path.exists(), f"expected to find {src_path}"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    target_funcs = {"_load_last_pr_analysis", "_build_last_pr_block"}
    direct_call_count = 0
    substrate_ref_count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in target_funcs:
            direct_call_count += 1
            kwarg_names = [kw.arg for kw in node.keywords if kw.arg is not None]
            assert "warnings_out" not in kwarg_names, (
                f"caller in {src_path.name} at line {node.lineno} now "
                f"threads warnings_out into {name}; W602 was audit-"
                f"only — update this test if intentionally opted in."
            )
        # W607-DI substrate-CALL recognition: the helper may be passed
        # as a positional-argument REFERENCE to ``_run_check_di``.
        # That still counts as a reachable invocation -- ``_run_check_di``
        # invokes it via ``fn(*args, **kwargs)``. Without this branch a
        # W607-DI refactor would zero out the callsite count.
        if isinstance(func, ast.Name) and func.id == "_run_check_di":
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in target_funcs:
                    substrate_ref_count += 1
    # 2 reachable invocations (one per target function) inside the CLI
    # entry path -- direct call OR substrate-CALL reference.
    total = direct_call_count + substrate_ref_count
    assert total >= 2, (
        f"expected at least 2 reachable invocations of "
        f"{{_load_last_pr_analysis, _build_last_pr_block}} "
        f"(direct calls + W607-DI substrate refs); "
        f"found direct={direct_call_count} substrate_refs={substrate_ref_count}. "
        f"If a caller was removed, update this test."
    )


# ===========================================================================
# (9) Closed-enum subset — W978 first-hypothesis discipline
# ===========================================================================


def test_w978_closed_enum_subset() -> None:
    """AST-check ``cmd_metrics_push.py`` for the exact closed-enum marker set.

    W978 first-hypothesis discipline: every emitted marker must
    correspond to a real silent-fail code path. Inventing markers
    that no path can ever emit adds dead vocabulary that contaminates
    the audit-trail surface.

    The expected closed enum after W602:

      * ``metrics_push_last_pr_read_failed:``
      * ``metrics_push_last_pr_corrupt:`` (parameterised by
        ``JSONDecodeError`` and ``NotAJsonObject`` post-fix subtype)
      * ``metrics_push_last_pr_timestamp_parse_failed:``

    Forbidden markers — paths that DO NOT exist in cmd_metrics_push:

      * ``metrics_push_http_failed:`` — _post_metrics is loud-by-tuple,
        no silent-pass to disclose.
      * ``metrics_push_target_invalid:`` — no URL-validation step;
        the URL is forwarded to urllib.request.Request which raises
        loudly on malformed URLs.
      * ``metrics_push_retry_exhausted:`` — no retry loop exists;
        push is single-shot.
      * ``metrics_push_response_corrupt:`` — _post_metrics returns
        raw response_text excerpt, never parses response JSON.
      * ``metrics_push_audit_failed:`` — _capture_audit already
        returns a loud ``{"error": ..., "exit_code": ...}`` envelope.
      * ``metrics_push_config_not_found:`` / ``metrics_push_config_read_failed:``
        — no on-disk config file is loaded by metrics-push; ``--token``
        comes from env-var / flag and ``--endpoint`` is a string.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "cmd_metrics_push.py"
    source = src_path.read_text(encoding="utf-8")

    expected_markers = {
        "metrics_push_last_pr_read_failed:",
        "metrics_push_last_pr_corrupt:",
        "metrics_push_last_pr_timestamp_parse_failed:",
    }
    forbidden_markers = {
        "metrics_push_http_failed:",
        "metrics_push_target_invalid:",
        "metrics_push_retry_exhausted:",
        "metrics_push_response_corrupt:",
        "metrics_push_audit_failed:",
        "metrics_push_config_not_found:",
        "metrics_push_config_read_failed:",
    }

    for marker in expected_markers:
        assert marker in source, (
            f"expected marker prefix {marker!r} missing from cmd_metrics_push.py — did the W602 plumb get reverted?"
        )
    for marker in forbidden_markers:
        assert marker not in source, (
            f"forbidden marker prefix {marker!r} present in "
            f"cmd_metrics_push.py — this marker has no corresponding "
            f"silent-pass code path. W978 first-hypothesis discipline: "
            f"only plumb markers for paths that actually exist."
        )


# ===========================================================================
# (10) Function-signature audit — kw-only warnings_out
# ===========================================================================


def test_signatures_carry_kw_only_warnings_out() -> None:
    """AST-check both helpers declare ``warnings_out`` as kw-only.

    Mirrors W598 / W599 / W600 / W601 signature-audit patterns. Kw-only
    declaration is the back-compat-preserving signal that existing
    positional callers (1 each) are unaffected.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "cmd_metrics_push.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    targets = {"_load_last_pr_analysis", "_build_last_pr_block"}
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            found.add(node.name)
            kwonly_names = [a.arg for a in node.args.kwonlyargs]
            assert "warnings_out" in kwonly_names, (
                f"{node.name} must declare ``warnings_out`` as a kw-only parameter (got kwonly={kwonly_names!r})"
            )
            for child in ast.walk(node):
                if isinstance(child, (ast.Yield, ast.YieldFrom)):
                    raise AssertionError(
                        f"{node.name} contains a yield — W602 must not turn the helpers into generators"
                    )

    missing = targets - found
    assert not missing, f"expected to find functions {missing!r} in cmd_metrics_push.py"


# ===========================================================================
# (11) Network discipline — no live urllib calls during the W602 tests
# ===========================================================================


def test_no_network_io_in_w602_plumb_paths() -> None:
    """AST-confirm the W602-plumbed helpers do NOT introduce HTTP calls.

    The W602 plumb lives on TWO helpers — ``_load_last_pr_analysis``
    (file-read only) and ``_build_last_pr_block`` (pure-Python timestamp
    arithmetic). Neither should reach for ``urllib.request.urlopen`` /
    ``urllib.request.Request`` / network sockets.

    ``_post_metrics`` is the only function in the file that legitimately
    calls ``urllib.request.urlopen`` — it is explicitly OUT of W602
    scope (loud-by-tuple on every failure mode). This guard pins that
    no HTTP call leaked into the W602 plumb paths.
    """
    src_path = repo_root() / "src" / "roam" / "commands" / "cmd_metrics_push.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    # Network-flavoured attribute names. Restricted to attr.value identifiers
    # tied to the urllib/requests/socket module families so dict.get() and
    # str.replace() don't false-positive.
    network_attr_names = {"urlopen", "Request"}
    network_qualifiers = {"urllib", "request", "requests", "socket", "httpx"}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_load_last_pr_analysis",
            "_build_last_pr_block",
        }:
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    fn = child.func
                    if isinstance(fn, ast.Attribute):
                        # Forbid urllib-family attr calls (urlopen / Request).
                        if fn.attr in network_attr_names:
                            raise AssertionError(
                                f"{node.name} now invokes {fn.attr!r} at "
                                f"line {child.lineno} — W602 plumb must "
                                f"be I/O-free beyond file read."
                            )
                        # Forbid <network_module>.<anything>() calls
                        # (e.g. requests.post / requests.get / socket.create).
                        target = fn.value
                        if isinstance(target, ast.Name) and target.id in network_qualifiers:
                            raise AssertionError(
                                f"{node.name} now invokes "
                                f"{target.id}.{fn.attr}() at line "
                                f"{child.lineno} — W602 plumb must be "
                                f"I/O-free beyond file read."
                            )
                    elif isinstance(fn, ast.Name):
                        if fn.id in network_attr_names:
                            raise AssertionError(
                                f"{node.name} now invokes {fn.id!r} at "
                                f"line {child.lineno} — W602 plumb must "
                                f"be I/O-free beyond file read."
                            )


# ===========================================================================
# (12) Round-trip — happy path with timestamp produces age_days
# ===========================================================================


def test_round_trip_load_then_build(last_pr_path: Path) -> None:
    """End-to-end: write valid envelope, load, build block — no warnings.

    Smoke test that the W602 plumb does not interfere with the
    documented happy-path semantic (valid envelope → dict → block with
    age_days + stale).
    """
    last_pr_path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "summary": {
            "verdict": "REVIEW",
            "blast_radius": 5,
            "ai_likelihood": 30,
            "rule_violations": 0,
            "high_severity_critique": 0,
        },
        "ai_likelihood": {"primary_language": "python"},
        "_meta": {"timestamp": "2026-05-01T00:00:00Z"},
    }
    last_pr_path.write_text(_json.dumps(envelope), encoding="utf-8")

    load_warnings: list[str] = []
    build_warnings: list[str] = []

    loaded = _load_last_pr_analysis(warnings_out=load_warnings)
    assert loaded == envelope
    assert load_warnings == []

    block = _build_last_pr_block(loaded, warnings_out=build_warnings)
    assert block["verdict"] == "REVIEW"
    assert block["primary_language"] == "python"
    assert "age_days" in block
    assert isinstance(block["age_days"], int)
    assert "stale" in block
    assert build_warnings == []
