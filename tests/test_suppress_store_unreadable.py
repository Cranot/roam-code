"""``roam suppress`` must fail closed on an UNKNOWN suppression store.

The finding-id-keyed store in ``.roam/suppressions.json`` is an audit ledger:
the evidence that a finding was reviewed and accepted, with rationale
("vetted by security"), ticket refs, and timestamps. ``cmd_suppress`` loaded
it with ``except (OSError, JSONDecodeError): current = {}``, so an UNREADABLE
or CORRUPT store was indistinguishable from an EMPTY one. Two measured harms:

1. ``roam suppress _ --list`` printed ``VERDICT: 0 suppression(s)`` at exit 0
   over a file that visibly held two entries — a terminal verdict read by any
   human or CI job auditing "what is currently suppressed and why".
2. Worse, ``current`` is written straight back. Adding ONE suppression over a
   corrupt store REWROTE the file with only that entry, destroying every prior
   suppression, its rationale and its timestamps — while reporting success.
   ``--remove`` was sharpest of all: it printed ``no-op: <id> not found``,
   literally claiming nothing changed, while truncating the store to ``{}``.

This is the same data-loss shape ``suppression.save_suppression`` already
learned to refuse (see its append-only comment + test_suppression_append_only).
These tests pin the fix: absent resolves to EMPTY, unreadable resolves to
UNKNOWN and fails closed with the bytes on disk left intact.

The healthy-path tests are load-bearing NEGATIVE CONTROLS: a "fix" that simply
refuses or blocks every invocation must fail this module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.cli import cli

# Two entries plus a trailing comma and no closing brace — a plausible
# truncated/interrupted write, the shape that reaches JSONDecodeError.
CORRUPT_STORE = '{\n "aaaaaaaaaaaaaaaa": {"reason":"vetted by security"},\n "bbbbbbbbbbbbbbbb": {"reason":"SEC-441"},\n'

HEALTHY_STORE = {
    "aaaaaaaaaaaaaaaa": {"reason": "vetted by security"},
    "bbbbbbbbbbbbbbbb": {"reason": "SEC-441"},
}


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return text


def _run(*args: str) -> object:
    return CliRunner().invoke(cli, list(args))


# ---------------------------------------------------------------------------
# Fail-closed: an unreadable store is UNKNOWN, never 0, and never overwritten
# ---------------------------------------------------------------------------


def test_list_over_corrupt_store_does_not_publish_zero(tmp_path: Path) -> None:
    """--list must not report a clean count it could not compute."""
    store = tmp_path / "sup.json"
    _write(store, CORRUPT_STORE)

    result = _run("suppress", "_", "--list", "--input", str(store))

    assert result.exit_code != 0, "unreadable store must not exit clean"
    assert "0 suppression(s)" not in result.output
    assert "UNKNOWN" in result.output


def test_list_over_corrupt_store_json_is_error_with_null_count(tmp_path: Path) -> None:
    """JSON consumers get isError + a null count, not a confident zero."""
    store = tmp_path / "sup.json"
    _write(store, CORRUPT_STORE)

    result = _run("--json", "suppress", "_", "--list", "--input", str(store))

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["isError"] is True
    assert payload["error_code"] == "SUPPRESSION_STORE_UNREADABLE"
    assert payload["summary"]["count"] is None, "count must be UNKNOWN, not 0"
    assert payload["summary"]["partial_success"] is False


def test_add_over_corrupt_store_preserves_bytes(tmp_path: Path) -> None:
    """The destructive case: adding must not rewrite the store from an empty view."""
    store = tmp_path / "sup.json"
    original = _write(store, CORRUPT_STORE)

    result = _run("suppress", "cccccccccccccccc", "--reason", "new", "--input", str(store))

    assert result.exit_code != 0, "must not report success over an unreadable store"
    assert store.read_text(encoding="utf-8") == original, "prior audit entries were destroyed"
    # The rationale a human wrote must still be on disk.
    assert "vetted by security" in store.read_text(encoding="utf-8")
    assert "SEC-441" in store.read_text(encoding="utf-8")


def test_remove_over_corrupt_store_preserves_bytes(tmp_path: Path) -> None:
    """--remove reported 'no-op' while truncating the store to {}."""
    store = tmp_path / "sup.json"
    original = _write(store, CORRUPT_STORE)

    result = _run("suppress", "zzzzzzzzzzzzzzzz", "--remove", "--input", str(store))

    assert result.exit_code != 0
    assert "no-op" not in result.output, "claimed nothing changed while rewriting the file"
    assert store.read_text(encoding="utf-8") == original


def test_from_finding_over_corrupt_store_preserves_bytes(tmp_path: Path) -> None:
    """The batch-ingest path writes the same `current` dict and must also abort."""
    store = tmp_path / "sup.json"
    original = _write(store, CORRUPT_STORE)
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps({"findings": [{"finding_id": "dddddddddddddddd"}]}), encoding="utf-8")

    result = _run("suppress", "_", "--from-finding", str(findings), "--reason", "batch", "--input", str(store))

    assert result.exit_code != 0
    assert store.read_text(encoding="utf-8") == original


def test_non_dict_root_is_unknown_not_empty(tmp_path: Path) -> None:
    """A JSON array root raised no exception at all — it fell to `current = {}`."""
    store = tmp_path / "sup.json"
    original = _write(store, '["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"]\n')

    result = _run("suppress", "cccccccccccccccc", "--reason", "new", "--input", str(store))

    assert result.exit_code != 0
    assert store.read_text(encoding="utf-8") == original


def test_binary_store_is_unknown_not_a_traceback(tmp_path: Path) -> None:
    """Invalid UTF-8 raises UnicodeDecodeError (a ValueError) — not caught by OSError."""
    store = tmp_path / "sup.json"
    original = b"\xff\xfe\x00\x01 not utf-8 \xc3\x28"
    store.write_bytes(original)

    result = _run("suppress", "cccccccccccccccc", "--reason", "new", "--input", str(store))

    assert result.exit_code != 0
    assert store.read_bytes() == original
    assert "UNKNOWN" in result.output, "should be an actionable verdict, not a raw traceback"


def test_abort_message_is_actionable(tmp_path: Path) -> None:
    """A fail-closed verdict is only useful if it names the path and the remedy."""
    store = tmp_path / "sup.json"
    _write(store, CORRUPT_STORE)

    result = _run("suppress", "_", "--list", "--input", str(store))

    assert str(store) in result.output
    assert "Nothing was modified." in result.output


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS — a fix that blocks everything must fail these
# ---------------------------------------------------------------------------


def test_absent_store_is_legitimately_empty(tmp_path: Path) -> None:
    """Absent is KNOWN-empty, not UNKNOWN: this must stay a clean exit-0 zero."""
    store = tmp_path / "does_not_exist.json"

    result = _run("suppress", "_", "--list", "--input", str(store))

    assert result.exit_code == 0, result.output
    assert "0 suppression(s)" in result.output


def test_healthy_store_lists_every_entry(tmp_path: Path) -> None:
    store = tmp_path / "sup.json"
    _write(store, json.dumps(HEALTHY_STORE))

    result = _run("suppress", "_", "--list", "--input", str(store))

    assert result.exit_code == 0, result.output
    assert "2 suppression(s)" in result.output
    assert "vetted by security" in result.output


def test_healthy_store_add_preserves_prior_entries(tmp_path: Path) -> None:
    store = tmp_path / "sup.json"
    _write(store, json.dumps(HEALTHY_STORE))

    result = _run("suppress", "cccccccccccccccc", "--reason", "new", "--input", str(store))

    assert result.exit_code == 0, result.output
    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert set(on_disk) == {"aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "cccccccccccccccc"}
    assert on_disk["aaaaaaaaaaaaaaaa"]["reason"] == "vetted by security"


def test_healthy_store_remove_still_works(tmp_path: Path) -> None:
    store = tmp_path / "sup.json"
    _write(store, json.dumps(HEALTHY_STORE))

    result = _run("suppress", "aaaaaaaaaaaaaaaa", "--remove", "--input", str(store))

    assert result.exit_code == 0, result.output
    assert set(json.loads(store.read_text(encoding="utf-8"))) == {"bbbbbbbbbbbbbbbb"}


def test_empty_json_object_store_is_known_empty(tmp_path: Path) -> None:
    """`{}` is a valid, KNOWN-empty store — it must not trip the guard."""
    store = tmp_path / "sup.json"
    _write(store, "{}")

    result = _run("suppress", "_", "--list", "--input", str(store))

    assert result.exit_code == 0, result.output
    assert "0 suppression(s)" in result.output


@pytest.mark.parametrize("json_mode", [False, True])
def test_healthy_add_reports_success_in_both_modes(tmp_path: Path, json_mode: bool) -> None:
    """Guard must not leak into the happy path in either output mode."""
    store = tmp_path / "sup.json"
    _write(store, json.dumps(HEALTHY_STORE))
    args = ["--json"] if json_mode else []

    result = _run(*args, "suppress", "cccccccccccccccc", "--reason", "ok", "--input", str(store))

    assert result.exit_code == 0, result.output
    if json_mode:
        payload = json.loads(result.output)
        assert payload.get("isError") is not True
        assert payload["summary"]["verdict"] == "suppressed cccccccccccccccc"
    else:
        assert "VERDICT: suppressed cccccccccccccccc" in result.output
