"""W607-D — ``cmd_dogfood`` threads ``warnings_out`` onto its JSON envelope.

The W595-W606 substrate-floor Pattern-2 arc plumbed ``warnings_out``
buckets on every silent-fallback substrate reader. W607-A landed the
first consumer-layer wave on ``cmd_search_semantic``. W607-B landed the
second consumer-layer wave on ``cmd_retrieve`` (outer-guard-only, since
cmd_retrieve does not call the W605-plumbed substrate directly). W607-C
landed the third consumer-layer wave on ``cmd_findings`` (3 subcommands
+ 8 emit sites + ``findings_query_failed:`` outer-guard marker).
W607-D is the fourth consumer-layer wave on ``cmd_dogfood`` — a
cross-detector aggregator that composes ``audit`` + ``pr-analyze`` +
``audit-trail-conformance-check`` envelopes into one compound result.

W978 first-hypothesis check (pre-fix audit)
-------------------------------------------

Before writing this file, audited ``cmd_dogfood.py`` head-to-tail:

* The command imports nothing from ``roam.search.index_embeddings``
  directly. The W605-plumbed substrate is NOT a direct callsite.
* cmd_dogfood invokes ``_run_subcommand`` (which uses ``CliRunner`` to
  call sibling commands in-process) and ``git_metadata``. The
  ``_run_subcommand`` helper already returns ``_subcommand_failed``
  sentinels for parse failures (Pattern-2 silent-fallback disclosure
  via the ``failed_sections`` field on the summary), but exceptions
  raised during the broader aggregation (git_metadata crash,
  subcommand-invocation exception bubbling past CliRunner,
  unexpected I/O error) historically bubbled as Click tracebacks
  with no structured envelope.
* No local ``warnings_out`` list existed in cmd_dogfood before W607-D.

Therefore the consumer-side gap was REAL. Because cmd_dogfood has no
direct W605 callsites, the disclosure shape lives at the
**outer-guard boundary**: any uncaught exception from the aggregation
(``git_metadata``, ``_run_subcommand``, or any I/O during section
composition) emits ``dogfood_aggregation_failed:<exc_class>:<detail>``.
This mirrors the cmd_retrieve W607-B
``retrieve_pipeline_failed:`` and cmd_findings W607-C
``findings_query_failed:`` outer-guard markers.

W907 verify-cycle check
-----------------------

No "duplicated to avoid cycle" docstrings were added. The
``warnings_out: list[str] = []`` local is a plain accumulator (mirrors
cmd_findings W607-C / cmd_retrieve W607-B / cmd_search_semantic W607-A
disclosure idioms); no shared module was created or hoisted.

LAW 4 note: warning markers are diagnostic strings, NOT
``agent_contract.facts`` content, and therefore not subject to the
concrete-noun-terminal lint.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_commit, git_init, index_in_process, invoke_cli  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture: a tiny indexed git-tracked project so dogfood can run.
# Mirrors test_dogfood.py::tiny_indexed shape exactly.
# ---------------------------------------------------------------------------


@pytest.fixture
def tiny_indexed(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text("def add(a, b):\n    return a + b\n")
    git_init(proj)
    git_commit(proj, "initial")
    monkeypatch.chdir(proj)
    index_in_process(proj)
    return proj


def _last_json(text: str) -> dict:
    """Pull the last JSON object out of mixed stdout.

    Mirrors test_dogfood.py::_last_json. dogfood aggregates subcommand
    outputs and the final compound envelope is appended last.
    """
    idx = text.rfind("\n{\n")
    if idx == -1:
        idx = text.find("{")
    return _json.loads(text[idx:])


# ---------------------------------------------------------------------------
# (1) HAPPY PATH — clean dogfood → no warnings_out / partial_success only on failed sections
# ---------------------------------------------------------------------------


def test_clean_dogfood_no_warnings(tiny_indexed, cli_runner):
    """Clean dogfood on a tiny indexed repo → envelope has no warnings_out.

    Hash-stable: an empty bucket must produce a byte-identical envelope
    on the success path. The ``failed_sections`` slot is a pre-existing
    Pattern-2 disclosure layer; W607-D adds a SEPARATE ``warnings_out``
    layer for outer-guard / aggregation faults that don't correspond to
    a single subcommand parse failure.
    """
    result = invoke_cli(cli_runner, ["dogfood"], json_mode=True)
    assert result.exit_code == 0, result.output
    env = _last_json(result.output)
    assert env["command"] == "dogfood"

    # No top-level warnings_out on the happy path.
    assert "warnings_out" not in env, (
        f"clean dogfood must NOT surface top-level warnings_out; got {env.get('warnings_out')!r}"
    )
    # No summary.warnings_out either.
    assert "warnings_out" not in env["summary"], (
        f"clean dogfood must NOT populate summary.warnings_out; got {env['summary'].get('warnings_out')!r}"
    )


# ---------------------------------------------------------------------------
# (2) OUTER-GUARD — aggregation raises → marker reaches envelope
# ---------------------------------------------------------------------------


def test_aggregation_failure_emits_marker(tiny_indexed, cli_runner, monkeypatch):
    """Monkeypatch ``git_metadata`` → exception → marker on envelope.

    Pattern-2 outer-guard contract: when the aggregation path raises
    before sections compose, the envelope surfaces a structured marker
    rather than a Click traceback. Mirrors cmd_findings W607-C
    ``findings_query_failed:`` and cmd_retrieve W607-B
    ``retrieve_pipeline_failed:`` outer-guard idioms.
    """
    from roam.commands import cmd_dogfood

    def _boom(*a, **kw):
        raise ConnectionError("synthetic-aggregation-failure from W607-D test")

    monkeypatch.setattr(cmd_dogfood, "git_metadata", _boom)

    result = invoke_cli(cli_runner, ["dogfood"], json_mode=True)
    assert result.exit_code == 0, result.output
    env = _last_json(result.output)

    # Top-level disclosure (canonical idiom — preserved-list field).
    top_wo = env.get("warnings_out")
    assert top_wo, (
        f"git_metadata ConnectionError must surface top-level warnings_out; got env keys = {sorted(env.keys())!r}"
    )
    assert any(m.startswith("dogfood_aggregation_failed:") for m in top_wo), (
        f"expected ``dogfood_aggregation_failed:`` marker in top-level warnings_out; got {top_wo!r}"
    )
    # ConnectionError class name must propagate for triage.
    assert any("ConnectionError" in m for m in top_wo), top_wo
    # Synthetic detail must propagate.
    assert any("synthetic-aggregation-failure from W607-D test" in m for m in top_wo), top_wo


# ---------------------------------------------------------------------------
# (3) partial_success flips when any marker present
# ---------------------------------------------------------------------------


def test_partial_success_flips_on_warning_present(tiny_indexed, cli_runner, monkeypatch):
    """Any non-empty warnings_out → summary.partial_success = True.

    Pattern-2 contract: the agent MUST be able to distinguish "clean
    dogfood run" from "dogfood ran with substrate degradation" via
    summary.partial_success alone.
    """
    from roam.commands import cmd_dogfood

    def _boom(*a, **kw):
        raise RuntimeError("synthetic-runtime-partial-success-test")

    monkeypatch.setattr(cmd_dogfood, "git_metadata", _boom)

    result = invoke_cli(cli_runner, ["dogfood"], json_mode=True)
    assert result.exit_code == 0, result.output
    env = _last_json(result.output)

    assert env["summary"].get("partial_success") is True, (
        f"non-empty warnings_out must flip summary.partial_success=True; got summary = {env['summary']!r}"
    )


# ---------------------------------------------------------------------------
# (4) summary.warnings_out is populated alongside top-level on disclosure
# ---------------------------------------------------------------------------


def test_summary_mirror(tiny_indexed, cli_runner, monkeypatch):
    """Non-empty bucket → both top-level AND summary.warnings_out are populated.

    Top-level is needed because the preserved-list field
    (``_ALWAYS_PRESERVED_LIST_FIELDS`` in formatter.py) survives
    ``strip_list_payloads`` in default-detail mode. summary mirror gives
    consumers reading only the summary block visibility too.
    """
    from roam.commands import cmd_dogfood

    def _boom(*a, **kw):
        raise ValueError("synthetic-mirror-test")

    monkeypatch.setattr(cmd_dogfood, "git_metadata", _boom)

    result = invoke_cli(cli_runner, ["dogfood"], json_mode=True)
    assert result.exit_code == 0, result.output
    env = _last_json(result.output)

    assert env.get("warnings_out"), f"top-level warnings_out missing on disclosure path; keys = {sorted(env.keys())!r}"
    assert env["summary"].get("warnings_out"), (
        f"summary.warnings_out missing on disclosure path; got summary = {env['summary']!r}"
    )
    assert sorted(env["warnings_out"]) == sorted(env["summary"]["warnings_out"]), (
        f"top-level vs summary.warnings_out must be equal; "
        f"top={env['warnings_out']!r} summary={env['summary']['warnings_out']!r}"
    )


# ---------------------------------------------------------------------------
# (5) Top-level mirror explicitly checked (W607-A/B/C discipline parity)
# ---------------------------------------------------------------------------


def test_top_level_warnings_out_mirror(tiny_indexed, cli_runner, monkeypatch):
    """Top-level ``warnings_out`` must be present alongside summary mirror.

    The preserved-list-field discipline at ``_ALWAYS_PRESERVED_LIST_FIELDS``
    requires the top-level mirror so the field survives detail-mode
    list-payload stripping. cmd_search_semantic W607-A + cmd_retrieve
    W607-B + cmd_findings W607-C pinned the same discipline; W607-D
    extends it to dogfood.
    """
    from roam.commands import cmd_dogfood

    def _boom(*a, **kw):
        raise OSError("synthetic-top-level-mirror-check")

    monkeypatch.setattr(cmd_dogfood, "git_metadata", _boom)

    result = invoke_cli(cli_runner, ["dogfood"], json_mode=True)
    assert result.exit_code == 0, result.output
    env = _last_json(result.output)

    top_wo = env.get("warnings_out")
    assert isinstance(top_wo, list) and top_wo, (
        f"top-level warnings_out must be a non-empty list on disclosure path; got {top_wo!r}"
    )


# ---------------------------------------------------------------------------
# (6) Marker-shape parity — three-segment prefix:exc_class:detail
# ---------------------------------------------------------------------------


def test_outer_guard_marker_shape(tiny_indexed, cli_runner, monkeypatch):
    """Marker must have three colon-separated segments.

    The marker shape MUST be ``<prefix>:<exc_class>:<detail>`` — three
    colon-separated segments — so downstream consumers can parse the
    exception class without regex gymnastics. Mirrors cmd_findings
    W607-C / cmd_retrieve W607-B outer-guard contracts.
    """
    from roam.commands import cmd_dogfood

    def _boom(*a, **kw):
        raise TypeError("synthetic-outer-guard-emit-check")

    monkeypatch.setattr(cmd_dogfood, "git_metadata", _boom)

    result = invoke_cli(cli_runner, ["dogfood"], json_mode=True)
    assert result.exit_code == 0, result.output
    env = _last_json(result.output)

    top_wo = env.get("warnings_out") or []
    assert top_wo, "outer-guard must emit at least one marker"

    pipeline_markers = [m for m in top_wo if m.startswith("dogfood_aggregation_failed:")]
    assert pipeline_markers, f"outer-guard must emit dogfood_aggregation_failed marker; got {top_wo!r}"

    marker = pipeline_markers[0]
    parts = marker.split(":", 2)
    assert len(parts) == 3, f"marker must have three colon-separated segments (prefix:exc_class:detail); got {marker!r}"
    assert parts[0] == "dogfood_aggregation_failed", parts
    assert parts[1] == "TypeError", parts
    assert "synthetic-outer-guard-emit-check" in parts[2], parts
