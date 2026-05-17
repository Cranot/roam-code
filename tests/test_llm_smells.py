"""Tests for W415 / W415b -- `roam llm-smells` LLM-API integration linter.

The `llm-smells` detector scans LLM-importing files for ten anti-patterns:

W415 (v1.0.0):
* `llm_api_no_model_version_pinning` -- moving alias instead of dated snapshot
* `llm_api_missing_max_tokens` -- completion call without token bound
* `llm_api_direct_user_input_concatenation` -- same-function prompt-injection
* `llm_api_no_structured_output_validation` -- json.loads without try/except
* `llm_api_temperature_not_set` -- completion call without explicit temperature

W415b (v1.1.0):
* `llm_api_missing_timeout` -- LLM client construction without timeout=
* `llm_api_missing_max_retries` -- LLM client construction without max_retries=
* `llm_api_no_system_message` -- inline messages=[...] with no role: system
* `llm_api_no_retry_on_rate_limit` -- file-level: no retry / backoff indicator
* `llm_api_call_in_loop` -- completion call inside an unbounded loop

Each test pairs a positive (anti-pattern present, detector should fire) with a
negative (mitigation applied, detector should NOT fire) so the test surface
covers both axes per the dogfood "never N/A without running it" rule.
"""

from __future__ import annotations

import json
import os
import re

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_llm_smells import (
    LLM_SMELLS_DETECTOR_VERSION,
    _detect_call_in_loop,
    _detect_missing_max_retries,
    _detect_missing_max_tokens,
    _detect_missing_timeout,
    _detect_no_json_validation,
    _detect_no_model_pinning,
    _detect_no_retry_backoff,
    _detect_no_system_message,
    _detect_pi_concat,
    _detect_temperature_not_set,
    _is_llm_file,
    _llm_smell_tier,
    _llm_smells_finding_id,
)
from roam.db.connection import open_db
from roam.db.findings import CONFIDENCE_HEURISTIC
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Unit tests -- pure functions on synthetic source text
# ---------------------------------------------------------------------------


def test_is_llm_file_recognises_openai_import():
    """Bare ``import openai`` flips the LLM-file gate."""
    assert _is_llm_file("import openai\n") is True
    assert _is_llm_file("from openai import OpenAI\n") is True


def test_is_llm_file_recognises_anthropic_and_langchain():
    """Multiple provider SDKs flip the gate."""
    assert _is_llm_file("import anthropic\n") is True
    assert _is_llm_file("from langchain.chat_models import ChatOpenAI\n") is True
    assert _is_llm_file("import litellm\n") is True


def test_is_llm_file_negative_on_non_llm_imports():
    """Non-LLM imports leave the gate closed."""
    assert _is_llm_file("import os\n") is False
    assert _is_llm_file("from collections import defaultdict\n") is False


def test_mp1_flags_unpinned_gpt4o():
    """Bare ``gpt-4o`` (no date suffix) flags."""
    src = 'response = client.chat.completions.create(model="gpt-4o", messages=msgs)\n'
    out = _detect_no_model_pinning("x.py", src)
    assert len(out) == 1
    assert out[0]["evidence"]["model_literal"] == "gpt-4o"


def test_mp1_passes_pinned_snapshot():
    """Dated snapshot ``gpt-4o-2024-11-20`` does NOT flag."""
    src = 'response = client.chat.completions.create(model="gpt-4o-2024-11-20")\n'
    out = _detect_no_model_pinning("x.py", src)
    assert out == []


def test_mp1_flags_claude_latest_alias():
    """``claude-3-5-sonnet-latest`` flags."""
    src = 'r = anthropic.messages.create(model="claude-3-5-sonnet-latest")\n'
    out = _detect_no_model_pinning("x.py", src)
    assert len(out) == 1


def test_tb1_flags_missing_max_tokens():
    """Completion call without max_tokens flags."""
    src = (
        "response = client.chat.completions.create(\n"
        '    model="gpt-4o-2024-11-20",\n'
        '    messages=[{"role": "user", "content": "hi"}]\n'
        ")\n"
    )
    out = _detect_missing_max_tokens("x.py", src)
    assert len(out) == 1


def test_tb1_passes_with_max_tokens():
    """Completion call with max_tokens passes."""
    src = (
        "response = client.chat.completions.create(\n"
        '    model="gpt-4o-2024-11-20",\n'
        "    messages=msgs,\n"
        "    max_tokens=1024,\n"
        ")\n"
    )
    out = _detect_missing_max_tokens("x.py", src)
    assert out == []


def test_tb1_passes_with_max_output_tokens_variant():
    """``max_output_tokens`` (Anthropic) also satisfies the bound."""
    src = (
        'response = client.messages.create(\n    model="claude-3-5-sonnet-20241022",\n    max_output_tokens=2048,\n)\n'
    )
    out = _detect_missing_max_tokens("x.py", src)
    assert out == []


def test_tn1_flags_missing_temperature():
    """Completion call without temperature= flags."""
    src = 'r = client.chat.completions.create(model="gpt-4o-2024-11-20", messages=m)\n'
    out = _detect_temperature_not_set("x.py", src)
    assert len(out) == 1


def test_tn1_passes_with_temperature():
    """Explicit temperature= passes."""
    src = 'r = client.chat.completions.create(\n    model="gpt-4o-2024-11-20", messages=m, temperature=0.2)\n'
    out = _detect_temperature_not_set("x.py", src)
    assert out == []


def test_so1_flags_bare_json_loads():
    """``json.loads`` without surrounding try flags."""
    src = "content = response.choices[0].message.content\ndata = json.loads(content)\n"
    out = _detect_no_json_validation("x.py", src)
    assert len(out) == 1


def test_so1_passes_with_try_block():
    """``json.loads`` inside try/except does NOT flag."""
    src = "try:\n    data = json.loads(content)\nexcept json.JSONDecodeError:\n    data = {}\n"
    out = _detect_no_json_validation("x.py", src)
    assert out == []


def test_pi1_flags_user_input_concat_with_prompt_keyword():
    """Function mixing completion call + user_input + concat + prompt keyword flags."""
    src = (
        "import openai\n"
        "client = openai.OpenAI()\n"
        "\n"
        "def respond(user_input):\n"
        "    system = 'You are a helpful assistant'\n"
        "    prompt = system + '\\n\\nUser: ' + user_input\n"
        "    return client.chat.completions.create(\n"
        '        model="gpt-4o-2024-11-20",\n'
        '        messages=[{"role": "user", "content": prompt}],\n'
        "    )\n"
    )
    out = _detect_pi_concat("x.py", src)
    assert len(out) >= 1
    assert out[0]["kind"] == "llm_api_direct_user_input_concatenation"


def test_pi1_passes_when_user_input_isolated():
    """User input in its own message role (no concat) does NOT flag."""
    src = (
        "import openai\n"
        "client = openai.OpenAI()\n"
        "\n"
        "def respond(user_input):\n"
        "    return client.chat.completions.create(\n"
        '        model="gpt-4o-2024-11-20",\n'
        "        messages=[\n"
        '            {"role": "system", "content": "You are helpful"},\n'
        '            {"role": "user", "content": user_input},\n'
        "        ],\n"
        "    )\n"
    )
    out = _detect_pi_concat("x.py", src)
    # The body still contains "you are" prompt keyword and an f-string-ish
    # quote pattern, but there's no + concatenation -- the heuristic
    # requires both the variable AND the concat operator + prompt keyword.
    # The negative case here uses no `+` concat -- should pass.
    assert out == []


def test_pi1_passes_when_no_completion_call():
    """Function without a completion call is out of scope (not an LLM-call function)."""
    src = "import openai\n\ndef helper(user_input):\n    prompt = 'You are: ' + user_input\n    return prompt\n"
    out = _detect_pi_concat("x.py", src)
    assert out == []


# ---------------------------------------------------------------------------
# W415b detectors (5 cheap patterns from W402 catalog)
# ---------------------------------------------------------------------------


def test_tb2_flags_openai_client_without_timeout():
    """``OpenAI()`` constructed without ``timeout=`` flags."""
    src = "import openai\nclient = openai.OpenAI()\n"
    out = _detect_missing_timeout("x.py", src)
    assert len(out) == 1
    assert out[0]["kind"] == "llm_api_missing_timeout"
    assert "OpenAI" in out[0]["evidence"]["client"]


def test_tb2_passes_with_timeout():
    """``OpenAI(timeout=30.0)`` passes."""
    src = "import openai\nclient = openai.OpenAI(timeout=30.0)\n"
    out = _detect_missing_timeout("x.py", src)
    assert out == []


def test_tb3_flags_openai_client_without_max_retries():
    """``OpenAI()`` constructed without ``max_retries=`` flags."""
    src = "import openai\nclient = openai.OpenAI()\n"
    out = _detect_missing_max_retries("x.py", src)
    assert len(out) == 1
    assert out[0]["kind"] == "llm_api_missing_max_retries"


def test_tb3_passes_with_max_retries():
    """``OpenAI(max_retries=3)`` passes."""
    src = "import openai\nclient = openai.OpenAI(max_retries=3)\n"
    out = _detect_missing_max_retries("x.py", src)
    assert out == []


def test_sm1_flags_messages_without_system_role():
    """Inline ``messages=[{role: user}]`` with no system entry flags."""
    src = (
        "response = client.chat.completions.create(\n"
        '    model="gpt-4o-2024-11-20",\n'
        '    messages=[{"role": "user", "content": "hi"}],\n'
        ")\n"
    )
    out = _detect_no_system_message("x.py", src)
    assert len(out) == 1
    assert out[0]["kind"] == "llm_api_no_system_message"


def test_sm1_passes_with_system_role():
    """Inline messages array containing role:system passes."""
    src = (
        "response = client.chat.completions.create(\n"
        '    model="gpt-4o-2024-11-20",\n'
        "    messages=[\n"
        '        {"role": "system", "content": "helpful"},\n'
        '        {"role": "user", "content": "hi"},\n'
        "    ],\n"
        ")\n"
    )
    out = _detect_no_system_message("x.py", src)
    assert out == []


def test_sm1_skips_messages_variable():
    """``messages=msgs`` (variable, not literal) is out of scope — no flag."""
    # Sanity: when messages is a variable we can't inspect its contents,
    # so we deliberately avoid false-positive noise. Otherwise tools that
    # build a system message in a helper function would always fire.
    src = 'response = client.chat.completions.create(\n    model="gpt-4o-2024-11-20",\n    messages=msgs,\n)\n'
    out = _detect_no_system_message("x.py", src)
    assert out == []


def test_re1_flags_llm_file_without_retry_indicator():
    """LLM file with no retry/backoff/RateLimitError marker flags once."""
    src = 'import openai\nclient = openai.OpenAI()\nr = client.chat.completions.create(model="x", messages=[])\n'
    out = _detect_no_retry_backoff("x.py", src)
    assert len(out) == 1
    assert out[0]["kind"] == "llm_api_no_retry_on_rate_limit"
    assert out[0]["evidence"]["scope"] == "file"


def test_re1_passes_with_tenacity_retry():
    """File using ``@retry`` (tenacity) passes."""
    src = (
        "import openai\n"
        "from tenacity import retry\n"
        "client = openai.OpenAI()\n"
        "@retry\n"
        "def call():\n"
        '    return client.chat.completions.create(model="x", messages=[])\n'
    )
    out = _detect_no_retry_backoff("x.py", src)
    assert out == []


def test_cl1_flags_completion_call_in_unbounded_for_loop():
    """LLM call inside ``for item in items:`` (unbounded) flags."""
    src = (
        "import openai\n"
        "client = openai.OpenAI()\n"
        "for item in items:\n"
        '    r = client.chat.completions.create(model="x", messages=[])\n'
    )
    out = _detect_call_in_loop("x.py", src)
    assert len(out) == 1
    assert out[0]["kind"] == "llm_api_call_in_loop"
    assert out[0]["evidence"]["loop_keyword"] == "for"


def test_cl1_passes_with_explicit_slice_bound():
    """``for item in items[:10]:`` provides an explicit bound — no flag."""
    src = (
        "import openai\n"
        "client = openai.OpenAI()\n"
        "for item in items[:10]:\n"
        '    r = client.chat.completions.create(model="x", messages=[])\n'
    )
    out = _detect_call_in_loop("x.py", src)
    assert out == []


def test_cl1_passes_with_range_bound():
    """``for i in range(100):`` is bounded — no flag."""
    src = (
        "import openai\n"
        "client = openai.OpenAI()\n"
        "for i in range(100):\n"
        '    r = client.chat.completions.create(model="x", messages=[])\n'
    )
    out = _detect_call_in_loop("x.py", src)
    assert out == []


def test_llm_smell_tier_returns_heuristic_for_all_kinds():
    """All v1 + v1.1 kinds ride the heuristic confidence tier."""
    for kind in (
        # v1 (W415)
        "llm_api_no_model_version_pinning",
        "llm_api_missing_max_tokens",
        "llm_api_direct_user_input_concatenation",
        "llm_api_no_structured_output_validation",
        "llm_api_temperature_not_set",
        # v1.1 (W415b)
        "llm_api_missing_timeout",
        "llm_api_missing_max_retries",
        "llm_api_no_system_message",
        "llm_api_no_retry_on_rate_limit",
        "llm_api_call_in_loop",
    ):
        assert _llm_smell_tier(kind) == CONFIDENCE_HEURISTIC


def test_llm_smells_finding_id_is_deterministic():
    """Same inputs -> same id (so re-runs upsert instead of duplicating)."""
    a = _llm_smells_finding_id("llm_api_missing_max_tokens", "a/b.py", 5, "deadbeef")
    b = _llm_smells_finding_id("llm_api_missing_max_tokens", "a/b.py", 5, "deadbeef")
    assert a == b
    assert a.startswith("llm-smells:llm_api_missing_max_tokens:")


def test_llm_smells_finding_id_changes_with_inputs():
    """Different inputs -> different ids (no collisions)."""
    base = _llm_smells_finding_id("llm_api_missing_max_tokens", "a/b.py", 5, "h1")
    assert base != _llm_smells_finding_id("llm_api_missing_max_tokens", "a/b.py", 6, "h1")
    assert base != _llm_smells_finding_id("llm_api_missing_max_tokens", "a/c.py", 5, "h1")
    assert base != _llm_smells_finding_id("llm_api_temperature_not_set", "a/b.py", 5, "h1")


# ---------------------------------------------------------------------------
# Integration test -- full CLI run on a synthetic LLM-using project
# ---------------------------------------------------------------------------


def _llm_project(tmp_path):
    """Synthetic LLM-using repo. Each file triggers a different pattern."""
    return _make_project(
        tmp_path,
        {
            "calls.py": """
            import openai

            client = openai.OpenAI()

            def alpha(user_input):
                system = "You are helpful"
                prompt = system + "\\n\\nUser: " + user_input
                return client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}]
                )

            def beta(content):
                data = json.loads(content)
                return data
            """,
            "safe.py": """
            import openai
            import json

            client = openai.OpenAI()

            def call_model(user_input):
                response = client.chat.completions.create(
                    model="gpt-4o-2024-11-20",
                    messages=[
                        {"role": "system", "content": "You are helpful"},
                        {"role": "user", "content": user_input},
                    ],
                    max_tokens=1024,
                    temperature=0.2,
                )
                try:
                    return json.loads(response.choices[0].message.content)
                except json.JSONDecodeError:
                    return {}
            """,
            "noisefree.py": """
            # No LLM import here -- should be skipped entirely.
            import os

            def helper(x):
                return x * 2
            """,
        },
    )


def test_llm_smells_runs_end_to_end_and_emits_findings(tmp_path):
    """Full ``roam llm-smells --persist`` round-trip writes registry rows."""
    proj = _llm_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        idx = runner.invoke(cli, ["index"])
        assert idx.exit_code == 0, idx.output
        result = runner.invoke(cli, ["llm-smells", "--persist"])
        assert result.exit_code == 0, result.output
        # VERDICT line is present
        assert "VERDICT:" in result.output

        # Findings registry should have llm-smells rows.
        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT finding_id_str, claim, source_detector, source_version, "
                "       subject_kind, confidence, evidence_json "
                "FROM findings WHERE source_detector = 'llm-smells'"
            ).fetchall()
        assert len(rows) >= 1, "expected at least one llm-smells finding row"
        for r in rows:
            assert r["source_detector"] == "llm-smells"
            assert r["source_version"] == LLM_SMELLS_DETECTOR_VERSION
            assert r["subject_kind"] == "file"
            assert r["confidence"] == "heuristic"
            assert r["finding_id_str"].startswith("llm-smells:")
            evidence = json.loads(r["evidence_json"])
            assert "pattern" in evidence
            assert evidence["pattern"].startswith("llm_api_")
    finally:
        os.chdir(old_cwd)


def test_llm_smells_json_envelope_has_verdict_and_patterns(tmp_path):
    """JSON mode emits a verdict + patterns array + findings array."""
    proj = _llm_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        idx = runner.invoke(cli, ["index"])
        assert idx.exit_code == 0, idx.output
        result = runner.invoke(cli, ["--json", "llm-smells"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["command"] == "llm-smells"
        summary = envelope["summary"]
        assert "verdict" in summary
        assert "total_findings" in summary
        assert "llm_files_scanned" in summary
        # All v1 + v1.1 pattern kinds appear in patterns[].
        kinds = {p["kind"] for p in envelope["patterns"]}
        # v1 (W415)
        assert "llm_api_no_model_version_pinning" in kinds
        assert "llm_api_missing_max_tokens" in kinds
        assert "llm_api_direct_user_input_concatenation" in kinds
        assert "llm_api_no_structured_output_validation" in kinds
        assert "llm_api_temperature_not_set" in kinds
        # v1.1 (W415b)
        assert "llm_api_missing_timeout" in kinds
        assert "llm_api_missing_max_retries" in kinds
        assert "llm_api_no_system_message" in kinds
        assert "llm_api_no_retry_on_rate_limit" in kinds
        assert "llm_api_call_in_loop" in kinds
    finally:
        os.chdir(old_cwd)


def test_llm_smells_finding_id_str_is_deterministic_across_runs(tmp_path):
    """Re-running llm-smells produces the same id set (upsert, not duplicate)."""
    proj = _llm_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        first = runner.invoke(cli, ["llm-smells", "--persist"])
        assert first.exit_code == 0
        with open_db(readonly=True) as conn:
            first_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'llm-smells'"
                ).fetchall()
            }
            first_count = conn.execute("SELECT COUNT(*) FROM findings WHERE source_detector = 'llm-smells'").fetchone()[
                0
            ]
        assert first_count == len(first_ids), "duplicate ids on first run"

        second = runner.invoke(cli, ["llm-smells", "--persist"])
        assert second.exit_code == 0
        with open_db(readonly=True) as conn:
            second_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT finding_id_str FROM findings WHERE source_detector = 'llm-smells'"
                ).fetchall()
            }
            second_count = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE source_detector = 'llm-smells'"
            ).fetchone()[0]
        assert second_count == first_count, "row count drifted across runs"
        assert second_ids == first_ids, "finding_id_str set changed across runs"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# LAW 4 drift guard -- verdict terminal must anchor on a concrete-noun
# ---------------------------------------------------------------------------


def test_llm_smells_verdict_terminal_is_concrete_noun_anchored(tmp_path):
    """The summary.verdict must end on a concrete-noun terminal (LAW 4).

    ``files`` and ``findings`` are both in the canonical anchor set; both
    branches of the verdict (zero / non-zero) must terminate on one of
    them. This guards against a future edit drifting the verdict to an
    abstract noun ("clean", "ok") which would silently activate summary
    mode in consuming agents.
    """
    # Empty case (no LLM files -- noisefree.py only).
    proj = _make_project(tmp_path, {"x.py": "import os\n"})
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["--json", "llm-smells"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        verdict = envelope["summary"]["verdict"]
        terminal = re.split(r"\s+", verdict.strip())[-1].rstrip(",.;:!?)").lower()
        assert terminal in {"files", "findings"}, f"verdict terminal {terminal!r} not in anchor set: {verdict!r}"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# W1005-followup-A -- --min-severity widened from 3-tier to W547 7-token vocab
# ---------------------------------------------------------------------------


def test_llm_smells_min_severity_high_keeps_critical_only(tmp_path):
    """``--min-severity high`` filters via W547 severity_rank.

    Detectors emit {info, warning, critical}. ``high`` has the same rank as
    ``critical`` in W547's canonical table (CVSS alias), so only the
    critical-severity pattern (``llm_api_direct_user_input_concatenation``,
    aka pi1) survives. ``warning`` and ``info`` rows are filtered out.

    This pins the W1005-followup-A widening: pre-widening the Click Choice
    rejected ``high`` outright (UsageError, exit 2); post-widening the value
    is accepted and severity_rank() does the comparison.
    """
    proj = _llm_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["--json", "llm-smells", "--min-severity", "high"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        kinds = {f["kind"] for f in envelope.get("findings", [])}
        # The PI-concat pattern in calls.py is critical -- must survive.
        assert "llm_api_direct_user_input_concatenation" in kinds, (
            f"expected critical PI pattern to survive --min-severity high; got {kinds}"
        )
        # Warning + info patterns must NOT survive. Spot-check a representative
        # warning (missing_max_tokens) and info (temperature_not_set).
        assert "llm_api_missing_max_tokens" not in kinds, (
            f"warning-severity pattern leaked past --min-severity high: {kinds}"
        )
        assert "llm_api_temperature_not_set" not in kinds, (
            f"info-severity pattern leaked past --min-severity high: {kinds}"
        )
    finally:
        os.chdir(old_cwd)


def test_llm_smells_min_severity_info_is_passthrough(tmp_path):
    """``--min-severity info`` keeps every emitted severity (pass-through).

    ``info`` is the floor of the W547 canonical ordering, so the filter is a
    no-op: the finding set with ``--min-severity info`` must equal the set
    without any --min-severity flag at all (default is also ``info``). Pins
    that the W1005-followup-A widening did not regress the default path.
    """
    proj = _llm_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        baseline = runner.invoke(cli, ["--json", "llm-smells"])
        assert baseline.exit_code == 0, baseline.output
        baseline_envelope = json.loads(baseline.output)
        baseline_kinds = {f["kind"] for f in baseline_envelope.get("findings", [])}

        explicit = runner.invoke(cli, ["--json", "llm-smells", "--min-severity", "info"])
        assert explicit.exit_code == 0, explicit.output
        explicit_envelope = json.loads(explicit.output)
        explicit_kinds = {f["kind"] for f in explicit_envelope.get("findings", [])}

        assert explicit_kinds == baseline_kinds, (
            f"--min-severity info should be pass-through; baseline={baseline_kinds} explicit={explicit_kinds}"
        )
        # Sanity: at least one finding kind survived (otherwise the test
        # would pass trivially on an empty set).
        assert explicit_kinds, "expected at least one finding from the synthetic LLM fixture"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# MCP wrapper coverage assertion
# ---------------------------------------------------------------------------


def test_mcp_wrapper_registered():
    """``roam_llm_smells`` is in the MCP tool metadata table."""
    try:
        from roam.mcp_server import _TOOL_METADATA
    except ImportError:
        # fastmcp absent -- skip in lean install.
        import pytest

        pytest.skip("MCP transport not installed")
    assert "roam_llm_smells" in _TOOL_METADATA
    meta = _TOOL_METADATA["roam_llm_smells"]
    assert meta["read_only"] is True
    assert meta["destructive"] is False
