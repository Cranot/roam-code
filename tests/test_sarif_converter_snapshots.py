"""Byte snapshots for the nine-member SARIF converter template family."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import pytest

from roam.output import sarif


def _snapshot_cases() -> dict[str, tuple[Callable[..., dict], object, dict[str, object]]]:
    return {
        "affected_tests": (
            sarif.affected_tests_to_sarif,
            {
                "command": "affected-tests",
                "summary": {"target": "handle_login (fn, src/auth.py:42)"},
                "tests": [
                    {
                        "file": "tests/test_auth.py",
                        "symbol": "test_handle_login",
                        "kind": "DIRECT",
                        "hops": 1,
                        "via": None,
                    },
                    {
                        "file": "tests/test_session.py",
                        "symbol": "test_session_refresh",
                        "kind": "TRANSITIVE",
                        "hops": 3,
                        "via": "refresh_session",
                    },
                    {
                        "file": "tests/test_auth_helpers.py",
                        "symbol": None,
                        "kind": "COLOCATED",
                        "hops": None,
                        "via": None,
                    },
                ],
            },
            {},
        ),
        "delete_check": (
            sarif.delete_check_to_sarif,
            {
                "command": "delete-check",
                "deletions": [
                    {
                        "kind": "symbol",
                        "name": "handleSave",
                        "from_file": "src/ui/form.py",
                        "from_line": 42,
                        "verdict": "BREAK-RISK",
                        "reason": "2 surviving reachable code references",
                        "survivors": [
                            {"path": "src/ui/main.py", "line": 17},
                            {"path": "src/ui/dialogs.py", "line": 88},
                        ],
                    },
                    {
                        "kind": "symbol",
                        "name": "old_helper",
                        "from_file": "src/legacy/util.py",
                        "from_line": 100,
                        "verdict": "LIKELY-SAFE",
                        "reason": "one test reference remains",
                        "survivors": [{"path": "tests/test_util.py", "line": 5}],
                    },
                    {
                        "kind": "file",
                        "name": "src/legacy/dead.py",
                        "from_file": "src/legacy/dead.py",
                        "from_line": 0,
                        "verdict": "SAFE",
                        "reason": "no surviving references",
                        "survivors": [],
                    },
                ],
            },
            {},
        ),
        "n1": (
            sarif.n1_to_sarif,
            [
                {
                    "model_name": "App\\Models\\User",
                    "accessor_name": "getProfileAttribute",
                    "accessor_location": "app/Models/User.php:42",
                    "appended_attribute": "profile",
                    "relationship": "profile",
                    "io_type": "belongsTo",
                    "confidence": "high",
                    "suggestion": "Add ::with('profile')",
                },
                {
                    "model_name": "Post",
                    "accessor_name": "comment_count",
                    "accessor_location": "models.py:25",
                    "appended_attribute": "comment_count",
                    "relationship": "comments",
                    "io_type": "all",
                    "confidence": "medium",
                    "suggestion": "Use prefetch_related('comments')",
                },
                {
                    "model_name": "Tag",
                    "accessor_name": "slug",
                    "accessor_location": "models.py:60",
                    "confidence": "low",
                },
            ],
            {},
        ),
        "missing_index": (
            sarif.missing_index_to_sarif,
            [
                {
                    "query_location": "app/repositories/users.py:31",
                    "confidence": "high",
                    "table": "users",
                    "columns": ["tenant_id", "email"],
                    "pattern_type": "where_paginate",
                    "has_paginate": True,
                    "issue": "full table scan",
                    "suggestion": "Add index users(tenant_id, email)",
                },
                {
                    "query_location": "app/repositories/orders.py:18",
                    "confidence": "medium",
                    "table": "orders",
                    "columns": ["created_at"],
                    "pattern_type": "order_by",
                    "has_paginate": False,
                    "issue": "filesort",
                    "suggestion": "Add index orders(created_at)",
                },
                {
                    "query_location": "app/repositories/events.py:9",
                    "confidence": "low",
                    "table": "events",
                    "columns": ["account_id", "timestamp"],
                    "pattern_type": "orderby_with_where",
                    "has_paginate": False,
                },
            ],
            {},
        ),
        "orphan_imports": (
            sarif.orphan_imports_to_sarif,
            [
                {
                    "language": "python",
                    "file": "src/app/service.py",
                    "line": 7,
                    "module": "app.modles.user",
                    "kind": "internal_typo",
                    "hint": "Did you mean app.models.user?",
                },
                {
                    "language": "python",
                    "file": "src/app/report.py",
                    "line": 11,
                    "module": "optional_pdf",
                    "kind": "missing_package",
                    "hint": "Package is not installed",
                },
                {
                    "language": "javascript",
                    "file": "web/src/view.js",
                    "line": 3,
                    "module": "./missing-widget",
                    "kind": "missing_local",
                    "hint": "No indexed local module matches",
                },
            ],
            {},
        ),
        "over_fetch": (
            sarif.over_fetch_to_sarif,
            [
                {
                    "state": "BARE",
                    "severity": "H",
                    "file": "app/Http/Controllers/UserController.php",
                    "line": 55,
                    "endpoint": "GET /users",
                    "evidence": "returns User::all() directly",
                    "recommendation": "Select only response fields",
                },
                {
                    "model_path": "app/Models/User.php",
                    "model_location": "app/Models/User.php:12",
                    "model_name": "User",
                    "confidence": "medium",
                    "fillable_count": 18,
                    "hidden_count": 2,
                    "exposed_count": 16,
                    "reasons": ["wide fillable surface", "direct controller return"],
                },
            ],
            {},
        ),
        "laws": (
            sarif.laws_to_sarif,
            [
                {
                    "law_id": "LAW-IMPORT-1",
                    "kind": "import",
                    "severity": "blocker",
                    "message": "handlers must not import persistence adapters",
                    "file": "src/handlers/save.py",
                    "line": 14,
                },
                {
                    "law_id": "LAW-NAME-2",
                    "kind": "naming",
                    "severity": "advisory",
                    "message": "function names use snake_case",
                    "file": "src/api/users.py",
                    "line": 21,
                },
            ],
            {"disclosures": ["laws: cached law set used after parse warning"]},
        ),
        "fan": (
            sarif.fan_to_sarif,
            [
                {
                    "mode": "symbol",
                    "flag": "HIGH-RISK",
                    "symbol_name": "dispatch",
                    "file_path": "src/core/dispatch.py",
                    "line_start": 19,
                    "fan_in": 28,
                    "fan_out": 17,
                },
                {
                    "mode": "symbol",
                    "flag": "spreader",
                    "symbol_name": "bootstrap",
                    "location": "src/app/bootstrap.py:8",
                    "fan_in": 1,
                    "fan_out": 22,
                },
                {
                    "mode": "file",
                    "flag": "hub",
                    "path": "src/contracts.py",
                    "fan_in": 31,
                    "fan_out": 2,
                },
            ],
            {},
        ),
        "flag_dead": (
            sarif.flag_dead_to_sarif,
            [
                {
                    "flag_name": "checkout-v1",
                    "staleness": "stale",
                    "provider": "launchdarkly",
                    "count": 2,
                    "reasons": ["listed in known-stale config"],
                    "locations": [
                        {"file": "src/checkout.py", "line": 22},
                        {"file": "src/payment.py", "line": 45},
                    ],
                },
                {
                    "flag_name": "single-use-beta",
                    "staleness": "likely_stale",
                    "provider": "unleash",
                    "count": 1,
                    "reasons": ["single reference"],
                    "locations": [{"file": "src/beta.py", "line": 7}],
                },
                {
                    "flag_name": "same-default",
                    "staleness": "suspect",
                    "provider": "generic",
                    "count": 3,
                    "reasons": ["same constant default at every call site"],
                    "locations": [{"file": "src/flags.py", "line": 10}],
                },
            ],
            {
                "emit_runtime_notifications": True,
                "warnings_out": ["flag-dead: known-stale config used fallback encoding"],
            },
        ),
    }


_EXPECTED_SNAPSHOTS: dict[str, tuple[int, str]] = {
    "affected_tests": (3162, "50158399bf22f48c68679658adba8196989d3794fb64c06d49ce374c47cac0d2"),
    # The incomplete-search REVIEW rule extends the catalogue; existing results
    # are unchanged (removing that one rule reproduces the prior byte snapshot).
    "delete_check": (4455, "c32a9d45ade01e75f0e6fda86875d8e3c1c907cd566cf6c0ce3146757298085a"),
    "n1": (4379, "1b32d50cb7fa6c798e76f52982270739abe93ec94ebbf63ed77b7367dcba0a64"),
    "missing_index": (4539, "c40bc94dca0c9fecc3462db5d1b20e87df674a58c54adcdf085e8dc30478fdb3"),
    "orphan_imports": (4643, "d7d030fff59e696a2ee9494a45f48e5d597430a318e31cfd14c92675086da1ea"),
    "over_fetch": (2765, "abc587faacf2be3808e7068e341acbc35b03fe40038f47372c6fa0b152adb7d1"),
    "laws": (4075, "2a207f905ee8e301cbb433d1396e23fe132cfb4d55efe3e5b9ce64f11f41d624"),
    "fan": (3252, "6fd069bae6d6dff3d3fe810d5a848ae75f955f69777d19ed3f7f9f1f8727ad67"),
    "flag_dead": (4119, "01e6d32f0103f53dbc04bfd1d520415811fa40facd62db2e82a0025ae8930a21"),
}


@pytest.mark.parametrize("case_name", _snapshot_cases())
def test_converter_output_byte_snapshot(case_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Representative output stays byte-identical across template extraction."""
    monkeypatch.setattr(sarif, "_get_version", lambda: "snapshot-version")
    monkeypatch.setattr(sarif, "_automation_details", lambda _tool, _version: {"id": "snapshot-run"})
    monkeypatch.setattr(sarif, "_version_control_provenance", lambda: [])
    monkeypatch.setattr(sarif, "_load_suppressions_typed", lambda **_kwargs: [])
    monkeypatch.setattr(sarif, "_load_suppressions", lambda **_kwargs: [])
    converter, payload, kwargs = _snapshot_cases()[case_name]

    output = sarif.write_sarif(converter(payload, **kwargs)).encode()
    expected_length, expected_digest = _EXPECTED_SNAPSHOTS[case_name]

    assert len(output) == expected_length
    assert hashlib.sha256(output).hexdigest() == expected_digest
