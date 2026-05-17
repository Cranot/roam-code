"""W1061-followup — SARIF runtime configurationOverrides[] +
notificationConfigurationOverrides[] for cmd_check_rules / cmd_vulns /
cmd_taint.

Extends W1061 (cmd_smells) coverage to three sibling commands. Per
SARIF 2.1.0 OASIS spec:

- ``ruleConfigurationOverrides[]`` (§3.20.5 + §3.51) is the slot for
  rule-id-level disables — used when a filter operates at rule-id
  granularity (``cmd_check_rules --rule``, ``cmd_taint --rule`` /
  ``--rules-pack``).
- ``notificationConfigurationOverrides[]`` (§3.20.4) is the sibling
  slot for finding-level filters that do NOT map onto rule-id granular
  disable semantics (``cmd_check_rules --severity``,
  ``cmd_vulns --reachable-only``, ``cmd_taint --rules-dir``). Each
  entry references a synthetic notification descriptor (e.g.
  ``severity-filter``, ``reachable-only-filter``, ``rules-dir-filter``)
  rather than a real rule.

Hash invariant: when no filter is applied, the SARIF document MUST be
byte-identical to pre-W1061-followup callers — both override slots
default to ``None`` and the gated emission inside :func:`to_sarif`
suppresses the ``invocations[]`` key entirely.
"""

from __future__ import annotations

from roam.commands.cmd_vulns import _vulns_to_sarif


def test_w1061_followup_check_rules_severity_emits_notification_override() -> None:
    """``cmd_check_rules --severity`` is a finding-level filter — projects
    onto ``notificationConfigurationOverrides[]`` (§3.20.4) NOT
    ``ruleConfigurationOverrides[]``.

    The discipline mirrors W1061's BAIL on ``--min-severity`` for smells:
    severity filters operate at finding-evaluation time. Surfaces under
    a synthetic ``severity-filter`` descriptor with the filter value in
    ``properties``.
    """
    from roam.commands.cmd_check_rules import _results_to_sarif

    results = [
        {
            "id": "max-fan-out",
            "severity": "warning",
            "description": "fan-out check",
            "check": "fan_out",
            "threshold": 10,
            "passed": False,
            "violation_count": 1,
            "violations": [{"symbol": "do_thing", "file": "src/x.py", "line": 1}],
        },
    ]
    sarif_notif_overrides = [
        {
            "configuration": {"enabled": True},
            "descriptor": {"id": "severity-filter"},
            "properties": {"filter": "--severity", "filter_value": "error"},
        }
    ]
    doc = _results_to_sarif(
        results, runtime_notification_overrides=sarif_notif_overrides
    )
    run = doc["runs"][0]
    assert "invocations" in run
    inv = run["invocations"][0]
    # Severity filter is FINDING-level: must NOT appear under rule overrides.
    assert "ruleConfigurationOverrides" not in inv
    # Must appear under notification overrides instead.
    nco = inv["notificationConfigurationOverrides"]
    assert len(nco) == 1
    assert nco[0]["descriptor"]["id"] == "severity-filter"
    assert nco[0]["properties"]["filter"] == "--severity"
    assert nco[0]["properties"]["filter_value"] == "error"


def test_w1061_followup_vulns_reachable_only_emits_notification_override() -> None:
    """``cmd_vulns --reachable-only`` is a finding-level filter (filters
    each vuln row by its ``reachable`` field, not by rule-id) — surfaces
    via ``notificationConfigurationOverrides[]`` with a synthetic
    ``reachable-only-filter`` descriptor.

    Hash invariant guard: passing ``runtime_notification_overrides=None``
    produces zero ``invocations[]`` so default-path SARIF stays
    byte-identical to pre-W1061-followup.
    """
    vulns: list[dict] = []  # empty inventory — filter still surfaces
    notif_overrides = [
        {
            "configuration": {"enabled": True},
            "descriptor": {"id": "reachable-only-filter"},
            "properties": {"filter": "--reachable-only", "filter_value": True},
        }
    ]
    doc = _vulns_to_sarif(vulns, runtime_notification_overrides=notif_overrides)
    run = doc["runs"][0]
    inv = run["invocations"][0]
    nco = inv["notificationConfigurationOverrides"]
    assert len(nco) == 1
    assert nco[0]["descriptor"]["id"] == "reachable-only-filter"
    assert nco[0]["properties"]["filter"] == "--reachable-only"
    assert nco[0]["properties"]["filter_value"] is True
    # Default-path hash invariant: no overrides => no invocations[].
    doc_default = _vulns_to_sarif(vulns)
    assert "invocations" not in doc_default["runs"][0]


def test_w1061_followup_taint_rule_filter_emits_rule_configuration_override() -> None:
    """``cmd_taint --rule`` / ``--rules-pack`` operate at rule-id
    granularity (substring match against ``rule_id``). The caller
    builds a ``configurationOverride`` per disabled rule and the SARIF
    document carries them on
    ``run.invocations[0].ruleConfigurationOverrides[]``.

    SARIF §3.51 contract: each entry MUST carry ``configuration`` (with
    ``enabled: false``) and ``descriptor`` (with ``id`` referencing the
    real rule). ``--rules-dir`` instead surfaces as a notification
    override because it replaces the rule_id namespace entirely; this
    test exercises the rule-id-level path.
    """
    from roam.output.sarif import taint_to_sarif

    findings: list[dict] = []  # filtered "no findings" result
    overrides = [
        {
            "configuration": {"enabled": False},
            "descriptor": {"id": "python-xss"},
            "properties": {"disabled_by": "--rule", "rule_filter": "sqli"},
        },
        {
            "configuration": {"enabled": False},
            "descriptor": {"id": "js-xss"},
            "properties": {"disabled_by": "--rule", "rule_filter": "sqli"},
        },
    ]
    doc = taint_to_sarif(findings, runtime_overrides=overrides)
    run = doc["runs"][0]
    inv = run["invocations"][0]
    rco = inv["ruleConfigurationOverrides"]
    assert len(rco) == 2
    for entry in rco:
        assert entry["configuration"]["enabled"] is False
        assert "descriptor" in entry
        assert entry["properties"]["disabled_by"] == "--rule"
    assert {e["descriptor"]["id"] for e in rco} == {"python-xss", "js-xss"}
    # Default-path hash invariant: no overrides => no invocations[].
    doc_default = taint_to_sarif(findings)
    assert "invocations" not in doc_default["runs"][0]
