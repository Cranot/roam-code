"""Tests for the R26 pr-bundle + R28 world-model integration (W12.x).

When the agent calls ``pr-bundle add affected <symbol>``, the bundle should
automatically:

  1. Classify ``<symbol>``'s side-effects + idempotency via the R28
     detectors and stamp the affected_symbol record with
     ``side_effect_kinds`` / ``idempotency_kind`` / ``world_model_confidence``.
  2. When the classification is non-trivial (io_write / mutation / process
     / non_idempotent), append a derived risk to ``bundle["risks"]`` with
     id ``side_effect_<symbol>`` and severity per the matrix:

       - H : io_write AND non_idempotent
       - M : io_write XOR non_idempotent, OR process
       - L : io_read only, OR mutation only
       - skip: only ``none`` / ``unknown``

  3. On ``emit --auto-collect``, classify any legacy affected_symbol that
     doesn't yet carry world-model fields.
  4. Surface ``side_effect_distribution``, ``idempotency_distribution``,
     ``risk_severity_distribution`` in ``summary``, and mention the
     io_write count in the verdict.
  5. Skip the auto-risk-add when the user already manually recorded a
     risk for the same symbol (id-based dedup OR description-prefix
     dedup).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, parse_json_output  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: project_factory creates an INDEXED git repo; we tack on the
# branch pinning the pr-bundle tests need.
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _pin_branch(proj: Path) -> None:
    """Pin branch to a stable name so bundle filename is deterministic."""
    subprocess.run(
        ["git", "checkout", "-B", "wm-branch"],
        cwd=proj,
        capture_output=True,
    )


def _invoke(cli_runner, args, **kw):
    from roam.cli import cli

    return cli_runner.invoke(cli, args, catch_exceptions=False, **kw)


def _read_bundle_file(proj: Path, branch: str = "wm-branch") -> dict:
    safe = branch.replace("/", "__")
    path = proj / ".roam" / "pr-bundles" / f"{safe}.json"
    if not path.exists():
        path = proj / ".roam" / "pr-bundle.json"
    assert path.exists(), f"bundle file missing -- looked at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Add-affected classifies side-effects on a write-to-file symbol
# ---------------------------------------------------------------------------


def test_add_affected_classifies_side_effects(
    project_factory, cli_runner, monkeypatch
):
    """`add affected <writer>` stamps side_effect_kinds=['io_write']."""
    proj = project_factory(
        {
            "src/writer.py": (
                "def dump_state(path, content):\n"
                "    with open(path, 'w') as f:\n"
                "        f.write(content)\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "wire writer"])
    r = _invoke(cli_runner, ["pr-bundle", "add", "affected", "dump_state"])
    assert r.exit_code == 0, r.output

    bundle = _read_bundle_file(proj)
    rec = next(s for s in bundle["affected_symbols"] if s["name"] == "dump_state")
    assert "io_write" in rec.get("side_effect_kinds", []), rec
    assert rec.get("idempotency_kind") in ("non_idempotent", "idempotent"), rec
    assert rec.get("world_model_confidence") in ("high", "medium", "low"), rec


# ---------------------------------------------------------------------------
# 2. Pure function -> classifier reports 'none' AND no auto-risk added
# ---------------------------------------------------------------------------


def test_add_affected_pure_function_no_auto_risk(
    project_factory, cli_runner, monkeypatch
):
    """`add affected <pure>` annotates kinds=['none'] and does NOT add a risk."""
    proj = project_factory(
        {
            "src/pure.py": (
                "def add(a, b):\n"
                "    return a + b\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "wire pure"])
    r = _invoke(cli_runner, ["pr-bundle", "add", "affected", "add"])
    assert r.exit_code == 0, r.output

    bundle = _read_bundle_file(proj)
    rec = next(s for s in bundle["affected_symbols"] if s["name"] == "add")
    # Pure function -> 'none' (or empty when classifier silent-fallback).
    kinds = rec.get("side_effect_kinds", [])
    assert "io_write" not in kinds, kinds
    assert "mutation" not in kinds, kinds
    assert "process" not in kinds, kinds
    # No risk should reference this symbol.
    risk_ids = [r.get("id", "") for r in bundle["risks"]]
    assert f"side_effect_add" not in risk_ids, bundle["risks"]


# ---------------------------------------------------------------------------
# 3. io_write only -> M severity risk
# ---------------------------------------------------------------------------


def test_add_affected_io_write_creates_risk(
    project_factory, cli_runner, monkeypatch
):
    """`open(path, 'w')` is io_write; classifier surfaces a risk.

    Severity is M (io_write XOR non_idempotent) OR H (io_write AND
    non_idempotent — the classifier may flag a naive overwrite as
    non_idempotent, which is correct). Accept either.
    """
    proj = project_factory(
        {
            "src/io.py": (
                "def write_file(p, c):\n"
                "    with open(p, 'w') as f:\n"
                "        f.write(c)\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "wire write"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "write_file"])

    bundle = _read_bundle_file(proj)
    auto_risks = [r for r in bundle["risks"] if r.get("id") == "side_effect_write_file"]
    assert len(auto_risks) == 1, f"expected 1 auto-risk, got {bundle['risks']}"
    risk = auto_risks[0]
    assert risk["severity"] in ("M", "H"), risk
    assert risk["source_command"] == "auto:world-model"
    assert "write_file" in risk["description"]
    assert "io_write" in risk["description"]


# ---------------------------------------------------------------------------
# 4. io_write + non_idempotent (naive .write()) -> H severity
# ---------------------------------------------------------------------------


def test_add_affected_io_write_non_idempotent_creates_high_severity_risk(
    project_factory, cli_runner, monkeypatch
):
    """Naive write with no check-first pattern -> H severity.

    The R28 idempotency detector classifies naive `open(path, 'w')`
    without an `exist_ok=` / `IF NOT EXISTS` guard as non_idempotent.
    Combined with io_write that should surface as severity H.
    """
    proj = project_factory(
        {
            "src/naive.py": (
                "def append_log(line):\n"
                "    with open('out.log', 'a') as f:\n"
                "        f.write(line)\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "wire append"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "append_log"])

    bundle = _read_bundle_file(proj)
    rec = next(s for s in bundle["affected_symbols"] if s["name"] == "append_log")
    # The append-mode open is io_write AND non_idempotent.
    assert "io_write" in rec.get("side_effect_kinds", []), rec
    assert rec.get("idempotency_kind") == "non_idempotent", rec
    auto_risks = [
        r for r in bundle["risks"] if r.get("id") == "side_effect_append_log"
    ]
    assert len(auto_risks) == 1, bundle["risks"]
    assert auto_risks[0]["severity"] == "H", auto_risks[0]


# ---------------------------------------------------------------------------
# 5. emit --auto-collect classifies legacy entries (no side_effect_kinds yet)
# ---------------------------------------------------------------------------


def test_emit_auto_collect_runs_classifier_on_legacy_entries(
    project_factory, cli_runner, monkeypatch
):
    """A bundle written before this wiring shipped has no world-model fields.

    On `emit --auto-collect`, those entries get classified retroactively.
    """
    proj = project_factory(
        {
            "src/legacy.py": (
                "def save_record(path, content):\n"
                "    with open(path, 'w') as f:\n"
                "        f.write(content)\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "wire legacy"])

    # Simulate a "legacy" affected_symbol entry by writing the bundle
    # directly with NO world-model fields populated.
    bundle_path = proj / ".roam" / "pr-bundles" / "wm-branch.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["affected_symbols"].append(
        {
            "name": "save_record",
            "kind": "function",
            "file": "src/legacy.py",
            "blast_radius": 0,
        }
    )
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

    # Now run emit --auto-collect — the integration should classify the
    # legacy entry.
    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit"])
    assert r.exit_code == 0, r.output

    bundle = _read_bundle_file(proj)
    rec = next(
        s for s in bundle["affected_symbols"] if s["name"] == "save_record"
    )
    # The legacy entry should now carry world-model fields.
    assert "side_effect_kinds" in rec, rec
    assert "io_write" in rec["side_effect_kinds"], rec
    assert rec.get("world_model_confidence") in ("high", "medium", "low"), rec


# ---------------------------------------------------------------------------
# 6. Dedup: manual `add risk` for symbol X prevents auto-add for same symbol
# ---------------------------------------------------------------------------


def test_dedup_prevents_double_risk(project_factory, cli_runner, monkeypatch):
    """Manually-added risk pre-empts the auto-add.

    The dedup matches either the canonical id ``side_effect_<sym>`` OR a
    description that starts with the symbol name. We test the
    description-prefix case here because that's what an agent typically
    types.
    """
    proj = project_factory(
        {
            "src/io.py": (
                "def write_file(p, c):\n"
                "    with open(p, 'w') as f:\n"
                "        f.write(c)\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "wire write"])
    # First the user manually records a risk for write_file.
    _invoke(
        cli_runner,
        [
            "pr-bundle",
            "add",
            "risk",
            "write_file may double-write on retry",
            "--severity",
            "H",
        ],
    )
    # Then the agent adds it as an affected symbol — auto-classifier
    # should NOT double-add.
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "write_file"])

    bundle = _read_bundle_file(proj)
    # Exactly one risk that mentions write_file.
    write_risks = [
        r for r in bundle["risks"]
        if "write_file" in (r.get("description") or "")
        or r.get("id") == "side_effect_write_file"
    ]
    assert len(write_risks) == 1, write_risks


def test_dedup_by_id_prevents_double_risk(project_factory, cli_runner, monkeypatch):
    """The id-based dedup path: same symbol added twice -> classifier runs
    on second add but doesn't double-add the risk."""
    proj = project_factory(
        {
            "src/io.py": (
                "def write_file(p, c):\n"
                "    with open(p, 'w') as f:\n"
                "        f.write(c)\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "wire write"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "write_file"])
    # Calling add affected a second time triggers the classifier again --
    # it should not append a duplicate risk.
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "write_file"])

    bundle = _read_bundle_file(proj)
    auto_risks = [
        r for r in bundle["risks"] if r.get("id") == "side_effect_write_file"
    ]
    assert len(auto_risks) == 1, auto_risks


# ---------------------------------------------------------------------------
# 7. Envelope summary includes the world-model distributions
# ---------------------------------------------------------------------------


def test_envelope_includes_side_effect_distribution(
    project_factory, cli_runner, monkeypatch
):
    """Emit envelope surfaces side_effect_distribution / idempotency / severity."""
    proj = project_factory(
        {
            "src/io.py": (
                "def write_file(p, c):\n"
                "    with open(p, 'w') as f:\n"
                "        f.write(c)\n"
                "\n"
                "def read_file(p):\n"
                "    with open(p) as f:\n"
                "        return f.read()\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "wire io"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "write_file"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "read_file"])

    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    data = parse_json_output(r, command="pr-bundle")
    summary = data["summary"]
    assert "side_effect_distribution" in summary, summary
    assert "idempotency_distribution" in summary, summary
    assert "risk_severity_distribution" in summary, summary
    # The write/read fixture is unambiguous: io_write + io_read appear.
    se_dist = summary["side_effect_distribution"]
    assert se_dist.get("io_write", 0) >= 1, se_dist
    assert se_dist.get("io_read", 0) >= 1, se_dist


# ---------------------------------------------------------------------------
# 8. Verdict mentions the io_write count
# ---------------------------------------------------------------------------


def test_envelope_verdict_mentions_io_write_count(
    project_factory, cli_runner, monkeypatch
):
    """When io_write is auto-flagged, the verdict says so (LAW 6).

    A verdict line that works without the rest of the envelope is the
    point of LAW 6 — agents that only consume the verdict still see
    the headline finding.
    """
    proj = project_factory(
        {
            "src/io.py": (
                "def write_file(p, c):\n"
                "    with open(p, 'w') as f:\n"
                "        f.write(c)\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "wire write"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "write_file", "--blast-radius", "3"])
    _invoke(cli_runner, ["pr-bundle", "add", "context-cmd", "roam preflight write_file"])
    _invoke(cli_runner, ["pr-bundle", "add", "test-required", "tests/test_write.py"])
    _invoke(cli_runner, ["pr-bundle", "add", "test-run", "tests/test_write.py", "--passed"])

    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    data = parse_json_output(r, command="pr-bundle")
    verdict = data["summary"]["verdict"]
    # The classifier should have auto-flagged write_file -> verdict
    # mentions an io_write count.
    assert "io_write" in verdict, verdict
    assert "auto-flagged" in verdict, verdict


# ---------------------------------------------------------------------------
# 9. Missing symbol (not in index) -> silent fallback, never fails
# ---------------------------------------------------------------------------


def test_add_affected_unknown_symbol_does_not_fail(
    project_factory, cli_runner, monkeypatch
):
    """Adding a symbol the indexer doesn't know about must not crash.

    The classifier silent-fallback stamps confidence='unknown' and skips
    the risk-add. The command exits 0.
    """
    proj = project_factory(
        {
            "src/main.py": "def hello():\n    return 'hi'\n",
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "wire ghost"])
    r = _invoke(
        cli_runner, ["pr-bundle", "add", "affected", "nonexistentGhostSymbol"]
    )
    assert r.exit_code == 0, r.output
    bundle = _read_bundle_file(proj)
    rec = next(
        s for s in bundle["affected_symbols"]
        if s["name"] == "nonexistentGhostSymbol"
    )
    # Either the field is absent (fallback path didn't stamp) or it's
    # explicitly 'unknown'.
    confidence = rec.get("world_model_confidence", "unknown")
    assert confidence == "unknown", rec
    # No auto-risk should reference the ghost symbol.
    risk_ids = [r.get("id", "") for r in bundle["risks"]]
    assert "side_effect_nonexistentGhostSymbol" not in risk_ids, bundle["risks"]
