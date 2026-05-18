"""W600 — ``stamp_all`` / ``compute_config_hash`` plumb ``warnings_out``.

W448 added ``warnings_out`` to ``read_lease``. W589/W592 closed the lease
sibling cluster. W593/W595 closed the permits cluster. W596 closed the
runs-ledger cluster (``read_run_meta`` + ``read_run_events``). W597
closed the runtime-daemon cluster (``daemon_state`` + ``daemon_running``).
W598 closed the pr-analyze-cache reader (``_load_cache``). W599 closed
the trace-ingest readers (``ingest_generic``/``otel``/``jaeger``/``zipkin``
+ ``auto_detect_format``). W600 closes the W210 ChangeEvidence
config-hash substrate (``compute_config_hash`` + ``stamp_all``) which
previously returned the empty string indistinguishably on:

* file does not exist (the COMMON, expected case — the three canonical
  paths are user-owned EXPECTATIONS), AND
* file exists but ``Path.read_bytes()`` raises (operational anomaly —
  permission denied, deleted mid-read, I/O error, etc.).

Marker shape mirrors W596's ``run_meta_*`` closed-enum vocabulary with
a ``config_hash_<scope>_`` prefix. Scope short-names are derived from
the W210 field name by stripping ``_hash`` (``rules_config`` /
``constitution`` / ``control_map``) so the marker vocabulary aligns
with the existing W210 vocabulary an operator already sees on
``RunMeta.extra``/``ChangeEvidence``.

Closed-enum kinds (per scope, exactly 2 — W978 first-hypothesis
discipline: ``compute_config_hash`` is a RAW BYTE HASH with no parse
step, so the W596/W598 third ``_corrupt:<exc_class>`` marker is N/A):

  * ``config_hash_rules_config_not_found:<rel_path>``
  * ``config_hash_rules_config_read_failed:<rel_path>:<exc_class>:<detail>``
  * ``config_hash_constitution_not_found:<rel_path>``
  * ``config_hash_constitution_read_failed:<rel_path>:<exc_class>:<detail>``
  * ``config_hash_control_map_not_found:<rel_path>``
  * ``config_hash_control_map_read_failed:<rel_path>:<exc_class>:<detail>``

Intentional-absence decision (W978 + "Make fallback chains loud"):
missing config file is the EXPECTED case (the three canonical paths
are EXPECTATIONS — users own the files). The marker is emitted so a
caller threading ``warnings_out`` through every substrate read site
sees uniform lineage disclosure, but the marker is INFORMATIONAL —
``config_hash_*_not_found`` does not signal a broken state. This
mirrors W596's ``run_meta_not_found`` (informational missing-state
marker) rather than W597's missing-pidfile (no marker at all) — the
W210 config-hash substrate is a STAMPED record and "no config" is
worth disclosing on every audit pass, just not alarming.

The empty-string return is PRESERVED — every silent return path
remains byte-identical to pre-W600 behaviour. ``warnings_out=None``
(default) preserves silent behaviour for the two live callsites
(``runs/ledger.start_run`` at ledger.py:266 and
``config_hashes_producer.current_hashes_or_none`` at
config_hashes_producer.py:115).

LAW 4 note: warning kinds are NOT ``agent_contract.facts`` strings and
therefore not subject to the concrete-noun-terminal lint. They are
internal diagnostic markers (same discipline as W589/W592/W593/W595/
W596/W597/W598/W599).
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _helpers.repo_root import repo_root  # noqa: E402

from roam.evidence.config_hashes import (  # noqa: E402
    CANONICAL_PATHS,
    SCOPE_NAMES,
    compute_config_hash,
    stamp_all,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def all_configs_project(tmp_path: Path) -> Path:
    """A repo root with all three canonical config files present."""
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam-rules.yml").write_bytes(b"rules: []\n")
    (tmp_path / ".roam" / "constitution.yml").write_bytes(b"laws: []\n")
    (tmp_path / ".roam" / "control-map.yml").write_bytes(b"controls: []\n")
    return tmp_path


# ===========================================================================
# (1) Happy path — clean three-config read emits no warning
# ===========================================================================


def test_clean_happy_path_no_warnings(all_configs_project: Path) -> None:
    """All three configs present + readable → no warnings, hashes populated.

    Sanity check that the W600 plumbing only fires on degenerate paths.
    The dict values must remain populated with real sha256 hex digests.
    """
    warnings: list[str] = []
    got = stamp_all(all_configs_project, warnings_out=warnings)

    assert warnings == [], f"clean stamp_all on a fully-populated repo must NOT emit warnings; got {warnings!r}"
    # All three fields must carry real 64-char sha256 hex digests.
    for field, value in got.items():
        assert len(value) == 64, f"{field} hash must be 64 hex chars; got {value!r}"
        assert all(c in "0123456789abcdef" for c in value), f"{field} hash must be lowercase hex; got {value!r}"


# ===========================================================================
# (2) Per-scope missing-file emits informational marker
# ===========================================================================


def test_rules_missing_emits_informational_marker(tmp_path: Path) -> None:
    """Missing ``.roam-rules.yml`` emits ``config_hash_rules_config_not_found:``.

    The other two configs are present so we can confirm the marker is
    scope-specific (NOT every scope fires when only one is absent).
    """
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam" / "constitution.yml").write_bytes(b"laws\n")
    (tmp_path / ".roam" / "control-map.yml").write_bytes(b"controls\n")

    warnings: list[str] = []
    got = stamp_all(tmp_path, warnings_out=warnings)

    # Hash contract preserved — missing file → empty string.
    assert got["rules_config_hash"] == ""
    assert len(got["constitution_hash"]) == 64
    assert len(got["control_map_hash"]) == 64

    # Exactly one marker, for the rules_config scope only.
    assert len(warnings) == 1, f"expected one not_found marker; got {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("config_hash_rules_config_not_found:"), msg
    assert ".roam-rules.yml" in msg, msg


def test_constitution_missing_emits_informational_marker(tmp_path: Path) -> None:
    """Missing ``.roam/constitution.yml`` emits
    ``config_hash_constitution_not_found:``."""
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam-rules.yml").write_bytes(b"rules\n")
    (tmp_path / ".roam" / "control-map.yml").write_bytes(b"controls\n")

    warnings: list[str] = []
    got = stamp_all(tmp_path, warnings_out=warnings)

    assert got["constitution_hash"] == ""
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("config_hash_constitution_not_found:"), msg
    assert ".roam/constitution.yml" in msg, msg


def test_control_map_missing_emits_informational_marker(tmp_path: Path) -> None:
    """Missing ``.roam/control-map.yml`` emits
    ``config_hash_control_map_not_found:``."""
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam-rules.yml").write_bytes(b"rules\n")
    (tmp_path / ".roam" / "constitution.yml").write_bytes(b"laws\n")

    warnings: list[str] = []
    got = stamp_all(tmp_path, warnings_out=warnings)

    assert got["control_map_hash"] == ""
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("config_hash_control_map_not_found:"), msg
    assert ".roam/control-map.yml" in msg, msg


def test_all_three_missing_emits_three_markers(tmp_path: Path) -> None:
    """No configs on disk → three not_found markers, parallel scope tokens.

    Confirms ``stamp_all`` walks every scope and emits one marker per
    missing file, even when ALL three are absent (the most common
    cold-state case — the canonical paths are EXPECTATIONS).
    """
    warnings: list[str] = []
    got = stamp_all(tmp_path, warnings_out=warnings)

    assert all(v == "" for v in got.values())
    assert len(warnings) == 3, f"expected three not_found markers; got {warnings!r}"
    # Marker scope tokens cover all three W210 scopes.
    prefixes = [w.split(":", 1)[0] for w in warnings]
    assert "config_hash_rules_config_not_found" in prefixes
    assert "config_hash_constitution_not_found" in prefixes
    assert "config_hash_control_map_not_found" in prefixes


# ===========================================================================
# (3) Per-scope read failure emits operational-anomaly marker
# ===========================================================================


def _patch_read_bytes_to_raise(
    monkeypatch: pytest.MonkeyPatch,
    target_path: Path,
    exc: BaseException,
) -> None:
    """Monkeypatch ``Path.read_bytes`` to raise ``exc`` on a specific path."""
    target_resolved = target_path.resolve()
    original_read_bytes = Path.read_bytes

    def _raising_read_bytes(self):
        try:
            resolved = self.resolve()
        except OSError:
            resolved = self
        if resolved == target_resolved:
            raise exc
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _raising_read_bytes)


def test_rules_unreadable_emits_read_failed_marker(all_configs_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``PermissionError`` on ``.roam-rules.yml`` emits ``config_hash_rules_config_read_failed:``.

    The file exists on disk (so we get past the ``not exists()``
    short-circuit) but ``read_bytes()`` raises. Caller contract is
    preserved — the field value stays ``""``.
    """
    rules_file = all_configs_project / ".roam-rules.yml"
    _patch_read_bytes_to_raise(
        monkeypatch,
        rules_file,
        PermissionError("synthetic-EACCES from W600 test"),
    )

    warnings: list[str] = []
    got = stamp_all(all_configs_project, warnings_out=warnings)

    # Caller contract preserved — read failure still empty-strings the field.
    assert got["rules_config_hash"] == ""
    # The other two reads still succeed cleanly.
    assert len(got["constitution_hash"]) == 64
    assert len(got["control_map_hash"]) == 64

    assert len(warnings) == 1, f"expected one read_failed marker; got {warnings!r}"
    msg = warnings[0]
    assert msg.startswith("config_hash_rules_config_read_failed:"), msg
    assert ".roam-rules.yml" in msg, msg
    assert "PermissionError" in msg, msg
    assert "synthetic-EACCES from W600 test" in msg, msg


def test_constitution_unreadable_emits_read_failed_marker(
    all_configs_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``PermissionError`` on ``.roam/constitution.yml`` emits
    ``config_hash_constitution_read_failed:``."""
    target = all_configs_project / ".roam" / "constitution.yml"
    _patch_read_bytes_to_raise(
        monkeypatch,
        target,
        PermissionError("synthetic-EACCES on constitution"),
    )

    warnings: list[str] = []
    got = stamp_all(all_configs_project, warnings_out=warnings)

    assert got["constitution_hash"] == ""
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("config_hash_constitution_read_failed:"), msg
    assert "PermissionError" in msg, msg


def test_control_map_unreadable_emits_read_failed_marker(
    all_configs_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``PermissionError`` on ``.roam/control-map.yml`` emits
    ``config_hash_control_map_read_failed:``."""
    target = all_configs_project / ".roam" / "control-map.yml"
    _patch_read_bytes_to_raise(
        monkeypatch,
        target,
        PermissionError("synthetic-EACCES on control-map"),
    )

    warnings: list[str] = []
    got = stamp_all(all_configs_project, warnings_out=warnings)

    assert got["control_map_hash"] == ""
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("config_hash_control_map_read_failed:"), msg
    assert "PermissionError" in msg, msg


def test_oserror_emits_read_failed_marker(all_configs_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A generic ``OSError`` (no errno) also emits read_failed.

    Confirms the ``except OSError`` clause catches the broader family —
    not only ``PermissionError``. Disk-full / I/O error / etc. all
    funnel through the same closed-enum marker. Note: ``OSError(13, ...)``
    is auto-promoted to ``PermissionError`` by CPython's errno→subclass
    mapping, so we use a plain-string OSError to keep the type name
    stable in the assertion.
    """
    rules_file = all_configs_project / ".roam-rules.yml"
    _patch_read_bytes_to_raise(
        monkeypatch,
        rules_file,
        OSError("synthetic generic IO error"),
    )

    warnings: list[str] = []
    got = stamp_all(all_configs_project, warnings_out=warnings)

    assert got["rules_config_hash"] == ""
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("config_hash_rules_config_read_failed:"), msg
    assert "OSError" in msg, msg


# ===========================================================================
# (4) Default ``warnings_out=None`` preserves silent behaviour
# ===========================================================================


def test_default_none_no_crash(tmp_path: Path) -> None:
    """Calling without ``warnings_out`` still works on every degenerate path.

    Existing callers (``runs/ledger.start_run`` at ledger.py:266 +
    ``config_hashes_producer.current_hashes_or_none`` at
    config_hashes_producer.py:115) call ``stamp_all(root)`` with no
    kwargs — they must NOT regress on any failure mode covered by the
    W600 plumb.
    """
    # (a) All-missing tmp_path — the most common silent-empty path.
    got = stamp_all(tmp_path)
    assert all(v == "" for v in got.values())

    # (b) compute_config_hash on a missing file with no kwargs.
    h = compute_config_hash(tmp_path, ".roam-rules.yml")
    assert h == ""

    # (c) Happy path with no warnings_out — hashes populated.
    (tmp_path / ".roam").mkdir()
    payload = b"rules: []\n"
    (tmp_path / ".roam-rules.yml").write_bytes(payload)
    h2 = compute_config_hash(tmp_path, ".roam-rules.yml")
    assert h2 == hashlib.sha256(payload).hexdigest()


# ===========================================================================
# (5) Caller contract preserved — stamp_all returns the empty string on
#     missing files regardless of warnings_out state
# ===========================================================================


def test_stamp_all_returns_empty_string_on_missing(tmp_path: Path) -> None:
    """Empty-string return on missing files is the W1234 invariant.

    The W600 marker disclosure does NOT change the return value
    semantic — empty string still means "absent / cannot read" so
    downstream verifiers' "hash absent vs hash mismatch" check
    continues to work.
    """
    # Without warnings_out.
    got_silent = stamp_all(tmp_path)
    assert all(v == "" for v in got_silent.values())

    # With warnings_out (3 markers emitted) — return value MUST be
    # byte-identical to the silent call.
    warnings: list[str] = []
    got_loud = stamp_all(tmp_path, warnings_out=warnings)
    assert got_loud == got_silent
    assert len(warnings) == 3


# ===========================================================================
# (6) AST audit — callers of stamp_all remain unmodified
# ===========================================================================


def test_caller_unmodified() -> None:
    """AST-check the two live callers of ``stamp_all``.

    W600 is additive — a kw-only ``warnings_out`` parameter with
    default ``None``. The two live callers (`runs/ledger.start_run`
    at ledger.py:266 and ``config_hashes_producer.current_hashes_or_none``
    at config_hashes_producer.py:115) call ``stamp_all(root)`` with no
    ``warnings_out`` kwarg and must remain unchanged by W600.

    A future refactor can opt either caller into threading the bucket;
    this test pins the current "audit-only, caller unmodified"
    contract — flipping it means an intentional handoff between waves.
    """
    # Track call sites across both consumers.
    consumers = [
        repo_root() / "src" / "roam" / "runs" / "ledger.py",
        repo_root() / "src" / "roam" / "evidence" / "config_hashes_producer.py",
    ]
    total_calls = 0
    for src_path in consumers:
        assert src_path.exists(), f"expected to find {src_path}"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name == "stamp_all":
                    total_calls += 1
                    kwarg_names = [kw.arg for kw in node.keywords if kw.arg is not None]
                    assert "warnings_out" not in kwarg_names, (
                        f"caller in {src_path.name} at line {node.lineno} now threads "
                        f"warnings_out; W600 was audit-only — update this test if "
                        f"intentionally opted in."
                    )
    assert total_calls >= 2, (
        f"expected at least 2 stamp_all callsites across consumers; "
        f"found {total_calls}. If a consumer was removed, update this test."
    )


# ===========================================================================
# (7) Three-scope marker symmetry — AST verification
# ===========================================================================


def test_three_scope_marker_symmetry() -> None:
    """SCOPE_NAMES maps every CANONICAL_PATHS field to a parallel scope token.

    Every W210 field must have a 1:1 mapping in SCOPE_NAMES so the
    closed-enum marker vocabulary stays symmetric across all three
    scopes. The marker structure is:

        config_hash_<scope_short_name>_<kind>:<rel_path>[:detail]

    Asymmetry between SCOPE_NAMES keys and CANONICAL_PATHS keys would
    silently drop a scope from the warnings_out channel.
    """
    assert set(SCOPE_NAMES.keys()) == set(CANONICAL_PATHS.keys()), (
        f"SCOPE_NAMES keys must match CANONICAL_PATHS keys exactly; "
        f"got SCOPE_NAMES={set(SCOPE_NAMES.keys())!r}, "
        f"CANONICAL_PATHS={set(CANONICAL_PATHS.keys())!r}"
    )
    # Scope short-names derived from the W210 field by stripping ``_hash``.
    for field, scope in SCOPE_NAMES.items():
        assert field.endswith("_hash"), f"W210 field name {field!r} must end with '_hash'"
        expected = field[: -len("_hash")]
        assert scope == expected, (
            f"SCOPE_NAMES[{field!r}] must derive from the field name; expected {expected!r}, got {scope!r}"
        )


def test_three_scope_symmetric_emission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """All three scopes emit parallel markers under the same failure mode.

    Drives every scope through the read_failed code path to confirm
    the marker prefixes are PARALLEL (one per scope, same shape, same
    detail format). Asymmetric marker emission would let one scope
    silently swallow failures while the others surfaced them.
    """
    # Populate all three files.
    (tmp_path / ".roam").mkdir()
    (tmp_path / ".roam-rules.yml").write_bytes(b"x")
    (tmp_path / ".roam" / "constitution.yml").write_bytes(b"x")
    (tmp_path / ".roam" / "control-map.yml").write_bytes(b"x")

    # Make ALL three reads raise.
    rules_resolved = (tmp_path / ".roam-rules.yml").resolve()
    constitution_resolved = (tmp_path / ".roam" / "constitution.yml").resolve()
    control_map_resolved = (tmp_path / ".roam" / "control-map.yml").resolve()
    failure_targets = {rules_resolved, constitution_resolved, control_map_resolved}

    original_read_bytes = Path.read_bytes

    def _raising_read_bytes(self):
        try:
            resolved = self.resolve()
        except OSError:
            resolved = self
        if resolved in failure_targets:
            raise PermissionError(f"synthetic EACCES on {resolved.name}")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _raising_read_bytes)

    warnings: list[str] = []
    got = stamp_all(tmp_path, warnings_out=warnings)

    # Every field empty-stringed.
    assert all(v == "" for v in got.values())
    # Exactly three parallel markers.
    assert len(warnings) == 3, warnings
    prefixes = sorted(w.split(":", 1)[0] for w in warnings)
    assert prefixes == sorted(
        [
            "config_hash_rules_config_read_failed",
            "config_hash_constitution_read_failed",
            "config_hash_control_map_read_failed",
        ]
    )
    # Detail shape symmetric: ``<exc_class>:<detail>`` for all three.
    for msg in warnings:
        assert "PermissionError" in msg, msg
        assert "synthetic EACCES" in msg, msg


# ===========================================================================
# (8) W978 positive coverage — no parse step → no corrupt marker
# ===========================================================================


def test_w978_no_parse_step() -> None:
    """``compute_config_hash`` is RAW BYTE HASH — no parse step exists.

    W978 first-hypothesis discipline check: forcing a 3rd
    ``_corrupt:<exc_class>`` marker on a function that never parses
    its input would add dead vocabulary that no real failure path can
    ever emit. This test pins the design decision by:

    1. AST-checking ``compute_config_hash`` has no JSON/YAML parse call.
    2. AST-checking ``compute_config_hash`` source contains NO emission
       of any ``_corrupt:`` marker.

    If a future refactor adds a parse step (e.g., YAML schema validation
    before hashing), this test must be updated AND the closed-enum
    vocabulary must grow a 3rd marker per scope.
    """
    src_path = repo_root() / "src" / "roam" / "evidence" / "config_hashes.py"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    found_compute = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "compute_config_hash":
            found_compute = True
            func_source = ast.get_source_segment(source, node) or ""
            # Sanity: no parse-step call within the function body.
            for forbidden in ("json.loads", "json.load", "yaml.safe_load", "yaml.load"):
                assert forbidden not in func_source, (
                    f"compute_config_hash now uses {forbidden!r} — a parse step "
                    f"exists. Update the W600 closed-enum vocabulary to include "
                    f"a third ``_corrupt:`` marker per scope and update this test."
                )
            # No ``_corrupt:`` emission anywhere in the function body.
            assert "_corrupt:" not in func_source, (
                "compute_config_hash emits a _corrupt: marker — but no parse "
                "step exists. W978 first-hypothesis discipline: the marker "
                "must correspond to a real failure path. Remove the marker "
                "or add the parse step."
            )

    assert found_compute, "expected compute_config_hash function in config_hashes.py"


# ===========================================================================
# (9) Function-signature audit — kw-only warnings_out on both functions
# ===========================================================================


def test_signatures_carry_kw_only_warnings_out() -> None:
    """AST-check both functions declare ``warnings_out`` as kw-only.

    Mirrors W598's ``test_load_cache_signature_carries_kw_only_warnings_out``
    shape. Kw-only declaration is the back-compat-preserving signal
    that existing positional callers (zero) are unaffected.
    """
    src_path = repo_root() / "src" / "roam" / "evidence" / "config_hashes.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    targets = {"compute_config_hash", "stamp_all"}
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
                        f"{node.name} contains a yield — W600 must not turn the hash readers into generators"
                    )

    missing = targets - found
    assert not missing, f"expected to find functions {missing!r} in config_hashes.py"


# ===========================================================================
# (10) compute_config_hash direct call — scope namespace contract
# ===========================================================================


def test_compute_config_hash_scope_kwarg(tmp_path: Path) -> None:
    """``compute_config_hash`` accepts ``scope`` to namespace the marker.

    Confirms an ad-hoc caller of ``compute_config_hash`` (not via
    ``stamp_all``) can still get a usable marker by either:
      * passing ``scope`` explicitly — marker uses the scope token, OR
      * omitting ``scope`` — marker falls back to the relative path.
    """
    warnings_with_scope: list[str] = []
    h1 = compute_config_hash(
        tmp_path,
        ".roam-rules.yml",
        scope="rules_config",
        warnings_out=warnings_with_scope,
    )
    assert h1 == ""
    assert len(warnings_with_scope) == 1
    assert warnings_with_scope[0].startswith("config_hash_rules_config_not_found:"), warnings_with_scope[0]

    warnings_no_scope: list[str] = []
    h2 = compute_config_hash(
        tmp_path,
        ".roam-rules.yml",
        warnings_out=warnings_no_scope,
    )
    assert h2 == ""
    assert len(warnings_no_scope) == 1
    # No scope override → rel_path is used as the marker namespace.
    assert ".roam-rules.yml" in warnings_no_scope[0]
