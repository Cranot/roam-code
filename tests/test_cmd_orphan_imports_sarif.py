"""W1218: SARIF projection for ``roam orphan-imports`` findings.

cmd_orphan_imports detects imports that don't resolve to any indexed
module / installed package across Python / JS-family / Go. Each
finding maps onto one of three closed-enum rule ids by kind:

- ``orphan-imports/internal-typo`` (defaultLevel ``error``): Python
  orphan where the top-level package IS indexed but the full dotted
  path is not. R22 confidence = ``high`` — deterministic
  set-membership over the index; almost surely a typo / stale import.
- ``orphan-imports/missing-package`` (defaultLevel ``warning``):
  Python orphan that resolves neither in the index NOR via
  ``importlib.util.find_spec``. R22 confidence = ``medium`` —
  could be typo or uninstalled optional dependency.
- ``orphan-imports/missing-local`` (defaultLevel ``warning``):
  JS/Go orphan where a relative / path-style import did not resolve
  to an indexed file. R22 confidence = ``medium`` — possible
  build-tool resolution.

Per-finding anchor: ``file`` + ``line`` (the import statement line).
Mirrors the closed-enum design from ``test_cmd_n1_sarif.py`` (W1208)
and ``test_cmd_auth_gaps_sarif.py`` (W1195).
"""

from __future__ import annotations

from roam.output.sarif import orphan_imports_to_sarif


def test_empty_findings_produce_valid_sarif_with_zero_results() -> None:
    """An empty findings list emits a valid SARIF doc with 0 results.

    The rules array is always populated (so consumers can introspect
    the closed-enum rule catalogue even when nothing fired), but
    ``results`` is empty. Mirrors the cmd_n1 / cmd_auth_gaps
    "no findings" path.
    """
    doc = orphan_imports_to_sarif([])

    assert doc["version"] == "2.1.0"
    assert "runs" in doc and len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["results"] == []
    # The rule catalogue is always present (closed enum of 3 rules).
    rules = run["tool"]["driver"]["rules"]
    rule_ids = {r["id"] for r in rules}
    assert rule_ids == {
        "orphan-imports/internal-typo",
        "orphan-imports/missing-package",
        "orphan-imports/missing-local",
    }
    # Each rule carries its closed-enum defaultLevel — surfaced via the
    # SARIF builder onto ``defaultConfiguration.level``.
    by_id = {r["id"]: r for r in rules}
    assert by_id["orphan-imports/internal-typo"]["defaultConfiguration"]["level"] == "error"
    assert by_id["orphan-imports/missing-package"]["defaultConfiguration"]["level"] == "warning"
    assert by_id["orphan-imports/missing-local"]["defaultConfiguration"]["level"] == "warning"


def test_internal_typo_finding_maps_to_error_band() -> None:
    """An ``internal_typo`` finding projects onto
    ``orphan-imports/internal-typo`` at ``level: error``.

    Python orphan where the top-level package IS in the index but the
    full dotted submodule is not. SARIF level is ``error`` so a CI
    gate keyed off ``level: error`` blocks the change — the
    deterministic-set-membership signal is strong (almost surely a
    typo or stale import).
    """
    findings = [
        {
            "language": "python",
            "file": "src/roam/commands/cmd_health.py",
            "line": 12,
            "module": "roam.cmds.foo",
            "kind": "internal_typo",
            "hint": "top-level package 'roam' is indexed but 'roam.cmds.foo' is not",
        }
    ]

    doc = orphan_imports_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "orphan-imports/internal-typo"
    assert r["level"] == "error"

    # Anchor is file + line.
    phys = r["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "src/roam/commands/cmd_health.py"
    assert phys["region"]["startLine"] == 12

    # Message body surfaces language + module + hint.
    text = r["message"]["text"]
    assert "python" in text
    assert "roam.cmds.foo" in text
    assert "top-level package 'roam' is indexed" in text


def test_missing_package_and_missing_local_scale_severity_bands() -> None:
    """missing_package -> ``warning``, missing_local -> ``warning``.

    Verifies the closed-enum band mapping in
    :func:`_orphan_imports_kind_level`: each kind projects onto a
    distinct rule id, but the two medium-confidence kinds share the
    ``warning`` band so a CI gate keyed off SARIF ``level: error``
    only blocks on the high-confidence ``internal_typo`` kind.
    """
    findings = [
        {
            "language": "python",
            "file": "src/roam/foo.py",
            "line": 5,
            "module": "totally_made_up_pkg",
            "kind": "missing_package",
            "hint": "neither indexed nor importable; check spelling or install package",
        },
        {
            "language": "javascript",
            "file": "src/web/foo.ts",
            "line": 8,
            "module": "./missing/path",
            "kind": "missing_local",
            "hint": "resolved path 'src/web/missing/path' not in indexed JS/TS files",
        },
    ]

    doc = orphan_imports_to_sarif(findings)
    results = doc["runs"][0]["results"]
    assert len(results) == 2

    by_rule = {r["ruleId"]: r for r in results}
    assert "orphan-imports/missing-package" in by_rule
    assert "orphan-imports/missing-local" in by_rule

    assert by_rule["orphan-imports/missing-package"]["level"] == "warning"
    assert by_rule["orphan-imports/missing-local"]["level"] == "warning"

    # No error band is emitted (no internal_typo findings).
    levels = {r["level"] for r in results}
    assert "error" not in levels

    # Anchors land on file:line for each finding.
    by_path = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]: r for r in results}
    assert by_path["src/roam/foo.py"]["locations"][0]["physicalLocation"]["region"]["startLine"] == 5
    assert by_path["src/web/foo.ts"]["locations"][0]["physicalLocation"]["region"]["startLine"] == 8


def test_malformed_entries_are_skipped_without_crash() -> None:
    """Non-dict entries / empty file anchor / unknown kind are skipped
    silently (no crash).

    Defensive parsing per Pattern 1 family discipline — the SARIF
    emitter must not crash on a malformed entry, since the producer
    envelope can carry partial data when the underlying scan hits an
    exception.
    """
    findings = [
        "not a dict",  # skipped
        {
            "language": "python",
            "file": "",  # empty anchor — skipped
            "line": 1,
            "module": "foo",
            "kind": "missing_package",
        },
        {
            "language": "python",
            "file": "foo.py",
            "line": 1,
            "module": "foo",
            "kind": "bogus_future_kind",  # unknown — skipped (closed enum)
        },
        {
            "language": "go",
            "file": "ok.go",
            "line": 3,
            "module": "example.local/missing",
            "kind": "missing_local",
            "hint": "Go import path not in indexed packages",
        },  # kept
    ]

    doc = orphan_imports_to_sarif(findings)
    results = doc["runs"][0]["results"]
    # Only the well-formed missing_local entry survived.
    assert len(results) == 1
    assert results[0]["ruleId"] == "orphan-imports/missing-local"
    assert results[0]["level"] == "warning"
    phys = results[0]["locations"][0]["physicalLocation"]
    assert phys["artifactLocation"]["uri"] == "ok.go"
    assert phys["region"]["startLine"] == 3
