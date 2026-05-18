"""W805-IIII — snapshot-state disclosure pin for ``cmd_fingerprint``.

Eighty-seventh-in-batch W805 sweep, ``cmd_fingerprint.py``. FOURTH member of the
counterfactual/snapshot-state family alongside:

- W805-BBBB cmd_simulate (counterfactual TARGET-side resolution)
- W805-DDDD cmd_orchestrate (partition output vacuous)
- W805-GGGG cmd_capsule (snapshot freshness disclosure)

Hypothesis (W805-GGGG agent recommendation): ``cmd_fingerprint`` extracts a
topology signature for **cross-repo comparison** AND writes/reads fingerprint
JSON files via ``--export`` / ``--compare``. The capture envelope does NOT
record indexed_at / git_head / commit lineage; the exported fingerprint file
does NOT carry lineage; the ``--compare`` flow loads an arbitrary JSON dict
from disk and merges it into the envelope WITHOUT validating that it IS a
fingerprint or that it was computed against the same / compatible index. This
is the same Pattern-1-V-D + CP45 lineage gap W805-GGGG pinned on
``cmd_capsule``, projected onto the comparison axis (where the consequence is
worse — a numeric similarity score is emitted on bogus input).

W978 first-hypothesis re-run BEFORE writing any test
====================================================

Probed live behaviour:

1. Indexed clean 2-symbol corpus, ran ``roam --json fingerprint``. Envelope
   summary keys: ``clusters_emitted / clusters_total / clusters_truncated /
   fiedler / god_components / god_components_definition / layers / modularity
   / partial_success / tangle_ratio / verdict``. The exported fingerprint
   payload keys: ``antipatterns / clusters / dependency_direction /
   hub_bridge_ratio / pagerank_gini / topology``. ZERO of
   ``indexed_at / git_head / git_dirty_hash / captured_at / freshness /
   staleness / index_state`` appear anywhere.

2. Wrote a fingerprint at commit A, mutated the repo + re-indexed at commit B,
   then ran ``roam --json fingerprint --compare A.json`` from commit B's
   index. The envelope emits ``similarity_score`` + per_metric deltas with
   no disclosure of WHICH commit / WHICH index each side was computed
   against. The ``comparison`` block carries only ``direction_match /
   euclidean_distance / per_metric / similarity`` — no commit / indexed_at /
   source-of-other-fingerprint field.

3. Wrote a non-fingerprint JSON (``{"this": "is not a fingerprint"}``) to
   disk, ran ``--compare`` on it. Result: exit 0, ``similarity: 0.9823``
   (98% similar to a nonsense dict), ``partial_success: False``, no
   resolution disclosure. This is the canonical Pattern-1-V-D failure mode
   — a degraded resolution produces a verdict structurally identical to
   a fully-resolved success.

W907 verify-cycle check
=======================

grep -i 'avoid.*cycle|circular import|kept local|would create a cycle' on
``src/roam/commands/cmd_fingerprint.py`` + ``src/roam/graph/fingerprint.py``
== NO MATCHES. The deferred imports inside the function body (``from
roam.graph.builder import ...`` / ``from roam.graph.fingerprint import ...``)
have no docstring hedging — they are benign lazy imports (defer heavy
networkx/numpy import cost), not cargo-cult false cycles. W907 clean.

Pinned via ``xfail(strict=True)`` so a future fix is detected (xpass →
test failure → unwrap).

Run isolation:
    python -m pytest tests/test_w805_iiii_cmd_fingerprint_snapshot_state.py -x -n 0

Regression baseline:
    python -m pytest tests/test_fingerprint.py -x -n 0
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_commit, git_init, index_in_process  # noqa: E402

# ---------------------------------------------------------------------------
# Module existence gate (W978 + W907 — verify before hypothesising)
# ---------------------------------------------------------------------------

_CMD_FINGERPRINT_SPEC = importlib.util.find_spec("roam.commands.cmd_fingerprint")
_GRAPH_FP_SPEC = importlib.util.find_spec("roam.graph.fingerprint")
_MANIFEST_SPEC = importlib.util.find_spec("roam.index.manifest")


def test_command_and_freshness_substrate_exist():
    """W978/W907 gate: fingerprint + manifest substrates import cleanly."""
    if _CMD_FINGERPRINT_SPEC is None:
        pytest.skip("roam.commands.cmd_fingerprint not installed")
    assert _GRAPH_FP_SPEC is not None, "roam.graph.fingerprint missing"
    assert _MANIFEST_SPEC is not None, "roam.index.manifest missing — bug pin assumes manifest substrate exists"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    return CliRunner()


def _make_repo(tmp_path: Path, name: str, files: dict) -> Path:
    proj = tmp_path / name
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    for rel, content in files.items():
        fp = proj / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    git_init(proj)
    return proj


@pytest.fixture
def fresh_indexed_project(tmp_path, monkeypatch):
    """Small indexed corpus — fresh index, clean git HEAD."""
    proj = _make_repo(
        tmp_path,
        "fresh_fp_iiii",
        {
            "app.py": ("def alpha():\n    return 1\n\ndef beta(x):\n    return alpha() + x\n"),
        },
    )
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def two_commit_project(tmp_path, monkeypatch):
    """Indexed project after two distinct commits, with a fingerprint file
    captured at the FIRST commit. Used to probe "compare across commits"
    lineage disclosure.

    Returns (proj, fp_path_at_commit_A) where fp_path_at_commit_A is the
    exported fingerprint from commit A (graph state different from current
    HEAD).
    """
    proj = _make_repo(
        tmp_path,
        "two_commit_fp_iiii",
        {
            "a.py": ("def alpha():\n    return 1\n\ndef beta(x):\n    return alpha() + x\n"),
        },
    )
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed at commit A: {out}"

    fp_a = proj / "fp_commit_a.json"
    from roam.cli import cli

    runner = CliRunner()
    r = runner.invoke(cli, ["fingerprint", "--export", str(fp_a)], catch_exceptions=False)
    assert r.exit_code == 0, f"export at commit A failed: {r.output}"
    assert fp_a.exists()

    # Mutate + commit + reindex → commit B (different graph topology).
    (proj / "b.py").write_text(
        "def gamma():\n    return 2\n\n"
        "def delta():\n    return gamma() + alpha_extern()\n\n"
        "def alpha_extern():\n    return 3\n",
        encoding="utf-8",
    )
    git_commit(proj, "add b.py")
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed at commit B: {out}"

    return proj, fp_a


@pytest.fixture
def stale_indexed_project(tmp_path, monkeypatch):
    """Indexed project where the index is *demonstrably stale* (DB back-dated 30d)."""
    proj = _make_repo(
        tmp_path,
        "stale_fp_iiii",
        {
            "app.py": "def hello():\n    return 'world'\n",
        },
    )
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"
    # Back-date the DB to make stale_index.check_stale fire.
    db_path = proj / ".roam" / "roam.db"
    if db_path.exists():
        old_ts = time.time() - (30 * 24 * 3600)
        os.utime(db_path, (old_ts, old_ts))
    return proj


# ---------------------------------------------------------------------------
# Invoke helper
# ---------------------------------------------------------------------------


def _invoke_fingerprint(runner, cwd: Path, *extra, json_mode: bool = True):
    """Invoke ``roam [--json] fingerprint ...`` via the top-level group."""
    from roam.cli import cli

    args: list[str] = []
    if json_mode:
        args.append("--json")
    args.append("fingerprint")
    args.extend(extra)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        result = runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result


def _parse_json(result):
    assert result.exit_code == 0, f"fingerprint exit={result.exit_code}:\n{result.output}"
    raw = (result.output or "").lstrip()
    assert raw.startswith("{"), f"expected JSON envelope, got:\n{result.output!r}"
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(raw)
    return obj


def _all_envelope_keys(data: dict) -> set:
    """Union of root + summary + fingerprint + comparison key sets."""
    keys = set(data.keys())
    keys |= set((data.get("summary") or {}).keys())
    keys |= set((data.get("fingerprint") or {}).keys())
    keys |= set((data.get("comparison") or {}).keys())
    return keys


_FRESHNESS_KEYS = {
    "indexed_at",
    "index_indexed_at",
    "index_freshness",
    "freshness",
    "staleness",
    "stale",
    "is_stale",
    "captured_at",
    "capture_state",
    "index_state",
    "snapshot_state",
}

_LINEAGE_KEYS = {
    "git_head",
    "indexed_git_head",
    "git_dirty_hash",
    "commit_sha",
    "commit",
    "index_schema_version",
    "roam_version_at_index",
    "index_roam_version",
    "index_manifest",
    "manifest",
}

_COMPARE_LINEAGE_KEYS = {
    # Fields that would disclose WHICH index/commit each side of the compare
    # was computed against. A bug-free --compare envelope would expose at
    # least one of these on the comparison block or summary.
    "this_commit",
    "other_commit",
    "this_indexed_at",
    "other_indexed_at",
    "this_git_head",
    "other_git_head",
    "compare_source",
    "compare_lineage",
    "other_fingerprint_source",
    "fingerprint_lineage",
}


# ---------------------------------------------------------------------------
# Positive shape tests — fingerprint must remain parseable on both axes
# ---------------------------------------------------------------------------


class TestFingerprintEnvelopeBaseline:
    def test_fresh_fingerprint_envelope_parses(self, fresh_indexed_project, cli_runner):
        result = _invoke_fingerprint(cli_runner, fresh_indexed_project)
        data = _parse_json(result)
        assert "summary" in data
        assert "verdict" in data["summary"]
        assert "fingerprint" in data

    def test_fingerprint_export_roundtrip(self, fresh_indexed_project, cli_runner, tmp_path):
        fp_out = tmp_path / "exported.json"
        from roam.cli import cli

        runner = cli_runner
        old_cwd = os.getcwd()
        try:
            os.chdir(str(fresh_indexed_project))
            r = runner.invoke(cli, ["fingerprint", "--export", str(fp_out)], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert r.exit_code == 0
        assert fp_out.exists()
        payload = json.loads(fp_out.read_text(encoding="utf-8"))
        # Sanity: the export must contain at least topology+clusters.
        assert "topology" in payload
        assert "clusters" in payload


# ---------------------------------------------------------------------------
# Sister-family invariant cross-checks (must stay green; do NOT re-assert
# the sister files' xfail-strict claims to avoid collision).
# ---------------------------------------------------------------------------


class TestW805GgggInvariantsPreserved:
    """W805-GGGG (cmd_capsule snapshot-state) sister cross-check.

    Asserts the baseline capsule invariant survives — capsule emits a
    parseable envelope with ``capsule.generated`` (wall-clock). We do
    NOT re-assert W805-GGGG's xfail-strict pins.
    """

    def test_capsule_baseline_parseable(self, tmp_path, monkeypatch, cli_runner):
        proj = _make_repo(
            tmp_path,
            "w805_gggg_parity_iiii",
            {"app.py": "def x():\n    return 1\n"},
        )
        monkeypatch.chdir(proj)
        out, rc = index_in_process(proj, "--force")
        assert rc == 0, f"index failed: {out}"

        from roam.commands.cmd_capsule import capsule

        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            result = cli_runner.invoke(capsule, [], obj={"json": True}, catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "summary" in data
        assert "verdict" in data["summary"]
        assert "generated" in data.get("capsule", {})


class TestW805BbbbInvariantsPreserved:
    """W805-BBBB (cmd_simulate counterfactual TARGET) sister cross-check.

    Baseline invariant: ``roam simulate --help`` returns 0 and lists the
    move/merge subcommands. We do NOT re-assert W805-BBBB's xfail-strict
    pins.
    """

    def test_simulate_help_runs(self, cli_runner):
        from roam.cli import cli

        result = cli_runner.invoke(cli, ["simulate", "--help"], catch_exceptions=False)
        assert result.exit_code == 0
        # Must mention at least one transform verb the sister test pins on.
        assert "move" in result.output.lower() or "merge" in result.output.lower()


# ---------------------------------------------------------------------------
# REAL BUG — Pattern-1 variant D + CP45 lineage rule
# Pinned xfail(strict=True): fix will flip to xpass → test failure.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "W805-IIII Pattern-1-V-D bug: cmd_fingerprint.py:597-643 builds the "
        "envelope from compute_fingerprint(conn, G) but NEVER consults "
        "roam.index.manifest.latest_manifest() or roam.commands.stale_index. "
        "The capture envelope, the exported fingerprint payload, and the "
        "--compare comparison block all carry ZERO lineage fields "
        "(no indexed_at / git_head / commit_sha / freshness / staleness / "
        "this_commit / other_commit). A fingerprint captured at commit A is "
        "structurally indistinguishable from one captured at commit B, and "
        "--compare emits a real numeric similarity_score (0.98) on bogus "
        "non-fingerprint JSON input with partial_success=False. Fix: stamp "
        "indexed_at + git_head + roam_version into the exported fingerprint "
        "AND into the capture summary; validate the loaded --compare payload "
        "shape AND disclose other_indexed_at / other_git_head on the "
        "comparison block; set partial_success=True when the loaded file "
        "lacks lineage. See CLAUDE.md Pattern-1-V-D + 'Make fallback chains "
        "loud' (CP45/CP46) + W805-GGGG sister pin."
    ),
)
class TestFingerprintSnapshotStateDisclosureBug:
    def test_capture_freshness_field_disclosed(self, fresh_indexed_project, cli_runner):
        """Pattern-1-V-D mirror of W805-GGGG: capture-time freshness must
        surface somewhere in the envelope."""
        result = _invoke_fingerprint(cli_runner, fresh_indexed_project)
        data = _parse_json(result)
        keys = _all_envelope_keys(data)
        overlap = _FRESHNESS_KEYS & keys
        assert overlap, (
            f"Pattern-1-V-D: fingerprint envelope discloses NO freshness "
            f"field. Looked for one of {sorted(_FRESHNESS_KEYS)}; envelope "
            f"had {sorted(keys)}."
        )

    def test_capture_lineage_field_disclosed(self, fresh_indexed_project, cli_runner):
        """CP45 lineage rule: the capture must record WHICH commit / WHICH
        index it was computed against, so cross-repo comparisons are
        traceable."""
        result = _invoke_fingerprint(cli_runner, fresh_indexed_project)
        data = _parse_json(result)
        keys = _all_envelope_keys(data)
        overlap = _LINEAGE_KEYS & keys
        assert overlap, (
            f"CP45 lineage: capture envelope has NO git_head / indexed_at / "
            f"commit_sha / manifest field. Looked for one of "
            f"{sorted(_LINEAGE_KEYS)}; envelope had {sorted(keys)}."
        )

    def test_exported_fingerprint_carries_lineage(self, fresh_indexed_project, cli_runner, tmp_path):
        """The fingerprint file written by --export is the artefact that
        cross-repo comparison consumes. It MUST carry lineage so a stale
        on-disk fingerprint can be told apart from a fresh one."""
        fp_out = tmp_path / "lineage_check.json"
        from roam.cli import cli

        old_cwd = os.getcwd()
        try:
            os.chdir(str(fresh_indexed_project))
            r = cli_runner.invoke(cli, ["fingerprint", "--export", str(fp_out)], catch_exceptions=False)
        finally:
            os.chdir(old_cwd)
        assert r.exit_code == 0, r.output
        payload = json.loads(fp_out.read_text(encoding="utf-8"))
        payload_keys = set(payload.keys())
        # The exported fingerprint payload is the cross-repo wire format.
        # Today it's pure topology — no lineage.
        overlap = (_LINEAGE_KEYS | _FRESHNESS_KEYS) & payload_keys
        assert overlap, (
            f"CP45 lineage: exported fingerprint JSON carries no lineage "
            f"fields. payload keys={sorted(payload_keys)}; expected one of "
            f"{sorted(_LINEAGE_KEYS | _FRESHNESS_KEYS)}."
        )

    def test_compare_two_fingerprints_lineage_disclosure(self, two_commit_project, cli_runner):
        """When --compare loads another fingerprint, the comparison block
        MUST disclose lineage of BOTH sides — otherwise a similarity
        score is unverifiable."""
        proj, fp_a = two_commit_project
        result = _invoke_fingerprint(cli_runner, proj, "--compare", str(fp_a))
        data = _parse_json(result)
        cmp_block = data.get("comparison") or {}
        cmp_keys = set(cmp_block.keys())
        # Search both the comparison block AND the summary for lineage
        # disclosure (a fix could stamp it on either).
        all_keys = _all_envelope_keys(data)
        overlap = _COMPARE_LINEAGE_KEYS & (cmp_keys | all_keys)
        assert overlap, (
            f"Pattern-1-V-D + CP45: --compare emits similarity score "
            f"({cmp_block.get('similarity')}) but no this_commit / "
            f"other_commit / this_indexed_at / other_indexed_at field. "
            f"Comparison block keys={sorted(cmp_keys)}; envelope keys="
            f"{sorted(all_keys)}."
        )

    def test_fingerprint_at_different_commits_disclosed(self, two_commit_project, cli_runner):
        """Two fingerprints from DIFFERENT commits must be detectable as
        such — the verdict / summary must signal commit divergence
        (different sha / partial_success=True / explicit "commits differ"
        flag)."""
        proj, fp_a = two_commit_project
        result = _invoke_fingerprint(cli_runner, proj, "--compare", str(fp_a))
        data = _parse_json(result)
        summary = data.get("summary") or {}
        cmp_block = data.get("comparison") or {}
        # Look for ANY signal that the two sides are from different commits.
        # Today none of these are emitted.
        commit_signal = (
            summary.get("commits_differ") is True
            or summary.get("same_index") is False
            or summary.get("same_commit") is False
            or summary.get("partial_success") is True
            or cmp_block.get("commits_differ") is True
            or cmp_block.get("same_commit") is False
        )
        assert commit_signal, (
            f"Pattern-1-V-D: --compare across commits emits no "
            f"commits_differ / same_commit / partial_success signal. "
            f"summary={summary}, comparison_keys={sorted(cmp_block.keys())}."
        )

    def test_bogus_fingerprint_path_resolution_state(self, fresh_indexed_project, cli_runner, tmp_path):
        """Pattern-1-V-D variant: ``--compare`` on a JSON file that is NOT
        a fingerprint (e.g. a bare dict ``{"this": "is not a fingerprint"}``)
        currently returns ``similarity: 0.98`` with no resolution flag.
        A fix MUST flag the resolution state of the loaded file."""
        bogus = tmp_path / "not_a_fingerprint.json"
        bogus.write_text('{"this": "is not a fingerprint"}', encoding="utf-8")
        result = _invoke_fingerprint(cli_runner, fresh_indexed_project, "--compare", str(bogus))
        data = _parse_json(result)
        summary = data.get("summary") or {}
        cmp_block = data.get("comparison") or {}
        # A correct fix would either set partial_success=True, emit an
        # explicit other_resolution / fingerprint_valid signal, or refuse
        # to emit similarity_score on a malformed payload.
        resolution_signal = (
            summary.get("partial_success") is True
            or summary.get("other_resolution") in {"invalid", "malformed", "unresolved"}
            or summary.get("compare_resolution") in {"invalid", "malformed", "unresolved"}
            or cmp_block.get("other_resolution") in {"invalid", "malformed", "unresolved"}
            or summary.get("fingerprint_valid") is False
        )
        assert resolution_signal, (
            f"Pattern-1-V-D: --compare on non-fingerprint JSON returned "
            f"similarity={cmp_block.get('similarity')!r} with no "
            f"resolution disclosure. summary={summary}, "
            f"comparison_keys={sorted(cmp_block.keys())}."
        )

    def test_stale_index_capture_discloses_lineage(self, stale_indexed_project, cli_runner):
        """A fingerprint captured against a 30-day-old index MUST disclose
        the stale lineage. Today the envelope is shape-identical to a
        fresh capture."""
        result = _invoke_fingerprint(cli_runner, stale_indexed_project)
        data = _parse_json(result)
        summary = data.get("summary") or {}
        keys = _all_envelope_keys(data)
        has_stale_signal = (
            "stale" in (summary.get("verdict") or "").lower()
            or summary.get("is_stale") is True
            or summary.get("partial_success") is True
            or bool(_FRESHNESS_KEYS & keys)
        )
        assert has_stale_signal, (
            f"Pattern-1-V-D: stale-index fingerprint emits no staleness "
            f"signal. verdict={summary.get('verdict')!r}, "
            f"partial_success={summary.get('partial_success')!r}, "
            f"envelope keys={sorted(keys)}."
        )


# ---------------------------------------------------------------------------
# Advisory probe (passing today) — documents the current pass-through
# semantics the fix must preserve.
# ---------------------------------------------------------------------------


def test_compare_block_carries_per_metric_today(two_commit_project, cli_runner):
    """The today-shape of the comparison block must keep its per_metric +
    similarity + euclidean_distance fields after the fix (additive only)."""
    proj, fp_a = two_commit_project
    result = _invoke_fingerprint(cli_runner, proj, "--compare", str(fp_a))
    data = _parse_json(result)
    cmp_block = data.get("comparison") or {}
    assert "per_metric" in cmp_block
    assert "similarity" in cmp_block
    assert "euclidean_distance" in cmp_block
    assert isinstance(cmp_block["per_metric"], dict)
