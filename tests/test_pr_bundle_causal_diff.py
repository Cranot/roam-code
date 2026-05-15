"""Tests for the W15.3 pr-bundle ↔ causal-graph diff integration.

The W12.1 wiring already classifies side-effects + idempotency on
``add affected``. The W15.3 sub-feature 3 detector additionally builds a
**causal graph** — which params / globals / env reads flow into which
side-effects, returns, raises, mutations — and that graph is interesting
to pr-bundle in a DIFF-AWARE way: snapshot the graph at ``add affected``,
diff against a fresh graph at ``emit``, and surface NEW or REMOVED
``param_to_effect → io_write:*`` edges as bundle risks.

These tests pin down:

1. ``add affected <sym>`` stamps a ``causal_snapshot`` on the affected
   record (with edges + snapshot_at + state).
2. ``emit`` reports ``causal_diff_added`` when the symbol body changed
   between add-time and emit-time and a new io_write edge appeared.
3. ``emit`` reports ``causal_diff_removed`` symmetrically.
4. The dedup helper suppresses causal-diff risks for symbols that
   already carry a ``side_effect_<sym>`` risk (no double-flagging).
5. A pure function (no side-effects) never produces causal-diff risks.
6. The emit envelope summary exposes ``causal_diff_distribution`` and
   ``causal_diff_high_severity_count`` and the verdict mentions any
   io_write path that changed since init (LAW 6).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import index_in_process, parse_json_output  # noqa: E402


@pytest.fixture
def cli_runner():
    return CliRunner()


def _pin_branch(proj: Path) -> None:
    """Pin branch name so the bundle path is deterministic."""
    subprocess.run(
        ["git", "checkout", "-B", "cd-branch"],
        cwd=proj,
        capture_output=True,
    )


def _invoke(cli_runner, args, **kw):
    from roam.cli import cli

    return cli_runner.invoke(cli, args, catch_exceptions=False, **kw)


def _read_bundle_file(proj: Path, branch: str = "cd-branch") -> dict:
    safe = branch.replace("/", "__")
    path = proj / ".roam" / "pr-bundles" / f"{safe}.json"
    if not path.exists():
        path = proj / ".roam" / "pr-bundle.json"
    assert path.exists(), f"bundle file missing -- looked at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _rewrite_and_reindex(proj: Path, rel_path: str, new_content: str) -> None:
    """Replace a file in the project and re-run the indexer.

    Re-index in-process so the new content is reflected in the DB when
    the emit-time causal-graph re-snapshot runs.
    """
    (proj / rel_path).write_text(new_content, encoding="utf-8")
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"reindex failed:\n{out}"


# ---------------------------------------------------------------------------
# 1. add affected stores a causal_snapshot
# ---------------------------------------------------------------------------


def test_add_affected_stores_causal_snapshot(
    project_factory, cli_runner, monkeypatch
):
    """``add affected <writer>`` records a non-empty causal snapshot.

    The fixture is a single function with a clear param->io_write edge so
    the classifier yields at least one edge. The snapshot must include
    ``edges``, ``snapshot_at``, and ``state == 'captured'``.
    """
    proj = project_factory(
        {
            "src/writer.py": (
                "from pathlib import Path\n"
                "\n"
                "def dump_state(path, content):\n"
                "    Path(path).write_text(content)\n"
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
    snap = rec.get("causal_snapshot")
    assert isinstance(snap, dict), rec
    assert snap.get("state") == "captured", snap
    assert "snapshot_at" in snap, snap
    edges = snap.get("edges") or []
    assert isinstance(edges, list)
    # At least one param->effect edge for dump_state's params.
    p2e = [
        e
        for e in edges
        if e.get("kind") == "param_to_effect"
        and e.get("source", "").startswith("param:")
    ]
    assert p2e, f"expected at least one param_to_effect edge in snapshot, got {edges}"


# ---------------------------------------------------------------------------
# 2. emit detects NEW causal edges introduced after the snapshot
# ---------------------------------------------------------------------------


def test_emit_detects_new_causal_edges(
    project_factory, cli_runner, monkeypatch
):
    """When code grows a new io_write edge between add-time and emit-time,
    emit reports it in ``causal_diff_added`` and adds a risk.

    Fixture: a single-param function with no side effects (the indexer
    won't snapshot any io_write edges). We then rewrite the file to
    introduce ``open(path, 'w').write(...)`` — which is a new
    ``param:path -> io_write:open`` edge — and re-index. Emit should
    flag the new edge.
    """
    proj = project_factory(
        {
            "src/grow.py": (
                "from pathlib import Path\n"
                "\n"
                "def maybe_save(path):\n"
                "    return path\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "grow writer"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "maybe_save"])

    bundle = _read_bundle_file(proj)
    rec = next(s for s in bundle["affected_symbols"] if s["name"] == "maybe_save")
    snap_edges = rec.get("causal_snapshot", {}).get("edges") or []
    # Sanity: the pure return-only fixture has no io_write edges yet.
    assert not any(
        "io_write" in (e.get("sink") or "") for e in snap_edges
    ), f"unexpected io_write in initial snapshot: {snap_edges}"

    # Now rewrite the file to INTRODUCE an io_write path.
    _rewrite_and_reindex(
        proj,
        "src/grow.py",
        "from pathlib import Path\n"
        "\n"
        "def maybe_save(path):\n"
        "    Path(path).write_text('payload')\n"
        "    return path\n",
    )

    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    data = parse_json_output(r, command="pr-bundle")

    # The summary surfaces a non-zero added_total + high_severity_count.
    cd_dist = data["summary"]["causal_diff_distribution"]
    assert cd_dist["added_total"] >= 1, cd_dist
    assert data["summary"]["causal_diff_high_severity_count"] >= 1, data["summary"]

    # The bundle record stamps causal_diff_added on maybe_save.
    bundle2 = _read_bundle_file(proj)
    rec2 = next(
        s for s in bundle2["affected_symbols"] if s["name"] == "maybe_save"
    )
    added = rec2.get("causal_diff_added") or []
    assert any(
        "io_write" in (e.get("sink") or "")
        for e in added
    ), f"expected an added io_write edge, got {added}"
    assert rec2.get("causal_diff_state") == "computed", rec2

    # A risk was appended with the auto:causal-diff source.
    auto_risks = [
        r for r in bundle2["risks"]
        if r.get("source_command") == "auto:causal-diff"
    ]
    assert auto_risks, f"expected an auto:causal-diff risk, got {bundle2['risks']}"
    assert any(
        "maybe_save" in (r.get("description") or "")
        and "io_write" in (r.get("description") or "")
        for r in auto_risks
    ), auto_risks


# ---------------------------------------------------------------------------
# 3. emit detects REMOVED causal edges
# ---------------------------------------------------------------------------


def test_emit_detects_removed_causal_edges(
    project_factory, cli_runner, monkeypatch
):
    """Inverse of (2): an io_write that existed at add-time is gone at
    emit-time. The diff records it on ``causal_diff_removed`` and adds a
    risk with REMOVED in the description.
    """
    proj = project_factory(
        {
            "src/shrink.py": (
                "from pathlib import Path\n"
                "\n"
                "def maybe_save(path):\n"
                "    Path(path).write_text('payload')\n"
                "    return path\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "shrink writer"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "maybe_save"])

    bundle = _read_bundle_file(proj)
    rec = next(s for s in bundle["affected_symbols"] if s["name"] == "maybe_save")
    snap_edges = rec.get("causal_snapshot", {}).get("edges") or []
    # Sanity: at least one io_write edge present at add-time.
    assert any(
        "io_write" in (e.get("sink") or "") for e in snap_edges
    ), f"expected initial io_write edge, got {snap_edges}"

    # Now strip the write away — keep the function but make it a no-op.
    _rewrite_and_reindex(
        proj,
        "src/shrink.py",
        "from pathlib import Path\n"
        "\n"
        "def maybe_save(path):\n"
        "    return path\n",
    )

    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    data = parse_json_output(r, command="pr-bundle")
    cd_dist = data["summary"]["causal_diff_distribution"]
    assert cd_dist["removed_total"] >= 1, cd_dist

    bundle2 = _read_bundle_file(proj)
    rec2 = next(
        s for s in bundle2["affected_symbols"] if s["name"] == "maybe_save"
    )
    removed = rec2.get("causal_diff_removed") or []
    assert any(
        "io_write" in (e.get("sink") or "") for e in removed
    ), f"expected a removed io_write edge, got {removed}"

    # The auto-risk description contains REMOVED.
    auto_risks = [
        r for r in bundle2["risks"]
        if r.get("source_command") == "auto:causal-diff"
    ]
    assert any(
        "REMOVED" in (r.get("description") or "") for r in auto_risks
    ), auto_risks


# ---------------------------------------------------------------------------
# 4. dedup: existing side_effect_<sym> risk suppresses per-edge causal-diff risks
# ---------------------------------------------------------------------------


def test_dedup_prevents_causal_diff_double_risk(
    project_factory, cli_runner, monkeypatch
):
    """When a ``side_effect_<sym>`` risk is already present, causal-diff
    must NOT append a redundant ``causal_diff_added_<sym>_...`` risk for
    the same symbol.

    We arrange this by:
      1. ``add affected`` on a writer → W12.1 wires ``side_effect_<sym>``.
      2. Rewrite the file to introduce an EXTRA io_write path.
      3. ``emit`` — the W12.1 risk still pre-exists, so causal-diff
         records the diff on the record but skips the per-edge risk.
    """
    proj = project_factory(
        {
            "src/dup.py": (
                "from pathlib import Path\n"
                "\n"
                "def writer(path):\n"
                "    Path(path).write_text('a')\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "dedup test"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "writer"])
    bundle = _read_bundle_file(proj)
    se_risks = [
        r for r in bundle["risks"] if r.get("id") == "side_effect_writer"
    ]
    assert len(se_risks) == 1, bundle["risks"]

    # Introduce an additional io_write path.
    _rewrite_and_reindex(
        proj,
        "src/dup.py",
        "from pathlib import Path\n"
        "\n"
        "def writer(path):\n"
        "    Path(path).write_text('a')\n"
        "    Path('extra.log').write_text(path)\n",
    )

    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    assert r.exit_code == 0, r.output

    bundle2 = _read_bundle_file(proj)
    # The diff is still recorded on the affected record (telemetry).
    rec2 = next(s for s in bundle2["affected_symbols"] if s["name"] == "writer")
    assert rec2.get("causal_diff_state") == "computed", rec2
    # But NO causal_diff_added risk was appended for writer.
    cd_risks = [
        r for r in bundle2["risks"]
        if r.get("source_command") == "auto:causal-diff"
        and "writer" in (r.get("description") or "")
    ]
    assert cd_risks == [], (
        f"expected dedup to suppress per-edge causal-diff risks; "
        f"got {cd_risks}"
    )
    # The original side_effect_writer risk is still there exactly once.
    se_risks2 = [
        r for r in bundle2["risks"] if r.get("id") == "side_effect_writer"
    ]
    assert len(se_risks2) == 1, bundle2["risks"]


# ---------------------------------------------------------------------------
# 5. pure function: no io_write kinds, no causal-diff risks
# ---------------------------------------------------------------------------


def test_causal_diff_for_pure_function_no_risks(
    project_factory, cli_runner, monkeypatch
):
    """pure-function refactors don't surface io_write risks or
    high-severity findings, and never trigger auto:causal-diff
    entries. pure-kind churn (e.g. refactoring ``return a+b`` to
    ``total = a+b; return total`` produces removed
    ``param_to_return->return`` edges) is expected and benign."""
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

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "pure"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "add"])

    # Edit the body but keep it pure.
    _rewrite_and_reindex(
        proj,
        "src/pure.py",
        "def add(a, b):\n"
        "    total = a + b\n"
        "    return total\n",
    )

    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    data = parse_json_output(r, command="pr-bundle")
    cd_dist = data["summary"]["causal_diff_distribution"]
    # No io_write paths at all, so any potential added/removed edges
    # would still be of pure kinds (param_to_return etc.) which never
    # surface as risks. Body refactors of a pure function CAN produce
    # added/removed param_to_return->return churn (e.g. introducing
    # an intermediate ``total =`` binding shifts the causal path);
    # that's expected and explicitly not a risk. What MUST stay absent
    # is any io_write kind in by_kind.
    by_kind = cd_dist.get("by_kind", {})
    io_write_kinds = [k for k in by_kind if "io_write" in k]
    assert io_write_kinds == [], (
        f"pure function should not produce io_write kinds in by_kind: "
        f"{io_write_kinds!r} (full dist: {cd_dist!r})"
    )
    assert data["summary"]["causal_diff_high_severity_count"] == 0, data["summary"]
    bundle = _read_bundle_file(proj)
    cd_risks = [
        r for r in bundle["risks"]
        if r.get("source_command") == "auto:causal-diff"
    ]
    assert cd_risks == [], cd_risks
    # And no symbol-level dedup-blocking risk either.
    auto_se = [r for r in bundle["risks"] if r.get("id") == "side_effect_add"]
    assert auto_se == [], auto_se


# ---------------------------------------------------------------------------
# 6. envelope includes causal_diff_distribution + verdict mentions io_write
# ---------------------------------------------------------------------------


def test_envelope_includes_causal_diff_distribution(
    project_factory, cli_runner, monkeypatch
):
    """Emit envelope's summary always carries ``causal_diff_distribution``
    and ``causal_diff_high_severity_count`` (Pattern 2: explicit absence
    means zeros, not missing keys). When at least one io_write edge
    changed, the verdict mentions it (LAW 6 — verdict is standalone)."""
    proj = project_factory(
        {
            "src/grow2.py": (
                "from pathlib import Path\n"
                "\n"
                "def writer(path):\n"
                "    return path\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "envelope shape"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "writer"])

    # Grow a new io_write.
    _rewrite_and_reindex(
        proj,
        "src/grow2.py",
        "from pathlib import Path\n"
        "\n"
        "def writer(path):\n"
        "    Path(path).write_text('x')\n"
        "    return path\n",
    )

    r = _invoke(cli_runner, ["--json", "pr-bundle", "emit", "--no-auto-collect"])
    data = parse_json_output(r, command="pr-bundle")
    summary = data["summary"]
    # Key presence (Pattern 2 — keys ALWAYS present, never silently dropped).
    assert "causal_diff_distribution" in summary, summary
    assert "causal_diff_high_severity_count" in summary, summary
    cd_dist = summary["causal_diff_distribution"]
    assert set(cd_dist.keys()) >= {"added_total", "removed_total", "by_kind", "symbols_with_diff"}, cd_dist
    # added_total reflects the new io_write edge.
    assert cd_dist["added_total"] >= 1, cd_dist
    # And the verdict mentions io_write changed since init (LAW 6).
    verdict = summary["verdict"]
    assert "io_write" in verdict and "changed since init" in verdict, verdict


# ---------------------------------------------------------------------------
# 7. snapshot is preserved across re-adds (no clobbering the baseline)
# ---------------------------------------------------------------------------


def test_resnapshot_preserves_original_baseline(
    project_factory, cli_runner, monkeypatch
):
    """Re-adding an affected symbol keeps the ORIGINAL snapshot.

    If we re-snapshotted on every ``add affected``, an agent that called
    ``add affected`` after making edits would lose the pre-edit baseline
    and the diff would always report zero. We test that this doesn't
    happen.
    """
    proj = project_factory(
        {
            "src/restable.py": (
                "from pathlib import Path\n"
                "\n"
                "def writer(path):\n"
                "    return path\n"
            ),
        }
    )
    _pin_branch(proj)
    monkeypatch.chdir(proj)

    _invoke(cli_runner, ["pr-bundle", "init", "--intent", "baseline"])
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "writer"])
    bundle1 = _read_bundle_file(proj)
    snap1 = bundle1["affected_symbols"][0]["causal_snapshot"]
    snap1_at = snap1["snapshot_at"]

    # Now mutate the source AND re-add the affected symbol. The baseline
    # snapshot must NOT change.
    _rewrite_and_reindex(
        proj,
        "src/restable.py",
        "from pathlib import Path\n"
        "\n"
        "def writer(path):\n"
        "    Path(path).write_text('x')\n"
        "    return path\n",
    )
    _invoke(cli_runner, ["pr-bundle", "add", "affected", "writer"])

    bundle2 = _read_bundle_file(proj)
    snap2 = bundle2["affected_symbols"][0]["causal_snapshot"]
    assert snap2["snapshot_at"] == snap1_at, (
        "snapshot_at must be unchanged across re-add",
    )
    assert snap2["edges"] == snap1["edges"], (
        "snapshot edges must be the original baseline, not the new graph",
    )
