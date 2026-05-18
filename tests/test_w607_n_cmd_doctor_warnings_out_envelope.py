"""W607-N — ``cmd_doctor`` threads ``warnings_out`` onto its envelope.

Fourteenth-in-batch W607 consumer-layer arc. DB-shape continuation after
W607-K (cmd_describe), W607-L (cmd_minimap), W607-M (cmd_health).
cmd_doctor per CLAUDE.md is the flagship environment-aggregator that
consumes findings + health + describe + retrieve + index_status
substrates through ~22 per-check helpers (corpus content, required
tables, manifest, manifest history, index step failures, phase timings,
math_signals drift, CI workflow drift, command registry, MCP registry,
…). Each helper already returns a ``passed: False`` dict on its own
exceptions, but the helper ITSELF can still raise before producing
that dict (Python misconfig, networkx import explosion, OSError in
``shutil.which``, …) — and the outer ``checks.append(_check_X())``
loop has no guards. W607-N adds the per-phase outer wrapper +
``warnings_out`` marker emission so that lineage is preserved.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_doctor.py`` head-to-tail.
The dominant additional failure surface BEYOND W805-836 Pattern-2
silent "all checks passed" on empty corpus is the per-helper raise
path: each ``_check_*`` helper has its own internal try/except for
its expected exception classes, but the call boundary itself is
unprotected. W607-N wraps each ``checks.append(_check_X())`` with a
single try/except that emits ``doctor_<phase>_failed:<exc>:<detail>``
markers via ``warnings_out`` and skips the check on raise so the
envelope still emits the remaining checks cleanly.

W805-836 (Pattern-2 silent "all checks passed" on empty corpus) is
COMPLEMENTARY: it pins the corpus-content advisory check shape (the
W836 "Corpus content" advisory check now ships in the doctor pipeline).
W607-N adds the substrate-failure disclosure axis to the SAME
envelope. On empty corpus the W836 advisory check fires and lands in
``checks`` cleanly (NOT raise), so warnings_out stays empty and the
envelope is byte-identical — W805-836 xfail-strict tests MUST remain
xfailed after W607-N lands.

Marker family is ``doctor_*`` — NOT ``health_*`` (W607-M), NOT
``describe_*`` (W607-K), NOT ``minimap_*`` (W607-L), NOT ``grep_*``
(W607-G), NOT ``history_*`` (W607-H), NOT ``refs_text_*`` (W607-I),
NOT ``delete_check_*`` (W607-J), NOT ``search_*`` / ``complete_*`` /
``semantic_*`` (W607-E/F/A). The marker-prefix discipline test pins
this closed-enum distinction.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings added. ``warnings_out`` is a
plain accumulator (mirrors W607-G's cmd_grep / W607-K's cmd_describe
/ W607-L's cmd_minimap / W607-M's cmd_health idiom). The per-helper
wrapper ``_run_check`` lives in the ``doctor()`` body so the bucket
collects markers consistently across every helper invocation.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import json as _json

import pytest
from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Helpers — invoke doctor via the Click group (uses --json flag on group)
# ---------------------------------------------------------------------------


def _invoke_doctor(runner: CliRunner, json_mode: bool = True, *extra):
    """Invoke ``roam doctor`` through the group so ``--json`` is honoured."""
    args = []
    if json_mode:
        args.append("--json")
    args.append("doctor")
    args.extend(extra)
    return runner.invoke(cli, args, catch_exceptions=False)


@pytest.fixture
def cli_runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# (1) Happy path — clean doctor → envelope omits warnings_out
# ---------------------------------------------------------------------------


def test_clean_happy_path(cli_runner):
    """Clean doctor run → envelope omits warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. The empty-bucket-no-keys discipline ensures
    consumers can't accidentally read a stale or always-present
    warnings_out field.
    """
    result = _invoke_doctor(cli_runner, json_mode=True)
    data = _json.loads(result.output)
    assert data["command"] == "doctor"
    # Happy or advisory path — exit 0 or 1 acceptable; what matters is
    # that no helper itself raised, so warnings_out must be absent.
    assert result.exit_code in (0, 1, 2), result.output
    assert "warnings_out" not in data, (
        f"clean doctor must NOT surface top-level warnings_out; got {data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean doctor must NOT populate summary.warnings_out; got {data['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) DB-phase failure marker fires when a substrate helper raises
# ---------------------------------------------------------------------------


def test_db_phase_failure_marker(cli_runner, monkeypatch):
    """If a helper raises (here ``_check_corpus_content``), the W607-N
    per-phase guard surfaces a ``doctor_<phase>_failed:`` marker.

    Substrate-failure shape: patch ``_check_corpus_content`` to raise
    (a substrate that queries ``SELECT COUNT(*) FROM symbols``). The
    per-phase guard inside ``doctor()`` catches it and threads the
    marker.
    """
    from roam.commands import cmd_doctor

    def _boom_corpus():
        raise PermissionError("synthetic-corpus-from-W607-N")

    monkeypatch.setattr(cmd_doctor, "_check_corpus_content", _boom_corpus)

    result = _invoke_doctor(cli_runner, json_mode=True)
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"_check_corpus_content PermissionError must surface top-level "
        f"warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    assert any(m.startswith("doctor_corpus_content_failed:") for m in top_wo), (
        f"expected ``doctor_corpus_content_failed:`` marker; got {top_wo!r}"
    )
    assert any("PermissionError" in m for m in top_wo), top_wo
    assert any("synthetic-corpus-from-W607-N" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (3) Helper consumption failure disclosed (substrate helper raises)
# ---------------------------------------------------------------------------


def test_helper_consumption_failure_disclosed(cli_runner, monkeypatch):
    """Substrate-helper failure surfaces via ``doctor_<phase>_failed:``.

    Patch ``_check_required_tables`` to raise — this exercises the
    table-listing per-phase guard inside ``doctor()`` and the W607-N
    marker-thread on a DIFFERENT substrate (sqlite_master) than the
    corpus_content axis.
    """
    from roam.commands import cmd_doctor

    def _boom_tables():
        raise RuntimeError("synthetic-tables-from-W607-N")

    monkeypatch.setattr(cmd_doctor, "_check_required_tables", _boom_tables)

    result = _invoke_doctor(cli_runner, json_mode=True)
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, (
        f"_check_required_tables RuntimeError must surface "
        f"top-level warnings_out; got data keys = {sorted(data.keys())!r}"
    )
    table_markers = [m for m in top_wo if m.startswith("doctor_required_tables_failed:")]
    assert table_markers, f"expected ``doctor_required_tables_failed:`` marker; got {top_wo!r}"
    assert any("RuntimeError" in m for m in table_markers), table_markers


# ---------------------------------------------------------------------------
# (4) No-failure clean → byte-identical envelope (hash stability)
# ---------------------------------------------------------------------------


def test_no_match_byte_identical(cli_runner):
    """Clean envelope must NOT carry warnings_out keys when no markers fire.

    Empty-bucket discipline: the W607-N plumbing must NOT leak the
    empty bucket onto a clean envelope. The pre-W607-N envelope shape
    is preserved byte-for-byte when no markers fired. Hash-stable
    contract matches W607-A/B/C/D/E/F/G/H/I/J/K/L/M discipline.
    """
    result = _invoke_doctor(cli_runner, json_mode=True)
    data = _json.loads(result.output)

    assert "warnings_out" not in data, (
        f"clean envelope must omit top-level warnings_out; got data['warnings_out']={data.get('warnings_out')!r}"
    )
    assert "warnings_out" not in data["summary"], (
        f"clean envelope must omit summary.warnings_out; got summary={data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Three-segment marker shape — prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_three_segment_marker_shape(cli_runner, monkeypatch):
    """Marker must have three colon-separated segments.

    Shape contract: ``<prefix>:<exc_class>:<detail>`` so downstream
    consumers can parse the exception class without regex gymnastics.
    Mirrors W607-A/B/C/D/E/F/G/H/I/J/K/L/M contracts.
    """
    from roam.commands import cmd_doctor

    def _boom_corpus():
        raise PermissionError("synthetic-shape-detail-from-W607-N")

    monkeypatch.setattr(cmd_doctor, "_check_corpus_content", _boom_corpus)

    result = _invoke_doctor(cli_runner, json_mode=True)
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out") or []
    assert top_wo, "_check_corpus_content per-phase guard must emit a marker"
    failure_markers = [m for m in top_wo if m.startswith("doctor_corpus_content_failed:")]
    assert failure_markers, f"expected doctor_corpus_content_failed: marker; got {top_wo!r}"

    marker = failure_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "doctor_corpus_content_failed", parts
    assert parts[1] == "PermissionError", parts
    assert parts[2], parts


# ---------------------------------------------------------------------------
# (6) Marker-prefix discipline — ``doctor_*`` not health/describe/etc.
#     family prefixes (closed-enum discipline)
# ---------------------------------------------------------------------------


def test_marker_prefix_doctor_not_health_or_describe(cli_runner, monkeypatch):
    """Every surfaced marker uses the canonical ``doctor_*`` prefix.

    cmd_doctor is the FLAGSHIP DB-SHAPE AGGREGATOR axis — distinct
    from:

    * cmd_health           → ``health_*`` (W607-M flagship CI-gate)
    * cmd_describe         → ``describe_*`` (W607-K flagship aggregator)
    * cmd_minimap          → ``minimap_*`` (W607-L DB-shape aggregator)
    * cmd_grep             → ``grep_*`` (W607-G ripgrep/git-grep fan-out)
    * cmd_history_grep     → ``history_*`` (W607-H pickaxe)
    * cmd_refs_text        → ``refs_text_*`` (W607-I string-audit)
    * cmd_delete_check     → ``delete_check_*`` (W607-J diff-gate)
    * cmd_search           → ``search_*`` (W607-E lexical)
    * cmd_complete         → ``complete_*`` (W607-F prefix)
    * cmd_search_semantic  → ``semantic_*`` (W607-A FTS5)
    * cmd_findings         → ``findings_query_*`` (W607-C registry)
    * cmd_dogfood          → ``dogfood_*`` (W607-D corpus loader)
    * cmd_retrieve         → ``retrieve_*`` (W607-B pipeline)

    Hard guard against accidental marker-prefix drift (a future
    contributor mis-routing a marker into a sibling family because
    cmd_doctor is a high-traffic CI surface that may be edited next to
    cmd_health / cmd_describe by a refactor wave). Closes the
    closed-enum discipline at the cmd_doctor boundary.
    """
    from roam.commands import cmd_doctor

    def _boom_corpus():
        raise PermissionError("synthetic-prefix-discipline-from-W607-N")

    monkeypatch.setattr(cmd_doctor, "_check_corpus_content", _boom_corpus)

    result = _invoke_doctor(cli_runner, json_mode=True)
    data = _json.loads(result.output)
    top_wo = data.get("warnings_out") or []
    assert top_wo, "expected non-empty warnings_out for prefix-consistency check"
    for marker in top_wo:
        assert marker.startswith("doctor_"), (
            f"every surfaced marker must use the W607-N ``doctor_*`` "
            f"prefix family (cmd_doctor DB-shape aggregator scope); "
            f"got {marker!r}"
        )
        # Hard distinction from sibling W607-* layers.
        for forbidden_prefix, sibling in (
            ("health_", "cmd_health W607-M"),
            ("describe_", "cmd_describe W607-K"),
            ("minimap_", "cmd_minimap W607-L"),
            ("grep_", "cmd_grep W607-G"),
            ("history_", "cmd_history_grep W607-H"),
            ("refs_text_", "cmd_refs_text W607-I"),
            ("delete_check_", "cmd_delete_check W607-J"),
            ("search_", "cmd_search W607-E"),
            ("complete_", "cmd_complete W607-F"),
            ("semantic_", "cmd_search_semantic W607-A"),
            ("findings_query_", "cmd_findings W607-C"),
            ("dogfood_", "cmd_dogfood W607-D"),
            ("retrieve_", "cmd_retrieve W607-B"),
        ):
            assert not marker.startswith(forbidden_prefix), (
                f"marker leaked into ``{forbidden_prefix}*`` family ({sibling} scope); got {marker!r}"
            )


# ---------------------------------------------------------------------------
# (7) partial_success flips on DB failure
# ---------------------------------------------------------------------------


def test_partial_success_flip_on_db_failure(cli_runner, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    doctor" from "doctor ran with substrate degradation" via
    summary.partial_success alone, independent of the verdict text.
    (Complementary to W805-836 — that axis pins the empty-corpus
    silent "all checks passed" verdict via the W836 "Corpus content"
    advisory; W607-N pins the substrate-failure flip on the per-helper
    raise path.)
    """
    from roam.commands import cmd_doctor

    def _boom_corpus():
        raise PermissionError("synthetic-partial-success-from-W607-N")

    monkeypatch.setattr(cmd_doctor, "_check_corpus_content", _boom_corpus)

    result = _invoke_doctor(cli_runner, json_mode=True)
    data = _json.loads(result.output)
    assert data["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {data['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (8) summary.warnings_out mirror — top-level AND summary populated
# ---------------------------------------------------------------------------


def test_summary_warnings_out_mirror(cli_runner, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror
    gives consumers reading only the summary block visibility too.
    Mirror parity with W607-A/B/C/D/E/F/G/H/I/J/K/L/M consumers.
    """
    from roam.commands import cmd_doctor

    def _boom_corpus():
        raise PermissionError("synthetic-mirror-from-W607-N")

    monkeypatch.setattr(cmd_doctor, "_check_corpus_content", _boom_corpus)

    result = _invoke_doctor(cli_runner, json_mode=True)
    data = _json.loads(result.output)

    assert data.get("warnings_out"), (
        f"top-level warnings_out missing on disclosure path; keys = {sorted(data.keys())!r}"
    )
    assert data["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {data['summary']!r}"
    )
    # Top-level and summary content must be equal.
    assert sorted(data["warnings_out"]) == sorted(data["summary"]["warnings_out"]), (
        f"top-level vs summary.warnings_out must be equal; "
        f"top={data['warnings_out']!r} summary={data['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (9) Top-level mirror explicitly checked (W607-A..M discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(cli_runner, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. W607-A through W607-M pinned the same
    discipline; W607-N extends it to cmd_doctor — fourth DB-shape
    consumer in the W607 arc (after K=describe, L=minimap, M=health).
    """
    from roam.commands import cmd_doctor

    def _boom_corpus():
        raise PermissionError("synthetic-top-level-from-W607-N")

    monkeypatch.setattr(cmd_doctor, "_check_corpus_content", _boom_corpus)

    result = _invoke_doctor(cli_runner, json_mode=True)
    data = _json.loads(result.output)

    top_wo = data.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (10) W805-836 parity — empty-corpus "all checks passed" disclosure
#      must NOT be graduated by W607-N (separate Pattern-2 fix wave).
# ---------------------------------------------------------------------------


def test_w805_836_xfail_still_strict():
    """W805-836 Pattern-2 disclosure (W836 "Corpus content" advisory)
    must remain at status quo.

    W805-836 / W836 pins the corpus-content advisory check in the
    cmd_doctor pipeline so a clean env + empty corpus no longer
    emits "all N checks passed" silently — the W836 "Corpus content"
    advisory fires with ``state: empty`` and ``passed: False`` instead.
    The advisory live registration sits in ``_ADVISORY_CHECK_NAMES``
    inside ``cmd_doctor.py`` and the advisory CHECK in
    ``_check_corpus_content`` returns the disclosure dict.

    W607-N adds a COMPLEMENTARY disclosure axis (per-helper raise via
    ``warnings_out``), but does NOT address the empty-corpus
    Pattern-2 contract — empty-corpus state is handled by the W836
    advisory check which returns its own ``passed: False`` dict
    cleanly (NOT raise), so warnings_out stays empty on that path.

    Verify the cmd_doctor source still wires the W836 corpus-content
    check and registers it in the advisory allowlist. Source-text scan
    beats invoking pytest-on-pytest; if either substrate were removed,
    this assertion catches it.
    """
    from pathlib import Path

    src_path = Path(__file__).parent.parent / "src" / "roam" / "commands" / "cmd_doctor.py"
    assert src_path.exists(), f"cmd_doctor.py missing at {src_path}"
    src = src_path.read_text(encoding="utf-8")
    # Pin the W836 corpus-content advisory check name in the advisory
    # allowlist — this encodes the Pattern-2 silent-fallback discipline
    # that W805-836 owns and W607-N does NOT touch.
    assert '"Corpus content"' in src, (
        "W836 corpus-content advisory check name removed from "
        "cmd_doctor; W607-N scope is the per-helper substrate-degrade "
        "axis only — empty-corpus silent-pass is a separate Pattern-2 fix."
    )
    # Pin the canonical helper that returns the W836 advisory dict.
    assert "_check_corpus_content" in src, (
        "W836 corpus-content helper removed; W607-N does not change the empty-corpus check contract."
    )
    # Pin the W836 advisory-failure detail so the disclosure phrase
    # contract stays explicit (W978 first-hypothesis discipline:
    # confirm the substrate exists before declaring W607-N orthogonal).
    assert "corpus empty" in src, (
        "W836 empty-corpus disclosure phrase removed from cmd_doctor; "
        "W607-N scope is the per-helper substrate-degrade axis only."
    )
