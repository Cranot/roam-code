"""W1061-followup-2 — :func:`runtime_filter_disclosure` parity tests.

Asserts the shared helper at
``src/roam/output/sarif.py:runtime_filter_disclosure`` produces the same
SARIF override entries as the 4 pre-helper inline builders it replaces.
Each caller's runtime-filter shape is exercised once:

- ``cmd_smells``       → rule-level disables under ``smells/<kind>`` ids
- ``cmd_check_rules``  → rule-level disables under ``rules/<id>`` ids +
                         finding-level ``severity-filter`` notification
- ``cmd_taint``        → rule-level disables under bare rule ids +
                         finding-level ``rules-dir-filter`` notification
- ``cmd_vulns``        → finding-level ``reachable-only-filter`` only

Hash invariant: passing empty / ``None`` inputs returns ``([], [])`` so
gated emission in :func:`to_sarif` keeps SARIF bytes byte-identical to
the pre-W1061 default path.
"""

from __future__ import annotations

from roam.output.sarif import runtime_filter_disclosure


def test_empty_inputs_return_empty_lists() -> None:
    """Hash-invariant default path: no filters → ``([], [])``.

    All 4 callers gate their downstream ``to_sarif()`` call on these
    lists being non-empty so a clean (no filters applied) run stays
    byte-identical to pre-W1061 SARIF output.
    """
    overrides, notif = runtime_filter_disclosure()
    assert overrides == []
    assert notif == []

    overrides, notif = runtime_filter_disclosure(
        rule_ids_disabled=[],
        finding_level_filters=[],
    )
    assert overrides == []
    assert notif == []


def test_smells_shape_parity() -> None:
    """cmd_smells builds entries of the form
    ``{configuration: {enabled: False}, descriptor: {id: "smells/<kind>"},
       properties: {disabled_by: "--kind"|"--only", filter_value: [...]}}``.

    Mirror the previous inline builder so the W1061 hash invariant
    (filter→SARIF projection) survives the helper migration.
    """
    overrides, notif = runtime_filter_disclosure(
        rule_ids_disabled=[
            (
                "smells/god-class",
                {"disabled_by": "--kind", "filter_value": ["long-method"]},
            ),
            (
                "smells/feature-envy",
                {"disabled_by": "--kind", "filter_value": ["long-method"]},
            ),
        ],
    )
    assert notif == []
    assert len(overrides) == 2
    for entry in overrides:
        assert entry["configuration"] == {"enabled": False}
        assert entry["descriptor"]["id"].startswith("smells/")
        assert entry["properties"]["disabled_by"] == "--kind"
        assert entry["properties"]["filter_value"] == ["long-method"]
    assert {e["descriptor"]["id"] for e in overrides} == {
        "smells/god-class",
        "smells/feature-envy",
    }


def test_check_rules_combined_rule_and_severity_shape_parity() -> None:
    """cmd_check_rules drives BOTH rails: ``--rule`` disables under
    ``rules/<id>`` (rule-level), ``--severity`` adds a notification under
    the synthetic ``severity-filter`` descriptor (finding-level).
    """
    overrides, notif = runtime_filter_disclosure(
        rule_ids_disabled=[
            (
                "rules/max-fan-in",
                {"disabled_by": "--rule", "filter_value": "max-fan-out"},
            ),
        ],
        finding_level_filters=[
            (
                "severity-filter",
                {"filter": "--severity", "filter_value": "error"},
            ),
        ],
    )
    assert len(overrides) == 1
    assert overrides[0]["descriptor"]["id"] == "rules/max-fan-in"
    assert overrides[0]["configuration"] == {"enabled": False}
    assert overrides[0]["properties"]["disabled_by"] == "--rule"

    assert len(notif) == 1
    assert notif[0]["descriptor"]["id"] == "severity-filter"
    # Finding-level filters are ENABLED (the filter IS active) — distinct
    # from rule-level disables which carry ``enabled: False``.
    assert notif[0]["configuration"] == {"enabled": True}
    assert notif[0]["properties"]["filter"] == "--severity"
    assert notif[0]["properties"]["filter_value"] == "error"


def test_taint_and_vulns_finding_level_shape_parity() -> None:
    """cmd_taint surfaces ``--rules-dir`` as the synthetic
    ``rules-dir-filter`` notification descriptor; cmd_vulns surfaces
    ``--reachable-only`` as ``reachable-only-filter``. Both are
    finding-level — both carry ``configuration.enabled: True``.
    """
    # cmd_taint shape: rule-level disable + rules-dir notification.
    overrides, notif = runtime_filter_disclosure(
        rule_ids_disabled=[
            (
                "python-xss",
                {"disabled_by": "--rule", "rule_filter": "sqli"},
            ),
        ],
        finding_level_filters=[
            (
                "rules-dir-filter",
                {"filter": "--rules-dir", "filter_value": "/tmp/rules"},
            ),
        ],
    )
    assert overrides[0]["descriptor"]["id"] == "python-xss"
    assert overrides[0]["configuration"]["enabled"] is False
    assert notif[0]["descriptor"]["id"] == "rules-dir-filter"
    assert notif[0]["configuration"]["enabled"] is True

    # cmd_vulns shape: notification only — no rule-level disables.
    overrides2, notif2 = runtime_filter_disclosure(
        finding_level_filters=[
            (
                "reachable-only-filter",
                {"filter": "--reachable-only", "filter_value": True},
            ),
        ],
    )
    assert overrides2 == []
    assert len(notif2) == 1
    assert notif2[0]["descriptor"]["id"] == "reachable-only-filter"
    assert notif2[0]["configuration"]["enabled"] is True
    assert notif2[0]["properties"]["filter_value"] is True


def test_properties_dict_is_copied_not_referenced() -> None:
    """The helper must defensively copy each ``properties`` dict so a
    caller mutating its input after calling the helper does NOT leak
    into the SARIF document. Avoids a class of action-at-a-distance bugs
    when the inline-builder lists were aliased.
    """
    props = {"disabled_by": "--rule", "filter_value": "x"}
    overrides, _ = runtime_filter_disclosure(
        rule_ids_disabled=[("rule-a", props)],
    )
    props["filter_value"] = "MUTATED"
    assert overrides[0]["properties"]["filter_value"] == "x"
