"""Tests for the v12.1 LLM-augmented taint classifier.

The classifier runs over MCP sampling — the agent's own model labels
each reachable taint finding as IDOR / AUTHZ / SQLI / etc. We mock the
sampler to keep the tests offline and deterministic; the real production
path is exercised by the integration test against the MCP tool.
"""

from __future__ import annotations

from typing import Any

import pytest

from roam.security.taint_classifier import (
    Classification,
    ClassifyOptions,
    _build_user_prompt,
    _parse_classifier_response,
    classify_finding,
    classify_findings,
)

# ---------------------------------------------------------------------------
# Pure parser unit tests
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_clean_json(self):
        raw = '{"label": "IDOR", "confidence": "high", "reasoning": "no auth check before DB access"}'
        out = _parse_classifier_response(raw)
        assert out is not None
        assert out.label == "IDOR"
        assert out.confidence == "high"
        assert "auth check" in out.reasoning

    def test_json_with_preamble(self):
        """Models often add fluff like 'Sure, here is …' — extract anyway."""
        raw = (
            "Sure, here is the classification:\n\n"
            '{"label": "SQLI", "confidence": "medium", "reasoning": "string concat on query"}\n'
            "Hope that helps!"
        )
        out = _parse_classifier_response(raw)
        assert out is not None
        assert out.label == "SQLI"
        assert out.confidence == "medium"

    def test_unknown_label_coerced_to_other(self):
        raw = '{"label": "MAGIC_VULN", "confidence": "high", "reasoning": "x"}'
        out = _parse_classifier_response(raw)
        assert out is not None
        assert out.label == "OTHER"

    def test_unknown_confidence_coerced_to_low(self):
        raw = '{"label": "XSS", "confidence": "very-yes", "reasoning": "x"}'
        out = _parse_classifier_response(raw)
        assert out is not None
        assert out.confidence == "low"

    def test_lowercase_label_normalised_to_uppercase(self):
        raw = '{"label": "idor", "confidence": "high", "reasoning": "x"}'
        out = _parse_classifier_response(raw)
        assert out is not None
        assert out.label == "IDOR"

    def test_long_reasoning_clipped_to_400_chars(self):
        long_reason = "A" * 1000
        raw = f'{{"label": "OTHER", "confidence": "low", "reasoning": "{long_reason}"}}'
        out = _parse_classifier_response(raw)
        assert out is not None
        assert len(out.reasoning) == 400

    def test_no_json_returns_none(self):
        assert _parse_classifier_response("just text, no JSON") is None
        assert _parse_classifier_response("") is None
        assert _parse_classifier_response("{ malformed") is None

    def test_non_dict_returns_none(self):
        assert _parse_classifier_response("[1, 2, 3]") is None


class TestBuildPrompt:
    def test_includes_rule_metadata(self):
        finding = {
            "rule_id": "python-sqli",
            "cwe": "CWE-89",
            "severity": "error",
            "source_symbol": {"qualified_name": "request.args.get"},
            "sink_symbol": {"qualified_name": "cursor.execute"},
            "path_symbols": [
                {"qualified_name": "handle_search", "file": "app.py", "line": 12},
                {"qualified_name": "build_query", "file": "app.py", "line": 28},
            ],
            "sanitizer_in_path": False,
        }
        prompt = _build_user_prompt(finding)
        assert "python-sqli" in prompt
        assert "CWE-89" in prompt
        assert "request.args.get" in prompt
        assert "cursor.execute" in prompt
        assert "handle_search" in prompt
        assert "Sanitizer on path: False" in prompt
        # Closed list of categories is in the response spec.
        assert "IDOR" in prompt
        assert "FALSE_POSITIVE" in prompt


# ---------------------------------------------------------------------------
# Mock-sampler integration tests — async classify_finding / classify_findings
# ---------------------------------------------------------------------------


class _MockSamplingResult:
    """Minimal SamplingResult shape that ``_extract_summary_text`` understands."""

    def __init__(self, text: str):
        self.text = text


class _MockContext:
    """Async context with a programmable .sample method."""

    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def sample(self, user_prompt: str, **kwargs):
        self.calls.append({"user_prompt": user_prompt, **kwargs})
        if not self._responses:
            return _MockSamplingResult("")
        return _MockSamplingResult(self._responses.pop(0))


_BASE_FINDING: dict[str, Any] = {
    "rule_id": "python-sqli",
    "severity": "error",
    "cwe": "CWE-89",
    "source_symbol": {"qualified_name": "request.args.get"},
    "sink_symbol": {"qualified_name": "cursor.execute"},
    "path_symbols": [
        {"qualified_name": "handle_search", "file": "app.py", "line": 12},
        {"qualified_name": "cursor.execute", "file": "app.py", "line": 28},
    ],
    "sanitizer_in_path": False,
}


class TestClassifyFinding:
    @pytest.mark.asyncio
    async def test_returns_classification_on_clean_response(self):
        ctx = _MockContext('{"label": "SQLI", "confidence": "high", "reasoning": "concat in execute"}')
        out = await classify_finding(_BASE_FINDING, ctx)
        assert isinstance(out, Classification)
        assert out.label == "SQLI"
        assert out.confidence == "high"
        assert "concat" in out.reasoning
        # The mock context was called exactly once.
        assert len(ctx.calls) == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_no_ctx(self):
        out = await classify_finding(_BASE_FINDING, None)
        assert out is None

    @pytest.mark.asyncio
    async def test_returns_none_when_ctx_lacks_sample(self):
        class _NoSampleCtx:
            pass

        out = await classify_finding(_BASE_FINDING, _NoSampleCtx())
        assert out is None

    @pytest.mark.asyncio
    async def test_skips_sanitized_finding_by_default(self):
        ctx = _MockContext('{"label": "OTHER", "confidence": "low", "reasoning": "x"}')
        sanitized = {**_BASE_FINDING, "sanitizer_in_path": True}
        out = await classify_finding(sanitized, ctx)
        assert out is None
        # The sampler must NOT have been called — that's the whole point
        # of skip_sanitized: avoid LLM cost on already-clean findings.
        assert ctx.calls == []

    @pytest.mark.asyncio
    async def test_classifies_sanitized_when_opt_in(self):
        ctx = _MockContext('{"label": "FALSE_POSITIVE", "confidence": "high", "reasoning": "sanitized"}')
        sanitized = {**_BASE_FINDING, "sanitizer_in_path": True}
        opts = ClassifyOptions(skip_sanitized=False)
        out = await classify_finding(sanitized, ctx, options=opts)
        assert out is not None
        assert out.label == "FALSE_POSITIVE"

    @pytest.mark.asyncio
    async def test_sampler_failure_returns_none(self):
        class _RaisingCtx:
            async def sample(self, *args, **kwargs):
                raise RuntimeError("transport down")

        out = await classify_finding(_BASE_FINDING, _RaisingCtx())
        assert out is None

    @pytest.mark.asyncio
    async def test_unparseable_response_returns_none(self):
        ctx = _MockContext("not JSON at all, model went sideways")
        out = await classify_finding(_BASE_FINDING, ctx)
        assert out is None


class TestClassifyFindings:
    @pytest.mark.asyncio
    async def test_annotates_each_finding(self):
        ctx = _MockContext(
            '{"label": "SQLI", "confidence": "high", "reasoning": "string concat"}',
            '{"label": "IDOR", "confidence": "medium", "reasoning": "no ownership check"}',
        )
        findings = [_BASE_FINDING, dict(_BASE_FINDING, rule_id="python-idor")]
        out = await classify_findings(findings, ctx)
        assert len(out) == 2
        assert out[0]["classification"]["label"] == "SQLI"
        assert out[1]["classification"]["label"] == "IDOR"
        # Original input must not be mutated.
        assert "classification" not in findings[0]
        assert "classification" not in findings[1]

    @pytest.mark.asyncio
    async def test_failed_classification_passed_through(self):
        ctx = _MockContext("garbage", '{"label": "XSS", "confidence": "high", "reasoning": "x"}')
        findings = [_BASE_FINDING, _BASE_FINDING]
        out = await classify_findings(findings, ctx)
        assert "classification" not in out[0]
        assert out[1]["classification"]["label"] == "XSS"

    @pytest.mark.asyncio
    async def test_no_ctx_passes_findings_through(self):
        findings = [_BASE_FINDING, dict(_BASE_FINDING, rule_id="other")]
        out = await classify_findings(findings, None)
        assert len(out) == 2
        for f in out:
            assert "classification" not in f

    @pytest.mark.asyncio
    async def test_sanitized_finding_passed_through_in_batch(self):
        ctx = _MockContext('{"label": "SQLI", "confidence": "high", "reasoning": "x"}')
        findings = [
            {**_BASE_FINDING, "sanitizer_in_path": True},
            _BASE_FINDING,
        ]
        out = await classify_findings(findings, ctx)
        assert "classification" not in out[0]
        assert out[1]["classification"]["label"] == "SQLI"
        # Sampler called exactly once — for the unsanitized finding only.
        assert len(ctx.calls) == 1
