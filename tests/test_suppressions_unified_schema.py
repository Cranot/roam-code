"""W691 — both readers agree on canonical ``.roam/suppressions.json`` shape.

Two readers consume this file and previously expected MUTUALLY INCOMPATIBLE
shapes:

* ``roam.commands.finding_suppress._load_per_finding_suppressions``
  expected ``{finding_id: entry}`` dict.
* ``roam.output.sarif._load_suppressions`` expected
  ``list[{rule_id, location, ...}]``.

W691 picks the dict shape as canonical (the only writer is ``roam
suppress``, which always writes the dict) and migrates the SARIF reader
to accept it via dict-entry projection (entries that embed ``rule_id`` +
``location`` ride through).

These tests assert:

1. A single canonical-shape file feeds BOTH readers cleanly.
2. The finding_suppress reader keys by finding_id and matches on
   identity-hash.
3. The SARIF reader keys by (ruleId, location) and matches the same
   underlying suppression intent.
4. Legacy list / ``{"suppressions": [...]}`` shapes still load on the
   SARIF side (back-compat).
"""

from __future__ import annotations

import json

from roam.commands.finding_suppress import (
    _load_per_finding_suppressions,
    annotate_with_suppression,
    finding_id,
)
from roam.output.sarif import to_sarif


def _canonical_entry(rule_id: str, location: str, reason: str) -> dict:
    """Build a canonical-shape entry that embeds the SARIF projection fields."""
    return {
        "reason": reason,
        "added_at": "2026-05-14T00:00:00.000000Z",
        "source": "from-finding",
        "rule_id": rule_id,
        "location": location,
    }


def _sarif_result(rule_id: str, file: str, line: int) -> dict:
    return {
        "ruleId": rule_id,
        "level": "warning",
        "message": {"text": "demo"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": file},
                    "region": {"startLine": line},
                }
            }
        ],
    }


def test_canonical_dict_shape_feeds_finding_suppress_reader(tmp_path):
    """The dict-shaped writer round-trips through the finding_suppress reader."""
    rule_id = "algo/io-in-loop"
    location = "src/foo.py:42"
    fid = finding_id("io-in-loop", location, "MyClass.list")

    sup_dir = tmp_path / ".roam"
    sup_dir.mkdir()
    sup_path = sup_dir / "suppressions.json"
    sup_path.write_text(
        json.dumps({fid: _canonical_entry(rule_id, location, "verified manually")}),
        encoding="utf-8",
    )

    loaded = _load_per_finding_suppressions(sup_path)
    assert fid in loaded
    assert loaded[fid]["reason"] == "verified manually"
    assert loaded[fid]["rule_id"] == rule_id
    assert loaded[fid]["location"] == location

    # End-to-end annotate path — the finding gets stamped suppressed.
    finding = {
        "task_id": "io-in-loop",
        "location": location,
        "symbol_name": "MyClass.list",
    }
    out, count = annotate_with_suppression([finding], command="math", project_root=tmp_path)
    assert count == 1
    assert out[0]["suppressed"]["source"] == "suppressions.json"
    assert out[0]["suppressed"]["reason"] == "verified manually"


def test_canonical_dict_shape_feeds_sarif_reader(tmp_path, monkeypatch):
    """The same dict-shape file makes the SARIF reader stamp the result."""
    rule_id = "algo/io-in-loop"
    location = "src/foo.py:42"
    fid = finding_id("io-in-loop", location, "MyClass.list")

    monkeypatch.chdir(tmp_path)
    sup_dir = tmp_path / ".roam"
    sup_dir.mkdir()
    (sup_dir / "suppressions.json").write_text(
        json.dumps({fid: _canonical_entry(rule_id, location, "verified manually")}),
        encoding="utf-8",
    )

    doc = to_sarif(
        tool_name="roam-code",
        version="0.0.0",
        rules=[{"id": rule_id, "shortDescription": "demo"}],
        results=[_sarif_result(rule_id, "src/foo.py", 42)],
    )
    result = doc["runs"][0]["results"][0]
    assert "suppressions" in result, "SARIF reader did not pick up canonical dict shape"
    sup = result["suppressions"][0]
    assert sup["status"] == "accepted"
    assert sup["kind"] == "external"
    assert "verified manually" in sup["justification"]


def test_both_readers_agree_on_synthetic_file(tmp_path, monkeypatch):
    """Both readers, same file, semantically equivalent outputs.

    The dict reader matches by finding_id; the SARIF reader matches by
    (ruleId, location) — but they MUST agree on which intents are
    suppressed for a fully-populated canonical entry.
    """
    rule_id = "algo/io-in-loop"
    location = "src/foo.py:42"
    fid = finding_id("io-in-loop", location, "MyClass.list")

    monkeypatch.chdir(tmp_path)
    sup_dir = tmp_path / ".roam"
    sup_dir.mkdir()
    sup_path = sup_dir / "suppressions.json"
    sup_path.write_text(
        json.dumps({fid: _canonical_entry(rule_id, location, "vetted")}),
        encoding="utf-8",
    )

    # Reader 1: finding_suppress (dict reader)
    dict_loaded = _load_per_finding_suppressions(sup_path)
    assert fid in dict_loaded

    # Reader 2: sarif (now accepts canonical dict)
    doc = to_sarif(
        tool_name="roam-code",
        version="0.0.0",
        rules=[{"id": rule_id, "shortDescription": "demo"}],
        results=[_sarif_result(rule_id, "src/foo.py", 42)],
    )
    result = doc["runs"][0]["results"][0]
    assert "suppressions" in result

    # Semantic equivalence: the same reason and the same identifying
    # (rule_id, location) — surfaced via different lookup keys.
    assert dict_loaded[fid]["rule_id"] == rule_id
    assert dict_loaded[fid]["location"] == location
    assert result["suppressions"][0]["justification"] == dict_loaded[fid]["reason"]


def test_canonical_entry_without_rule_id_invisible_to_sarif(tmp_path, monkeypatch):
    """Dict entries without rule_id/location cannot bind to SARIF results.

    This is intentional — finding_id is a one-way hash, so SARIF cannot
    reverse it. The finding_suppress reader still matches the entry on
    finding_id identity. Documents the design contract.
    """
    rule_id = "algo/io-in-loop"
    location = "src/foo.py:42"
    fid = finding_id("io-in-loop", location, "MyClass.list")

    monkeypatch.chdir(tmp_path)
    sup_dir = tmp_path / ".roam"
    sup_dir.mkdir()
    # Note: NO rule_id / location stamped on the entry.
    (sup_dir / "suppressions.json").write_text(
        json.dumps({fid: {"reason": "by hash only", "added_at": "2026-05-14T00:00:00Z"}}),
        encoding="utf-8",
    )

    doc = to_sarif(
        tool_name="roam-code",
        version="0.0.0",
        rules=[{"id": rule_id, "shortDescription": "demo"}],
        results=[_sarif_result(rule_id, "src/foo.py", 42)],
    )
    # SARIF cannot resolve hash-only entries — expected behaviour.
    assert "suppressions" not in doc["runs"][0]["results"][0]

    # But finding_suppress still applies via finding_id.
    finding = {
        "task_id": "io-in-loop",
        "location": location,
        "symbol_name": "MyClass.list",
    }
    out, count = annotate_with_suppression([finding], command="math", project_root=tmp_path)
    assert count == 1
    assert out[0]["suppressed"]["reason"] == "by hash only"


def test_legacy_list_shape_still_loads_in_sarif(tmp_path, monkeypatch):
    """Back-compat: the historical SARIF list-shape file must still work."""
    monkeypatch.chdir(tmp_path)
    sup_dir = tmp_path / ".roam"
    sup_dir.mkdir()
    (sup_dir / "suppressions.json").write_text(
        json.dumps(
            [
                {
                    "rule_id": "algo/io-in-loop",
                    "location": "src/x.py:10",
                    "reason": "legacy list shape",
                }
            ]
        ),
        encoding="utf-8",
    )
    doc = to_sarif(
        tool_name="roam-code",
        version="0.0.0",
        rules=[{"id": "algo/io-in-loop", "shortDescription": "demo"}],
        results=[_sarif_result("algo/io-in-loop", "src/x.py", 10)],
    )
    result = doc["runs"][0]["results"][0]
    assert "suppressions" in result
    assert "legacy list shape" in result["suppressions"][0]["justification"]


def test_legacy_envelope_shape_still_loads_in_sarif(tmp_path, monkeypatch):
    """Back-compat: the ``{"suppressions": [...]}`` envelope shape still works."""
    monkeypatch.chdir(tmp_path)
    sup_dir = tmp_path / ".roam"
    sup_dir.mkdir()
    (sup_dir / "suppressions.json").write_text(
        json.dumps(
            {
                "suppressions": [
                    {
                        "rule_id": "algo/io-in-loop",
                        "location": "src/x.py:10",
                        "reason": "legacy envelope shape",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    doc = to_sarif(
        tool_name="roam-code",
        version="0.0.0",
        rules=[{"id": "algo/io-in-loop", "shortDescription": "demo"}],
        results=[_sarif_result("algo/io-in-loop", "src/x.py", 10)],
    )
    result = doc["runs"][0]["results"][0]
    assert "suppressions" in result
    assert "legacy envelope shape" in result["suppressions"][0]["justification"]
