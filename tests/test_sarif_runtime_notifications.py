"""W1046: SARIF runtime-notifications opt-in via ``emit_runtime_notifications``.

Builds on W1042 (which plumbed ``warnings_out`` through
``sarif._load_suppressions`` + ``sarif._load_suppressions_typed`` but kept
the warnings OFF the SARIF document by default to preserve byte-stability
of existing SARIF output).

SARIF 2.1.0 defines ``run.invocations[].toolExecutionNotifications[]`` for
tool-runtime issues (loader failures, malformed configs, ...). The opt-in
flag projects W1042 warnings onto that array as:

    {
      "level": "warning",
      "descriptor": {"id": "suppressions.malformed-entry"},
      "message": {"text": "..."}
    }

Hard invariant under test: default ``emit_runtime_notifications=False``
produces byte-identical SARIF output to pre-W1046, even when a malformed
``.roam/suppressions.json`` is present (because the default loader call
no longer threads ``warnings_out``).
"""

from __future__ import annotations

import json

from roam.output.sarif import complexity_to_sarif, to_sarif


def test_default_byte_identical_to_pre_w1046_clean(tmp_path, monkeypatch) -> None:
    """Default (no opt-in) + no suppressions.json — no invocations[] in SARIF."""
    monkeypatch.chdir(tmp_path)
    doc = to_sarif("roam-code", "9.9.9", rules=[], results=[])
    run = doc["runs"][0]
    assert "invocations" not in run


def test_default_byte_identical_to_pre_w1046_with_malformed_suppressions(tmp_path, monkeypatch) -> None:
    """Default (no opt-in) + malformed suppressions.json — still no invocations[].

    This is the byte-stability regression-guard: the opt-in flag is the
    ONLY gate that can put `invocations[]` on the SARIF document.
    """
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text("{not valid json: [", encoding="utf-8")
    doc = to_sarif("roam-code", "9.9.9", rules=[], results=[])
    run = doc["runs"][0]
    assert "invocations" not in run


def test_opt_in_no_warnings_emits_empty_notifications(tmp_path, monkeypatch) -> None:
    """Opt-in + clean suppressions state — emits an empty notifications array.

    The ``invocations[]`` key + empty ``toolExecutionNotifications: []``
    array let consumers distinguish "opted in + clean run" from "did not
    opt in" (the documented contract).
    """
    monkeypatch.chdir(tmp_path)
    doc = to_sarif(
        "roam-code",
        "9.9.9",
        rules=[],
        results=[],
        emit_runtime_notifications=True,
    )
    run = doc["runs"][0]
    assert "invocations" in run
    assert len(run["invocations"]) == 1
    assert run["invocations"][0]["toolExecutionNotifications"] == []
    assert run["invocations"][0]["executionSuccessful"] is True


def test_opt_in_with_malformed_json_emits_notifications(tmp_path, monkeypatch) -> None:
    """Opt-in + malformed JSON — notifications carry W1042 warning text.

    Both ``_load_suppressions_typed`` and ``_load_suppressions`` (called
    when the typed loader returns empty) see the same malformed file and
    each appends a warning. Two notifications is the honest count under
    the current loader chain; the descriptor.id + message.text are the
    contract being asserted, not the cardinality.
    """
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text("{not valid json: [", encoding="utf-8")

    doc = to_sarif(
        "roam-code",
        "9.9.9",
        rules=[],
        results=[],
        emit_runtime_notifications=True,
    )
    run = doc["runs"][0]
    notifications = run["invocations"][0]["toolExecutionNotifications"]
    assert len(notifications) >= 1
    for note in notifications:
        assert note["level"] == "warning"
        assert note["descriptor"]["id"] == "suppressions.malformed-entry"
        assert "malformed JSON" in note["message"]["text"]
        assert "sarif-suppressions" in note["message"]["text"]


def test_opt_in_with_malformed_entry_emits_notifications(tmp_path, monkeypatch) -> None:
    """Opt-in + legacy list shape with a bogus entry — notifications fire.

    The typed loader warns once (legacy-list-shape unsupported), then the
    legacy dict loader is consulted because the typed loader returned
    empty; it warns again on the bogus list entry. Both notifications
    carry the closed descriptor.id.
    """
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text(
        json.dumps(
            [
                {"rule_id": "ROAM-DEMO-1", "location": "src/x.py:10"},
                "not a dict",
            ]
        ),
        encoding="utf-8",
    )

    doc = to_sarif(
        "roam-code",
        "9.9.9",
        rules=[],
        results=[],
        emit_runtime_notifications=True,
    )
    run = doc["runs"][0]
    notifications = run["invocations"][0]["toolExecutionNotifications"]
    assert len(notifications) >= 1
    for note in notifications:
        assert note["level"] == "warning"
        assert note["descriptor"]["id"] == "suppressions.malformed-entry"
    # At least one notification names the bogus entry index.
    assert any("entry #2" in n["message"]["text"] and "Skipping entry." in n["message"]["text"] for n in notifications)


def test_sarif_doc_still_parses_as_valid_json(tmp_path, monkeypatch) -> None:
    """Schema-shape sanity: emitting notifications doesn't break JSON shape."""
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text("{not valid json: [", encoding="utf-8")
    doc = to_sarif(
        "roam-code",
        "9.9.9",
        rules=[],
        results=[],
        emit_runtime_notifications=True,
    )
    # Round-trip through json.dumps/loads — proves the dict is JSON-safe.
    redux = json.loads(json.dumps(doc))
    assert redux["version"] == "2.1.0"
    assert isinstance(redux["runs"], list) and len(redux["runs"]) == 1
    run = redux["runs"][0]
    assert "invocations" in run
    assert "toolExecutionNotifications" in run["invocations"][0]


def test_complexity_to_sarif_plumbs_producer_warnings(tmp_path, monkeypatch) -> None:
    """W1060: ``complexity_to_sarif(..., warnings=[...])`` projects producer
    advisories onto ``run.invocations[0].toolExecutionNotifications[]``.

    The closed-enum ``descriptor.id`` for caller-supplied warnings is
    ``producer.advisory-warning`` (distinct from the SARIF-loader
    ``suppressions.malformed-entry`` so consumers can tell loader-class
    advisories apart from producer-class advisories). The notification
    ``message.text`` must equal the producer warning string verbatim.
    """
    monkeypatch.chdir(tmp_path)
    warning_text = "pre-W89 schema; complexity findings not persisted"
    doc = complexity_to_sarif(complex_symbols=[], threshold=15, warnings=[warning_text])
    run = doc["runs"][0]
    assert "invocations" in run
    assert len(run["invocations"]) == 1
    notifications = run["invocations"][0]["toolExecutionNotifications"]
    assert len(notifications) == 1
    note = notifications[0]
    assert note["level"] == "warning"
    assert note["descriptor"]["id"] == "producer.advisory-warning"
    assert note["message"]["text"] == warning_text


def test_complexity_to_sarif_empty_warnings_byte_identical(tmp_path, monkeypatch) -> None:
    """Hash-stability invariant: empty / None ``warnings`` reproduces pre-W1060
    SARIF bytes exactly.

    Three calls — kwarg omitted, ``warnings=None``, ``warnings=[]`` — must
    all produce byte-identical SARIF output, and none of them may emit the
    ``invocations`` key (because ``emit_runtime_notifications=bool([]) is
    False`` short-circuits the W1046 emission block in :func:`to_sarif`).
    """
    monkeypatch.chdir(tmp_path)
    doc_omit = complexity_to_sarif(complex_symbols=[], threshold=15)
    doc_none = complexity_to_sarif(complex_symbols=[], threshold=15, warnings=None)
    doc_empty = complexity_to_sarif(complex_symbols=[], threshold=15, warnings=[])
    # No invocations key on any default-path call.
    for d in (doc_omit, doc_none, doc_empty):
        assert "invocations" not in d["runs"][0]
    # Strip the date-stamped automationDetails.guid so the byte-equality
    # check doesn't false-positive across midnight (mirrors the W1046
    # ``test_byte_identical_fixture_default_vs_pre_w1046`` discipline).
    for d in (doc_omit, doc_none, doc_empty):
        d["runs"][0]["automationDetails"].pop("guid", None)
    omit_bytes = json.dumps(doc_omit, sort_keys=True)
    assert omit_bytes == json.dumps(doc_none, sort_keys=True)
    assert omit_bytes == json.dumps(doc_empty, sort_keys=True)


def test_byte_identical_fixture_default_vs_pre_w1046(tmp_path, monkeypatch) -> None:
    """Hash-stability regression-guard.

    Two `to_sarif()` calls — one with the default flag, one with
    ``emit_runtime_notifications=False`` explicitly — must produce
    byte-identical JSON output on the malformed-suppressions input.
    Together with the "default produces no invocations[]" check above,
    this proves the default path stays byte-identical to pre-W1046.
    """
    monkeypatch.chdir(tmp_path)
    suppressions_dir = tmp_path / ".roam"
    suppressions_dir.mkdir()
    (suppressions_dir / "suppressions.json").write_text("{not valid json: [", encoding="utf-8")
    doc_implicit = to_sarif("roam-code", "9.9.9", rules=[], results=[])
    doc_explicit = to_sarif(
        "roam-code",
        "9.9.9",
        rules=[],
        results=[],
        emit_runtime_notifications=False,
    )
    # Strip the time-stamped automationDetails.guid — it embeds today's
    # date string and would create false positives if the tests straddle
    # midnight. The W1046 surface lives on `runs[0]`, not on the guid.
    for d in (doc_implicit, doc_explicit):
        d["runs"][0]["automationDetails"].pop("guid", None)
    assert json.dumps(doc_implicit, sort_keys=True) == json.dumps(doc_explicit, sort_keys=True)
