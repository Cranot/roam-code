"""W1060: Verify the W1046 ``emit_runtime_notifications`` opt-in is wired
through ``health_to_sarif`` (W1084) and ``complexity_to_sarif`` (W1060)
producer warnings.

Context: W1046 added an opt-in kwarg on :func:`roam.output.sarif.to_sarif`
that projects advisory warnings onto
``run.invocations[].toolExecutionNotifications[]``. W1084 wired the
``cmd_health`` gate-config loader's silent-fallback warnings through
:func:`health_to_sarif`. W1060 / W1086 wired ``cmd_complexity``'s
``warnings`` accumulator through :func:`complexity_to_sarif`.

This file is an activation regression-guard: when a producer collects an
advisory warning AND emits SARIF, the warning MUST land on
``toolExecutionNotifications[]`` with the closed-enum
``descriptor.id: "producer.advisory-warning"``.

Note ``cmd_doctor`` is intentionally absent: it does NOT emit SARIF
(see ``src/roam/commands/cmd_doctor.py`` module docstring, lines 7-10 —
"SARIF is deliberately NOT emitted because doctor checks are
environment-scoped … SARIF requires locations[]; doctor has no source
coordinates"). Wiring SARIF runtime-notifications for doctor is a
separate, larger wave that would first need a ``doctor_to_sarif`` +
``--sarif`` flag on doctor itself.
"""

from __future__ import annotations

from roam.output.sarif import complexity_to_sarif, health_to_sarif

_PRODUCER_ID = "producer.advisory-warning"


def test_health_to_sarif_plumbs_producer_warnings_via_w1084() -> None:
    """W1084: ``cmd_health._gate_warnings`` advisory warnings (malformed
    ``.roam-gates.yml`` shape) project onto
    ``run.invocations[0].toolExecutionNotifications[]`` when the producer
    opts in. Mirrors the W1060 ``complexity_to_sarif`` shape exactly so
    SARIF consumers can apply one parser to both surfaces.
    """
    warning_text = "health-gate: '.roam-gates.yml' has no `health:` key. Treating as default gates."
    doc = health_to_sarif(
        issues={"cycles": [], "god_components": [], "bottlenecks": [], "layer_violations": []},
        emit_runtime_notifications=True,
        warnings_out=[warning_text],
    )
    run = doc["runs"][0]
    assert "invocations" in run
    notifications = run["invocations"][0]["toolExecutionNotifications"]
    assert len(notifications) == 1
    note = notifications[0]
    assert note["level"] == "warning"
    assert note["descriptor"]["id"] == _PRODUCER_ID
    assert note["message"]["text"] == warning_text


def test_complexity_to_sarif_plumbs_producer_warnings_via_w1060() -> None:
    """W1060: ``cmd_complexity.warnings`` accumulator entries (count-probe
    failure, pre-W89 schema findings-table missing on ``--persist``)
    project onto ``run.invocations[0].toolExecutionNotifications[]``.

    Mirrors :data:`test_complexity_to_sarif_plumbs_producer_warnings`
    in ``tests/test_sarif_runtime_notifications.py`` but uses the
    no-symbols + ``threshold=0`` path to exercise the empty-results
    branch the cmd_complexity ``--sarif`` flag hits when the underlying
    symbol_metrics table is empty.
    """
    warning_text = "symbol_metrics count probe failed; treating as empty"
    doc = complexity_to_sarif(complex_symbols=[], threshold=0, warnings=[warning_text])
    run = doc["runs"][0]
    assert "invocations" in run
    notifications = run["invocations"][0]["toolExecutionNotifications"]
    assert len(notifications) == 1
    note = notifications[0]
    assert note["level"] == "warning"
    assert note["descriptor"]["id"] == _PRODUCER_ID
    assert note["message"]["text"] == warning_text


def test_health_to_sarif_hash_stable_when_warnings_empty() -> None:
    """Hash-stability invariant: omitting / passing empty warnings_out to
    :func:`health_to_sarif` does NOT emit the ``invocations`` key.

    Mirrors :data:`test_complexity_to_sarif_empty_warnings_byte_identical`
    in the sibling test file. Asserts pre-W1084 byte-stability for
    callers that don't opt into runtime-notifications.
    """
    issues = {"cycles": [], "god_components": [], "bottlenecks": [], "layer_violations": []}
    doc_omit = health_to_sarif(issues)
    doc_none = health_to_sarif(issues, emit_runtime_notifications=False, warnings_out=None)
    doc_empty = health_to_sarif(issues, emit_runtime_notifications=False, warnings_out=[])
    for d in (doc_omit, doc_none, doc_empty):
        assert "invocations" not in d["runs"][0]
