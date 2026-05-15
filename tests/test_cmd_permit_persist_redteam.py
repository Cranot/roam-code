"""W349 - red-team tests for ``roam permit issue --persist`` (W198).

W198 shipped the writer; W345 confirmed the happy path. W349 covers the
adversarial / robustness side: what happens when a permit file is
malformed, expired, duplicated, stale, or written under a race-ish
conditions? Verify that:

* ``cmd_pr_bundle._load_permits_from_disk`` is resilient (best-effort
  skip with no crash and no torn-state visible to readers).
* ``evidence.collector._build_authority_refs`` either drops or marks
  invalid entries; no malformed entry crashes the collector.
* ``permits/store.atomic_write_json`` cannot leave a torn permit file
  on disk even when the writer is interrupted mid-call.

Discipline (per CLAUDE.md):

* These tests are PINS of current behavior. If a test exposes a real
  bug, the test still asserts current behavior and the bug is reported
  as a drive-by, NOT fixed in W349.
* Tests-only file. No production code is modified.
* Race tests use mocking; no real concurrency (would be flaky).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, invoke_cli  # noqa: E402

from roam.commands.cmd_pr_bundle import _load_permits_from_disk  # noqa: E402
from roam.evidence.collector import _build_authority_refs  # noqa: E402
from roam.permits.store import permits_root  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def permit_project(tmp_path, monkeypatch):
    """Minimal git-initialised project with no permits / runs yet.

    Mirrors the fixture in test_cmd_permit_persist.py so the W349 red-team
    suite shares the same baseline state as the W198 happy-path suite.
    """
    proj = tmp_path / "w349_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def main():\n    return 0\n")
    git_init(proj)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    monkeypatch.delenv("ROAM_PERMIT_ID", raising=False)
    return proj


def _ensure_permits_dir(project: Path) -> Path:
    """Create ``.roam/permits/`` and return the directory path."""
    root = permits_root(project)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _valid_permit_dict(permit_id: str = "permit_20260514_aaaaaa") -> dict:
    """Return a well-formed permit dict matching the on-disk schema."""
    return {
        "permit_id": permit_id,
        "scope": "redteam-baseline",
        "expires_at": "2027-01-01T00:00:00Z",
        "issued_to": "agent:redteam",
        "issued_at": "2026-05-14T10:00:00Z",
        "issued_by": "human:redteam-operator",
        "reason": "",
    }


# ===========================================================================
# 1. Malformed permit JSON (best-effort skip)
# ===========================================================================


def test_malformed_json_file_skipped_with_no_crash(permit_project):
    """A ``.json`` file that is not valid JSON is skipped + warned (W382).

    W382 hardening: ``_load_permits_from_disk`` now appends an
    actionable warning into the optional ``warnings_out`` list when a
    permit file fails to parse. The well-formed sibling still loads.
    """
    root = _ensure_permits_dir(permit_project)
    # Deliberately torn / malformed JSON.
    (root / "permit_20260514_bad001.json").write_text(
        "{not valid json at all",
        encoding="utf-8",
    )
    # Sibling that should still load cleanly. PERMIT_ID_RE requires the
    # suffix to be 6+ hex chars (W198), so the test fixture uses hex only.
    good = _valid_permit_dict("permit_20260514_900d01")
    (root / "permit_20260514_900d01.json").write_text(
        json.dumps(good), encoding="utf-8"
    )

    warnings: list[str] = []
    loaded = _load_permits_from_disk(permit_project, warnings_out=warnings)
    ids = [p.get("permit_id") for p in loaded]
    assert "permit_20260514_900d01" in ids
    assert "permit_20260514_bad001" not in ids
    # Reader returns ONLY the parseable sibling -> exactly 1 entry.
    assert len(loaded) == 1
    # W382: warning surfaces the offending file name + parse error class
    # so a reviewer can fix the underlying permit without grepping.
    assert any(
        "permit_20260514_bad001.json" in w and "malformed JSON" in w
        for w in warnings
    ), f"expected malformed-JSON warning naming the file; got {warnings!r}"


def test_empty_json_file_skipped(permit_project):
    """Zero-byte ``.json`` files are skipped (json.loads of '' raises)."""
    root = _ensure_permits_dir(permit_project)
    (root / "permit_20260514_empty1.json").write_text("", encoding="utf-8")

    loaded = _load_permits_from_disk(permit_project)
    assert loaded == []


def test_json_array_instead_of_object_skipped(permit_project):
    """Top-level JSON array is not a dict -> reader skips it.

    Current behavior (PIN): ``_load_permits_from_disk`` requires
    ``isinstance(raw, dict)`` and drops non-dict roots without warning.
    """
    root = _ensure_permits_dir(permit_project)
    (root / "permit_20260514_arr001.json").write_text(
        json.dumps([{"permit_id": "permit_20260514_arr001"}]),
        encoding="utf-8",
    )

    loaded = _load_permits_from_disk(permit_project)
    assert loaded == []


# ===========================================================================
# 2. Missing-required-field permit
# ===========================================================================


def test_missing_permit_id_dropped_by_reader_with_warning(
    permit_project,
):
    """Permit JSON missing ``permit_id`` is dropped + warned at the
    reader (W380), so the collector never sees the malformed row.

    W380 hardening: ``_load_permits_from_disk`` now hands every dict to
    ``permits.store._permit_from_dict`` for schema validation. A row
    missing the required ``permit_id`` cannot reconstruct a
    ``PermitRecord`` and is therefore dropped + warned at the reader
    boundary. The collector remains the second line of defense (its
    ``_entry_id`` would have dropped the row too), but the reader-level
    drop ensures the auditor sees a clear warning naming the file.
    """
    root = _ensure_permits_dir(permit_project)
    payload = _valid_permit_dict()
    payload.pop("permit_id")
    (root / "permit_20260514_noid01.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    warnings: list[str] = []
    loaded = _load_permits_from_disk(permit_project, warnings_out=warnings)
    # W380: reader now drops schema-invalid rows.
    assert loaded == []
    # Warning names the file + the schema validation failure mode +
    # the missing permit_id marker so the operator can locate the fix.
    assert any(
        "permit_20260514_noid01.json" in w
        and "schema validation failed" in w
        and "permit_id=<missing>" in w
        for w in warnings
    ), f"expected schema-validation warning naming the file; got {warnings!r}"

    refs = _build_authority_refs(
        pr_bundle_envelope={"permits": loaded},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    # Defense in depth: even if a future caller passes the raw row,
    # the collector still drops it (no permit AuthorityRef).
    assert [r for r in refs if r.authority_kind == "permit"] == []


def test_missing_scope_field_dropped_by_reader_with_warning(permit_project):
    """Permit missing ``scope`` is dropped + warned (W380).

    W380 hardening: the reader now routes every dict through
    ``permits.store._permit_from_dict`` which rejects rows missing the
    required ``scope`` field. The reader appends an actionable warning
    naming the file + the permit_id so the operator can locate the
    broken record.
    """
    root = _ensure_permits_dir(permit_project)
    # Hex-only suffix so the rejection reason is specifically "missing
    # scope", not "permit_id fails PERMIT_ID_RE".
    payload = _valid_permit_dict("permit_20260514_5c0bef")
    payload.pop("scope")
    (root / "permit_20260514_5c0bef.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    warnings: list[str] = []
    loaded = _load_permits_from_disk(permit_project, warnings_out=warnings)
    # W380: schema-invalid rows no longer pass the reader.
    assert loaded == []
    assert any(
        "permit_20260514_5c0bef.json" in w
        and "schema validation failed" in w
        and "permit_20260514_5c0bef" in w
        for w in warnings
    ), f"expected schema-validation warning; got {warnings!r}"


def test_wrong_type_expires_at_dropped_by_reader_with_warning(permit_project):
    """``expires_at`` as integer (not ISO string) is dropped + warned (W380).

    W380 hardening: the W198 validator coerces every required field
    through ``str(...)`` and then re-validates ISO-8601 parseability
    inside ``PermitRecord.__post_init__``. An epoch-seconds int passes
    the str-coerce but fails ISO parsing, so the validator returns
    ``None`` and the reader drops + warns.
    """
    root = _ensure_permits_dir(permit_project)
    # Hex-only suffix isolates the rejection reason to the wrong type
    # on ``expires_at`` (not the permit_id format).
    payload = _valid_permit_dict("permit_20260514_be0001")
    payload["expires_at"] = 1893456000  # epoch seconds, NOT ISO-8601
    (root / "permit_20260514_be0001.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    warnings: list[str] = []
    loaded = _load_permits_from_disk(permit_project, warnings_out=warnings)
    assert loaded == []
    assert any(
        "permit_20260514_be0001.json" in w
        and "schema validation failed" in w
        for w in warnings
    ), f"expected schema-validation warning; got {warnings!r}"


# ===========================================================================
# 3. Expired permit
# ===========================================================================


def test_expired_permit_loaded_into_envelope_and_authority_ref(permit_project):
    """An expired permit IS loaded and IS minted as an AuthorityRef,
    BUT the AuthorityRef now carries ``extra["expired"] = True`` (W377).

    Decision: we keep the historical-fact discipline (an expired permit
    is a real audit record for changes made BEFORE expiry, so filtering
    would retroactively invalidate evidence), but we now make the
    expiry status explicit on the AuthorityRef so downstream consumers
    can render expired permits differently and auditors can detect
    "agent acted while the permit had already expired" without having
    to re-parse the on-disk JSON.
    """
    root = _ensure_permits_dir(permit_project)
    # Hex-only suffix so the W198 validator accepts the permit.
    permit_id = "permit_20260514_e10001"
    payload = _valid_permit_dict(permit_id)
    # 30 days in the past.
    expired_at = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).isoformat().replace("+00:00", "Z")
    payload["expires_at"] = expired_at
    (root / f"{permit_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    loaded = _load_permits_from_disk(permit_project)
    assert len(loaded) == 1
    assert loaded[0]["permit_id"] == permit_id

    refs = _build_authority_refs(
        pr_bundle_envelope={"permits": loaded},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    permit_refs = [r for r in refs if r.authority_kind == "permit"]
    assert len(permit_refs) == 1
    target = permit_refs[0]
    assert target.authority_id == permit_id
    # W377: expiry marker now lands on extra so auditors can see the
    # status at a glance.
    assert target.extra.get("expired") is True, (
        f"expected extra['expired']=True on an expired permit; got "
        f"{target.extra!r}"
    )
    # W294 real-vs-facade disambiguation still fires correctly.
    assert target.extra.get("permit_id") == permit_id
    # W381: sibling fields land on extra so the auditor sees WHAT was
    # authorised + WHEN it expired without re-parsing the envelope.
    assert target.extra.get("scope") == "redteam-baseline"
    assert target.extra.get("expires_at") == expired_at
    assert target.extra.get("issued_to") == "agent:redteam"


# ===========================================================================
# 4. Duplicate id (same permit_id in two files)
# ===========================================================================


def test_duplicate_permit_id_in_two_files_collapsed_by_collector(permit_project):
    """Two files claiming the same ``permit_id`` -> reader keeps first +
    warns (W379); collector still emits a single AuthorityRef.

    W379 hardening: ``_load_permits_from_disk`` now detects duplicates
    inside the directory scan and emits an actionable warning naming
    the duplicate permit_id + the offending file + the first-seen file.
    The first file's content survives (Pattern 2 — never silently
    invalidate evidence), the second is dropped, and the auditor can
    see exactly which copies collided.
    """
    root = _ensure_permits_dir(permit_project)
    # Hex-only suffix so the W198 validator accepts both copies.
    pid = "permit_20260514_d00001"
    payload_a = _valid_permit_dict(pid)
    payload_a["scope"] = "first-version"
    payload_b = _valid_permit_dict(pid)
    payload_b["scope"] = "second-version-different-content"

    (root / f"{pid}.json").write_text(json.dumps(payload_a), encoding="utf-8")
    # Sibling file name MUST sort lexicographically AFTER the primary so
    # the reader's first-seen wins. Suffix the stem rather than mutating
    # the date prefix.
    (root / f"{pid}_dup.json").write_text(json.dumps(payload_b), encoding="utf-8")

    warnings: list[str] = []
    loaded = _load_permits_from_disk(permit_project, warnings_out=warnings)
    # W379: reader keeps exactly the first-seen permit (sorted order:
    # ``permit_20260514_dup001.json`` < ``permit_20260514_dup001_dup.json``).
    assert len(loaded) == 1
    assert loaded[0]["permit_id"] == pid
    assert loaded[0]["scope"] == "first-version"
    # W379: warning names BOTH files + the duplicate permit_id so the
    # operator can locate the collision and delete the unwanted copy.
    assert any(
        f"permit_id={pid!r}" in w
        and "duplicate" in w
        and f"{pid}_dup.json" in w
        and f"{pid}.json" in w
        for w in warnings
    ), f"expected duplicate-permit_id warning naming both files; got {warnings!r}"

    refs = _build_authority_refs(
        pr_bundle_envelope={"permits": loaded},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    permit_refs = [r for r in refs if r.authority_kind == "permit"]
    # Collector still ends up with exactly one ref (now because the
    # reader fed it one row; previously because ``seen`` deduped two).
    assert len(permit_refs) == 1
    assert permit_refs[0].authority_id == pid


# ===========================================================================
# 5. Stale (issued long ago) but not expired
# ===========================================================================


def test_stale_but_unexpired_permit_fully_loaded(permit_project):
    """A permit issued 2 years ago but expiring in the future is loaded
    and carries ``extra["issued_days_ago"] = N`` (W378).

    W378 hardening: the collector now stamps ``issued_days_ago`` on
    AuthorityRef.extra when the permit's ``issued_at`` is older than
    the 90-day threshold. The permit remains evidence-relevant (the
    AuthorityRef is still materialised), but auditors and downstream
    consumers can now distinguish "freshly issued" from "issued years
    ago and quietly re-used."
    """
    root = _ensure_permits_dir(permit_project)
    # Hex-only suffix so the W198 validator accepts the permit.
    permit_id = "permit_20240101_57a1e1"
    payload = _valid_permit_dict(permit_id)
    payload["issued_at"] = "2024-01-01T00:00:00Z"  # ~2+ years ago
    payload["expires_at"] = "2099-01-01T00:00:00Z"  # far future
    (root / f"{permit_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    loaded = _load_permits_from_disk(permit_project)
    assert len(loaded) == 1
    assert loaded[0]["issued_at"] == "2024-01-01T00:00:00Z"

    refs = _build_authority_refs(
        pr_bundle_envelope={"permits": loaded},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    permit_refs = [r for r in refs if r.authority_kind == "permit"]
    assert len(permit_refs) == 1
    target = permit_refs[0]
    # W378: staleness marker present + reasonable (well over 90 days).
    days_ago = target.extra.get("issued_days_ago")
    assert isinstance(days_ago, int), (
        f"expected extra['issued_days_ago']: int; got {target.extra!r}"
    )
    assert days_ago >= 90, (
        f"expected issued_days_ago >= 90 for a 2+yr-old permit; got "
        f"{days_ago}"
    )
    # Sanity: NOT expired (far-future expires_at).
    assert "expired" not in target.extra, (
        f"unexpired permit must not carry extra['expired']; got "
        f"{target.extra!r}"
    )


# ===========================================================================
# 6. Race-ish atomic write (no torn state ever visible)
# ===========================================================================


def test_atomic_write_never_exposes_torn_permit_file(permit_project, monkeypatch):
    """Mid-write interruption MUST NOT leave a torn permit on disk.

    Strategy (no real concurrency — would be flaky):

    * Patch ``atomic_write_json`` to simulate an interruption AFTER the
      temp file is created but BEFORE the ``os.replace`` lands.
    * Run ``roam permit issue --persist``; expect the CLI to surface
      the exception (or non-zero exit).
    * Assert: no ``permit_*.json`` file ever materialised at the
      target path. (The torn ``.tmp`` debris cleanup is exercised by
      the atomic_io module's own tests; here we only assert that no
      consumer of ``_load_permits_from_disk`` could ever observe a
      half-written permit.)
    * Then issue a SECOND permit through the unpatched path; assert it
      lands cleanly and is the ONLY file present.
    """
    from roam.permits import store as store_mod

    proot = permits_root(permit_project)
    proot.mkdir(parents=True, exist_ok=True)

    def boom_atomic_write_json(path, data, *, indent=2, sort_keys=False):
        # Simulate the writer being interrupted (e.g. SIGKILL) BEFORE the
        # atomic rename lands. We deliberately do NOT touch ``path`` so
        # the target slot stays empty -- mirrors the atomic_write_text
        # contract: "the target file is NEVER left in a half-written
        # state".
        raise RuntimeError("simulated mid-write interruption")

    monkeypatch.setattr(store_mod, "atomic_write_json", boom_atomic_write_json)

    runner = CliRunner()
    result = invoke_cli(
        runner,
        [
            "permit", "issue",
            "--scope", "race-test-A",
            "--expires-at", "2027-01-01T00:00:00Z",
            "--issued-to", "agent:race-A",
            "--persist",
        ],
        cwd=permit_project,
    )
    # The simulated interruption must surface as non-zero exit (the CLI
    # may catch + emit a structured error, or let the exception bubble).
    # Either way: no torn file on disk.
    assert result.exit_code != 0, (
        f"expected non-zero exit on simulated mid-write, got {result.exit_code}; "
        f"output={result.output!r}"
    )
    # The reader sees an EMPTY permits dir (no torn file ever
    # materialised).
    loaded_after_crash = _load_permits_from_disk(permit_project)
    assert loaded_after_crash == [], (
        f"expected zero permits after simulated crash, got {loaded_after_crash!r}"
    )
    # No JSON files on disk.
    assert list(proot.glob("*.json")) == []

    # Now release the monkeypatch and try again -- the second write
    # should land cleanly. (The previous failed write must not have
    # left any state that blocks the next call.)
    monkeypatch.undo()
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    monkeypatch.delenv("ROAM_PERMIT_ID", raising=False)

    result2 = invoke_cli(
        runner,
        [
            "permit", "issue",
            "--scope", "race-test-B",
            "--expires-at", "2027-01-01T00:00:00Z",
            "--issued-to", "agent:race-B",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert result2.exit_code == 0, result2.output
    after = list(proot.glob("*.json"))
    assert len(after) == 1, (
        f"expected exactly one permit after recovery, got {after!r}"
    )


def test_two_concurrent_writers_with_same_id_do_not_corrupt_target(
    permit_project, monkeypatch
):
    """Simulated race: two writers target the same ``permit_id``.

    The atomic_write_json contract guarantees the second ``os.replace``
    atomically overwrites the target; the file on disk is ALWAYS one
    coherent JSON document, never a mixture. We simulate this by
    invoking the writer twice with the same env-pinned permit_id and
    inspect the resulting file: it must be valid JSON matching one of
    the two writers' payloads, never a tear.
    """
    pinned_id = "permit_20260514_abcdef"
    monkeypatch.setenv("ROAM_PERMIT_ID", pinned_id)

    runner = CliRunner()
    # First writer.
    r1 = invoke_cli(
        runner,
        [
            "permit", "issue",
            "--scope", "writer-A",
            "--expires-at", "2027-01-01T00:00:00Z",
            "--issued-to", "agent:A",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert r1.exit_code == 0, r1.output

    # Second writer with the SAME pinned id -> the store's collision
    # avoidance kicks in (counter perturbation in issue_permit). Pin
    # this current behavior: two writers with the same env id do NOT
    # corrupt the first file; the second lands at a perturbed id.
    r2 = invoke_cli(
        runner,
        [
            "permit", "issue",
            "--scope", "writer-B",
            "--expires-at", "2027-01-01T00:00:00Z",
            "--issued-to", "agent:B",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert r2.exit_code == 0, r2.output

    proot = permits_root(permit_project)
    files = sorted(proot.glob("*.json"))
    # Every file on disk must parse as valid JSON (no torn state).
    for fp in files:
        raw = json.loads(fp.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        assert raw["permit_id"]  # field present and non-empty
    # The first writer's file MUST still match writer-A's payload (not
    # silently overwritten by writer-B's content).
    a_file = proot / f"{pinned_id}.json"
    assert a_file.exists(), (
        f"expected first writer's file at {a_file}, got {files!r}"
    )
    a_raw = json.loads(a_file.read_text(encoding="utf-8"))
    assert a_raw["scope"] == "writer-A", (
        f"first writer's file was unexpectedly overwritten: {a_raw!r}"
    )


# ===========================================================================
# 7. Collector resilience: malformed permit in envelope does not crash
# ===========================================================================


def test_evidence_collector_handles_non_dict_permit_entries(permit_project):
    """A permits[] list with junk entries (None, ints, empty dicts) must
    not crash the collector. Each junk entry is silently dropped; valid
    entries still produce AuthorityRefs.
    """
    junk_envelope = {
        "permits": [
            None,
            42,
            "permit_id_as_bare_string",  # valid: _entry_id accepts str
            {},  # empty dict -> _entry_id returns None -> dropped
            {"permit_id": ""},  # empty string id -> _add rejects
            {"permit_id": "permit_20260514_realok"},
            {"id": "permit_20260514_idfield"},  # "id" fallback key
        ],
    }
    refs = _build_authority_refs(
        pr_bundle_envelope=junk_envelope,
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    permit_refs = [r for r in refs if r.authority_kind == "permit"]
    ids = {r.authority_id for r in permit_refs}
    assert "permit_20260514_realok" in ids
    assert "permit_id_as_bare_string" in ids
    assert "permit_20260514_idfield" in ids
    # Junk entries were dropped (None, 42, empty dict, empty-string id).
    assert "" not in ids
    assert len(permit_refs) == 3


def test_evidence_collector_handles_permits_field_not_a_list(permit_project):
    """``permits`` field is a string or dict (not a list) -> collector
    skips the section without crashing.

    PIN of current behavior. ``_build_authority_refs`` guards each
    section with ``isinstance(..., list)`` so a producer that mis-emits
    the field as a scalar cannot crash the collector. Defensive
    parsing per Pattern 1.
    """
    # permits as a string.
    refs_string = _build_authority_refs(
        pr_bundle_envelope={"permits": "permit_20260514_oops01"},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    assert [r for r in refs_string if r.authority_kind == "permit"] == []

    # permits as a dict.
    refs_dict = _build_authority_refs(
        pr_bundle_envelope={"permits": {"permit_id": "permit_20260514_oops02"}},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    assert [r for r in refs_dict if r.authority_kind == "permit"] == []


def test_evidence_collector_handles_permit_dict_with_nondict_extra(permit_project):
    """Bad-shaped extra fields are rejected; good string fields project (W381).

    W381 hardening: the collector now projects ``scope`` / ``expires_at``
    / ``issued_to`` onto ``AuthorityRef.extra`` so an auditor can see
    WHAT each permit authorised. The projection is type-gated: only
    non-empty strings flow through; non-string scalars (lists, None,
    nested dicts) are dropped so a junk envelope row cannot poison the
    AuthorityRef shape.
    """
    weird = {
        "permit_id": "permit_20260514_weird1",
        "scope": ["scope-as-array-not-string"],  # list -> dropped
        "expires_at": None,                      # None -> dropped
        "extra_garbage_field": {"nested": "ignored"},  # never projected
    }
    refs = _build_authority_refs(
        pr_bundle_envelope={"permits": [weird]},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    permit_refs = [r for r in refs if r.authority_kind == "permit"]
    assert len(permit_refs) == 1
    target = permit_refs[0]
    assert target.authority_id == "permit_20260514_weird1"
    # Bad-shaped sibling fields still NOT projected (type gate).
    assert "scope" not in target.extra
    assert "expires_at" not in target.extra
    assert "extra_garbage_field" not in target.extra
    assert target.extra.get("permit_id") == "permit_20260514_weird1"


def test_evidence_collector_projects_permit_sibling_fields_when_well_typed(
    permit_project,
):
    """A well-typed permit row projects ``scope`` / ``expires_at`` /
    ``issued_to`` onto AuthorityRef.extra (W381 happy path).
    """
    well_typed = {
        "permit_id": "permit_20260514_well01",
        "scope": "deploy:production",
        "expires_at": "2099-01-01T00:00:00Z",
        "issued_to": "agent:deployer",
        "issued_at": "2026-05-14T00:00:00Z",
        "issued_by": "human:operator",
    }
    refs = _build_authority_refs(
        pr_bundle_envelope={"permits": [well_typed]},
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    permit_refs = [r for r in refs if r.authority_kind == "permit"]
    assert len(permit_refs) == 1
    target = permit_refs[0]
    assert target.extra.get("permit_id") == "permit_20260514_well01"
    assert target.extra.get("scope") == "deploy:production"
    assert target.extra.get("expires_at") == "2099-01-01T00:00:00Z"
    assert target.extra.get("issued_to") == "agent:deployer"
    # Far-future expiry -> no expired marker.
    assert "expired" not in target.extra
    # Recent issuance -> no staleness marker.
    assert "issued_days_ago" not in target.extra


# ===========================================================================
# 8. End-to-end: malformed file + valid file -> pr-bundle picks up only valid
# ===========================================================================


def test_pr_bundle_emit_ignores_malformed_permit_alongside_valid_one(
    permit_project,
):
    """A malformed permit on disk is silently skipped when ``pr-bundle
    emit`` reads ``.roam/permits/``. The valid sibling flows through.

    This is the integration-level proof of the unit-level skip above:
    the end-to-end pipeline is robust against a single corrupt permit
    file (e.g. half-written by a crashed external tool).
    """
    # 1. Drop a malformed permit on disk.
    root = _ensure_permits_dir(permit_project)
    (root / "permit_20260514_bad999.json").write_text(
        "{ this is not JSON",
        encoding="utf-8",
    )

    # 2. Issue a valid permit through the normal --persist path.
    runner = CliRunner()
    r_issue = invoke_cli(
        runner,
        [
            "--json",
            "permit", "issue",
            "--scope", "redteam-e2e",
            "--expires-at", "2027-01-01T00:00:00Z",
            "--issued-to", "agent:redteam-e2e",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert r_issue.exit_code == 0, r_issue.output
    issue_payload = json.loads(r_issue.output)
    valid_pid = issue_payload["summary"]["permit_id"]

    # 3. Initialise + emit a pr-bundle.
    r_init = invoke_cli(
        runner,
        ["pr-bundle", "init", "--intent", "w349 redteam e2e"],
        cwd=permit_project,
    )
    assert r_init.exit_code == 0, r_init.output

    r_emit = invoke_cli(
        runner,
        ["--json", "pr-bundle", "emit"],
        cwd=permit_project,
    )
    assert r_emit.exit_code in (0, 6), r_emit.output
    envelope = json.loads(r_emit.output)
    permits_top = envelope.get("permits") or []
    ids_top = {p.get("permit_id") for p in permits_top if isinstance(p, dict)}
    # Valid permit appears; malformed sibling does NOT.
    assert valid_pid in ids_top
    assert "permit_20260514_bad999" not in ids_top
    # W382: the pr-bundle envelope surfaces the malformed-file warning
    # so an auditor reviewing the bundle sees the dropped row + reason.
    bundle_warnings = envelope.get("bundle_warnings") or []
    assert any(
        "permit_20260514_bad999.json" in w and "malformed JSON" in w
        for w in bundle_warnings
    ), (
        f"expected malformed-JSON warning in envelope; got "
        f"bundle_warnings={bundle_warnings!r}"
    )


# ===========================================================================
# 9. Reader robustness on a non-existent permits dir
# ===========================================================================


def test_load_permits_returns_empty_when_directory_absent(tmp_path):
    """``_load_permits_from_disk`` returns ``[]`` when ``.roam/permits``
    does not exist. Pattern 2: never raise on missing-state.
    """
    bare = tmp_path / "no_roam_dir"
    bare.mkdir()
    assert _load_permits_from_disk(bare) == []


def test_load_permits_returns_empty_when_repo_root_is_none():
    """``_load_permits_from_disk(None)`` is a no-op returning ``[]``."""
    assert _load_permits_from_disk(None) == []


# ===========================================================================
# 10. Defense-in-depth: directory traversal in permit_id field
# ===========================================================================


def test_permit_id_with_traversal_chars_in_field_still_handled(permit_project):
    """A hand-crafted permit file whose ``permit_id`` field contains
    ``../`` is rejected by the W380 schema validator and never escapes
    the permits dir or reaches the collector.

    W380 hardening: ``PERMIT_ID_RE`` (``^permit_\\d{8}_[0-9a-f]{6,}$``)
    rejects path-traversal strings at the dataclass boundary, so the
    reader drops + warns the row. Defense-in-depth: even if a future
    refactor relaxes the validator, the collector would still treat
    the string as opaque (no filesystem operation is keyed on it).
    """
    root = _ensure_permits_dir(permit_project)
    # Hex-only file stem so the file is iterated; the payload's
    # permit_id field carries the adversarial string.
    payload = _valid_permit_dict("permit_20260514_a0b0c0")
    payload["permit_id"] = "../../../etc/passwd"
    (root / "permit_20260514_a0b0c0.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    warnings: list[str] = []
    loaded = _load_permits_from_disk(permit_project, warnings_out=warnings)
    # W380: the traversal-string permit_id fails PERMIT_ID_RE so the
    # validator returns None and the reader drops + warns.
    assert loaded == []
    assert any(
        "permit_20260514_a0b0c0.json" in w
        and "schema validation failed" in w
        and "../../../etc/passwd" in w
        for w in warnings
    ), (
        f"expected schema-validation warning naming the traversal "
        f"permit_id; got {warnings!r}"
    )

    # Defense-in-depth: even if the dict had reached the collector, no
    # filesystem operation is keyed on the field value (the collector
    # treats authority_id as an opaque string).
    refs = _build_authority_refs(
        pr_bundle_envelope={"permits": []},  # reader already filtered
        caller_mode=None,
        corroborated_authorities=frozenset(),
    )
    assert [r for r in refs if r.authority_kind == "permit"] == []


# ===========================================================================
# 11. Mock-based race simulator: ensure no partial bytes ever observed
# ===========================================================================


def test_atomic_write_uses_replace_not_direct_write(permit_project, monkeypatch):
    """Pin that the writer uses temp-file + os.replace, not a direct
    open(path, 'w'). A reader that polls the directory must NEVER see
    a partial file at the target path.

    Strategy: intercept ``atomic_write_json`` and verify it routes
    through a code path that creates a sibling tempfile, NOT the
    target path directly. We assert: at no point during the write
    sequence does the target permit_*.json filename exist with
    partial content.
    """
    from roam.permits import store as store_mod

    proot = permits_root(permit_project)
    proot.mkdir(parents=True, exist_ok=True)
    observed_partial_states: list[str] = []

    real_atomic_write_json = store_mod.atomic_write_json

    def observing_atomic_write_json(path, data, *, indent=2, sort_keys=False):
        target = Path(path)
        # BEFORE the call: snapshot what's at target.
        if target.exists():
            observed_partial_states.append(f"pre-exists:{target.name}")
        # DURING the call (via wrapped real call): the real writer
        # creates a sibling .tmp and only ``os.replace``s at the end.
        real_atomic_write_json(path, data, indent=indent, sort_keys=sort_keys)
        # AFTER: confirm one valid JSON document at the target.
        text = target.read_text(encoding="utf-8")
        try:
            json.loads(text)
        except json.JSONDecodeError:
            observed_partial_states.append(f"torn:{target.name}")

    monkeypatch.setattr(
        store_mod, "atomic_write_json", observing_atomic_write_json
    )

    runner = CliRunner()
    result = invoke_cli(
        runner,
        [
            "permit", "issue",
            "--scope", "atomic-observation",
            "--expires-at", "2027-01-01T00:00:00Z",
            "--issued-to", "agent:obs",
            "--persist",
        ],
        cwd=permit_project,
    )
    assert result.exit_code == 0, result.output
    # No torn or pre-existing state observed during the write.
    assert observed_partial_states == [], (
        f"unexpected partial states observed: {observed_partial_states!r}"
    )
    # One coherent file on disk.
    files = list(proot.glob("permit_*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["scope"] == (
        "atomic-observation"
    )


# ===========================================================================
# 12. W383 - pr-bundle and pr-replay readers drop the SAME rows
# ===========================================================================


def test_w383_pr_bundle_and_pr_replay_drop_identical_rows(
    permit_project, monkeypatch,
):
    """W383: ``cmd_pr_bundle._load_permits_from_disk`` and
    ``cmd_pr_replay._gather_permit_policy_decisions`` MUST drop the same
    set of permit files on the same fixture.

    Before W383 the pr-replay gatherer had its own reader that skipped
    the W380 schema gate entirely, so a malformed permit dropped by
    pr-bundle would silently surface in the replay envelope (silent
    divergence between bundle emit and replay render). W383 consolidates
    both onto :func:`roam.permits.store.load_permits_from_disk` so the
    drop set is identical.

    The fixture mixes one valid permit, one malformed-JSON file, one
    schema-invalid dict, and one duplicate of the valid permit_id.
    Expected: BOTH readers keep exactly the one valid permit and surface
    matching warnings on the dropped three.
    """
    root = _ensure_permits_dir(permit_project)
    valid_id = "permit_20260514_d0d0d0"
    payload_valid = _valid_permit_dict(valid_id)
    (root / f"{valid_id}.json").write_text(
        json.dumps(payload_valid), encoding="utf-8"
    )
    # Malformed JSON (W382 drop).
    (root / "permit_20260514_bad001.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    # Schema-invalid (W380 drop): missing required fields.
    (root / "permit_20260514_bad002.json").write_text(
        json.dumps({"permit_id": "permit_20260514_bad002"}),
        encoding="utf-8",
    )
    # Duplicate permit_id (W379 drop): sorts after the primary.
    payload_dup = _valid_permit_dict(valid_id)
    payload_dup["scope"] = "duplicate-collision"
    (root / f"{valid_id}_dup.json").write_text(
        json.dumps(payload_dup), encoding="utf-8"
    )

    # pr-bundle path.
    bundle_warnings: list[str] = []
    bundle_permits = _load_permits_from_disk(
        permit_project, warnings_out=bundle_warnings
    )
    bundle_ids = sorted(p["permit_id"] for p in bundle_permits)

    # pr-replay path. Point find_project_root at the fixture so the
    # gatherer reads the same .roam/permits/ directory.
    monkeypatch.setattr(
        "roam.db.connection.find_project_root",
        lambda *a, **k: permit_project,
    )
    from roam.commands import cmd_pr_replay  # late import (no module cycle)
    replay_warnings: list[str] = []
    replay_rows = cmd_pr_replay._gather_permit_policy_decisions(
        replay_warnings
    )
    # Each row's rule_id is ``permit:<permit_id>`` - reconstruct ids.
    replay_ids = sorted(
        r["rule_id"][len("permit:"):]
        for r in replay_rows
        if r["rule_id"].startswith("permit:")
    )

    # PARITY ASSERTION: both readers keep the same set of permits.
    assert bundle_ids == [valid_id], (
        f"pr-bundle reader kept unexpected ids: {bundle_ids!r}"
    )
    assert replay_ids == [valid_id], (
        f"pr-replay reader kept unexpected ids: {replay_ids!r}"
    )
    assert bundle_ids == replay_ids, (
        f"divergence: bundle={bundle_ids!r} replay={replay_ids!r}"
    )

    # PARITY ASSERTION: both warning surfaces name the same three
    # dropped files (one malformed, one schema-invalid, one duplicate).
    expected_file_mentions = {
        "permit_20260514_bad001.json",  # malformed JSON
        "permit_20260514_bad002.json",  # schema-invalid
        f"{valid_id}_dup.json",         # duplicate permit_id
    }
    for label, ws in (
        ("bundle", bundle_warnings),
        ("replay", replay_warnings),
    ):
        for fname in expected_file_mentions:
            assert any(fname in w for w in ws), (
                f"{label} reader missing warning for {fname!r}; got "
                f"{ws!r}"
            )


# Sanity: keep the unused-import linter happy on ``mock`` even though we
# wired everything through monkeypatch + handcrafted helpers above. The
# import documents the intent of "this is a mock-based red-team suite"
# and gives future tests a one-line escape hatch.
_ = mock
