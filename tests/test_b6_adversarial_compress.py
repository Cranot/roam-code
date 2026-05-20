"""B6 prototype tests — adversarial-domain MCP sampling compression.

Covers ``roam.mcp_extras.adversarial_compress``:

* prompt builders (``build_defend_prompt`` / ``digest_task_hint`` /
  ``defend_system_prompt``) — pure, no ctx
* envelope shaper (``apply_defend_briefing``) — non-mutating, verdict-safe
* async dispatcher (``compress_adversarial``) in digest + defend modes,
  with a dependency-injected fake sampling module AND against the real
  ``roam.mcp_extras.sampling`` round-trip (gated on ``ROAM_AI_ENABLED``).

Mirrors the ``asyncio.run`` + fake-ctx style of ``tests/test_mcp_extras.py``
(no pytest-asyncio dependency).
"""

from __future__ import annotations

import asyncio

from roam.mcp_extras import adversarial_compress as ac
from roam.mcp_extras import sampling

# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


def _envelope(challenges=None, verdict="2 critical of 4 challenges"):
    """Build a minimal adversarial-shaped envelope."""
    if challenges is None:
        challenges = [
            {
                "type": "new_cycle",
                "severity": "CRITICAL",
                "title": "Cyclic dependency involving 3 symbols",
                "description": "Changed symbols participate in a cycle: a -> b -> c.",
                "question": "Explain why this won't cause initialization ordering issues.",
                "location": "src/roam/foo.py",
            },
            {
                "type": "layer_violation",
                "severity": "HIGH",
                "title": "Layer skip: L1 -> L3",
                "description": "cli calls db, skipping 1 layer.",
                "question": "Justify the shortcut.",
                "location": "src/roam/cli.py:42",
            },
            {
                "type": "high_fan_out",
                "severity": "WARNING",
                "title": "High fan-out: foo calls 12 dependencies",
                "description": "foo has 12 outgoing edges.",
                "question": "Consider splitting responsibilities.",
                "location": "src/roam/foo.py:9",
            },
            {
                "type": "orphaned",
                "severity": "INFO",
                "title": "Orphaned symbol: helper",
                "description": "helper has no callers.",
                "question": "Is this a new entry point?",
                "location": "src/roam/foo.py:99",
            },
        ]
    return {
        "command": "adversarial",
        "summary": {"verdict": verdict, "challenges": len(challenges)},
        "challenges": challenges,
        "agent_contract": {"facts": [verdict]},
    }


class _Ctx:
    """Minimal MCP-context stand-in with no ``.sample`` (sampling absent)."""


class _CtxOK:
    """Ctx whose ``.sample`` echoes a deterministic SamplingResult-like obj."""

    def __init__(self):
        self.calls = []

    async def sample(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return type("Result", (), {"text": "DEFEND: the cycle is broken at init time."})()


class _FakeSampling:
    """Dependency-injected fake of the sampling module for unit isolation."""

    def __init__(self, summary_text="fake briefing", return_none=False):
        self.summary_text = summary_text
        self.return_none = return_none
        self.compress_calls = []

    async def compress_with_sampling(self, ctx, payload, **kwargs):
        self.compress_calls.append({"payload": payload, "kwargs": kwargs})
        if self.return_none:
            return None
        return {
            "compressed": True,
            "summary": self.summary_text,
            "tokens_estimated": max(1, len(self.summary_text) // 4),
        }

    def maybe_apply_compression(self, original, compressed):
        # Reuse the real merge so we exercise the genuine merge contract.
        return sampling.maybe_apply_compression(original, compressed)


# ---------------------------------------------------------------------------
# Pure prompt builders
# ---------------------------------------------------------------------------


class TestDefendPrompt:
    def test_returns_none_on_empty_challenges(self):
        assert ac.build_defend_prompt(_envelope(challenges=[])) is None

    def test_returns_none_on_non_dict(self):
        assert ac.build_defend_prompt("not a dict") is None
        assert ac.build_defend_prompt(None) is None

    def test_prompt_contains_severity_title_location_question(self):
        prompt = ac.build_defend_prompt(_envelope())
        assert prompt is not None
        assert "[CRITICAL]" in prompt
        assert "Cyclic dependency involving 3 symbols" in prompt
        assert "src/roam/foo.py" in prompt
        assert "initialization ordering" in prompt
        assert "defend" in prompt.lower()

    def test_highest_severity_first(self):
        prompt = ac.build_defend_prompt(_envelope())
        assert prompt is not None
        crit = prompt.index("CRITICAL")
        high = prompt.index("Layer skip")
        info = prompt.index("Orphaned symbol")
        assert crit < high < info

    def test_max_challenges_caps_but_reports_total(self):
        prompt = ac.build_defend_prompt(_envelope(), max_challenges=2)
        assert prompt is not None
        # 4 total exist; only 2 shown -> the cap disclosure must fire.
        assert "found 4 architectural" in prompt
        assert "2 highest-severity" in prompt
        # The INFO (lowest) challenge must be dropped.
        assert "Orphaned symbol" not in prompt

    def test_task_is_threaded_into_prompt(self):
        prompt = ac.build_defend_prompt(_envelope(), task="add caching layer")
        assert prompt is not None
        assert "add caching layer" in prompt

    def test_verdict_included(self):
        prompt = ac.build_defend_prompt(_envelope(verdict="3 critical of 5 challenges"))
        assert prompt is not None
        assert "3 critical of 5 challenges" in prompt

    def test_unknown_severity_sorts_last(self):
        env = _envelope(
            challenges=[
                {"severity": "WEIRD", "title": "mystery", "question": "?"},
                {"severity": "CRITICAL", "title": "real", "question": "defend"},
            ]
        )
        prompt = ac.build_defend_prompt(env)
        assert prompt is not None
        assert prompt.index("real") < prompt.index("mystery")


class TestDefendSystemPrompt:
    def test_is_adversarial_not_neutral(self):
        sp = ac.defend_system_prompt()
        assert "adversarial" in sp.lower()
        assert "dungeon master" in sp.lower()
        # Distinct from the neutral sampling summariser prompt.
        assert sp != sampling._BRIEFING_SYSTEM_PROMPT


class TestDigestTaskHint:
    def test_counts_challenges(self):
        hint = ac.digest_task_hint(_envelope())
        assert "4 adversarial architecture challenges" in hint

    def test_no_challenges_phrasing(self):
        hint = ac.digest_task_hint(_envelope(challenges=[]))
        assert "no challenges" in hint

    def test_base_task_prepended(self):
        hint = ac.digest_task_hint(_envelope(), base_task="fix login bug")
        assert hint.startswith("fix login bug; ")

    def test_non_dict_safe(self):
        hint = ac.digest_task_hint(None)
        assert "no challenges" in hint


# ---------------------------------------------------------------------------
# Envelope shaper
# ---------------------------------------------------------------------------


class TestApplyDefendBriefing:
    def test_adds_field_preserves_verdict(self):
        env = _envelope()
        out = ac.apply_defend_briefing(env, "the cycle is fine because X")
        assert out["defend_briefing"] == "the cycle is fine because X"
        assert out["summary"]["verdict"] == "2 critical of 4 challenges"
        assert out["summary"]["compressed"] is True
        assert out["summary"]["defend_briefing_tokens"] >= 1

    def test_does_not_mutate_input(self):
        env = _envelope()
        ac.apply_defend_briefing(env, "briefing text")
        assert "defend_briefing" not in env
        assert "compressed" not in env["summary"]

    def test_blank_briefing_is_noop(self):
        env = _envelope()
        assert ac.apply_defend_briefing(env, "   ") is env
        assert ac.apply_defend_briefing(env, "") is env

    def test_non_dict_returns_input(self):
        assert ac.apply_defend_briefing("x", "y") == "x"


# ---------------------------------------------------------------------------
# Async dispatcher — fake sampling (unit isolation)
# ---------------------------------------------------------------------------


class TestCompressAdversarialFakeSampling:
    def test_summarize_false_short_circuits(self):
        fake = _FakeSampling()
        env = _envelope()
        out = asyncio.run(ac.compress_adversarial(_CtxOK(), env, mode="digest", summarize=False, sampling=fake))
        assert out is env
        assert fake.compress_calls == []

    def test_unknown_mode_returns_unchanged(self):
        fake = _FakeSampling()
        env = _envelope()
        out = asyncio.run(ac.compress_adversarial(_CtxOK(), env, mode="bogus", sampling=fake))
        assert out is env
        assert fake.compress_calls == []

    def test_none_ctx_returns_unchanged(self):
        fake = _FakeSampling()
        env = _envelope()
        out = asyncio.run(ac.compress_adversarial(None, env, mode="digest", sampling=fake))
        assert out is env
        assert fake.compress_calls == []

    def test_non_dict_envelope_returns_unchanged(self):
        fake = _FakeSampling()
        out = asyncio.run(ac.compress_adversarial(_CtxOK(), "nope", mode="digest", sampling=fake))
        assert out == "nope"
        assert fake.compress_calls == []

    def test_digest_mode_merges_briefing(self):
        fake = _FakeSampling(summary_text="triage: 1 critical cycle, defend it")
        env = _envelope()
        out = asyncio.run(ac.compress_adversarial(_CtxOK(), env, mode="digest", sampling=fake))
        # maybe_apply_compression adds a top-level ``briefing`` field.
        assert out["briefing"] == "triage: 1 critical cycle, defend it"
        assert out["summary"]["compressed"] is True
        # Verdict preserved (LAW 6).
        assert out["summary"]["verdict"] == "2 critical of 4 challenges"
        # Digest passes the WHOLE envelope as payload + adversarial task hint.
        call = fake.compress_calls[0]
        assert call["payload"] is env
        assert "adversarial architecture challenges" in call["kwargs"]["task"]
        assert call["kwargs"]["target"] == "adversarial-review"

    def test_defend_mode_builds_prompt_and_merges(self):
        fake = _FakeSampling(summary_text="The cycle is broken at module init.")
        env = _envelope()
        out = asyncio.run(ac.compress_adversarial(_CtxOK(), env, mode="defend", sampling=fake))
        assert out["defend_briefing"] == "The cycle is broken at module init."
        assert out["summary"]["verdict"] == "2 critical of 4 challenges"
        # Defend passes the assembled PROMPT STRING as payload, not the envelope.
        call = fake.compress_calls[0]
        assert isinstance(call["payload"], str)
        assert "--- CHALLENGES ---" in call["payload"]
        assert call["kwargs"]["target"] == "adversarial-defense"

    def test_defend_mode_no_challenges_skips_sampling(self):
        fake = _FakeSampling()
        env = _envelope(challenges=[])
        out = asyncio.run(ac.compress_adversarial(_CtxOK(), env, mode="defend", sampling=fake))
        assert out is env
        assert fake.compress_calls == []

    def test_sampling_returns_none_falls_back(self):
        fake = _FakeSampling(return_none=True)
        env = _envelope()
        out_digest = asyncio.run(ac.compress_adversarial(_CtxOK(), env, mode="digest", sampling=fake))
        out_defend = asyncio.run(ac.compress_adversarial(_CtxOK(), env, mode="defend", sampling=fake))
        # Both fall back to the unchanged deterministic envelope.
        assert "briefing" not in out_digest
        assert "defend_briefing" not in out_defend


# ---------------------------------------------------------------------------
# Async dispatcher — REAL sampling round-trip (gate + transport)
# ---------------------------------------------------------------------------


class TestCompressAdversarialRealSampling:
    def test_default_off_without_env(self, monkeypatch):
        """Without ROAM_AI_ENABLED the real gate returns None -> no-op."""
        monkeypatch.delenv("ROAM_AI_ENABLED", raising=False)

        class CtxMustNotSample(_CtxOK):
            async def sample(self, *a, **k):
                raise AssertionError("sample must not run when ROAM_AI_ENABLED is unset")

        env = _envelope()
        out = asyncio.run(ac.compress_adversarial(CtxMustNotSample(), env, mode="defend"))
        assert "defend_briefing" not in out
        assert out["summary"]["verdict"] == "2 critical of 4 challenges"

    def test_real_defend_round_trip_when_enabled(self, monkeypatch):
        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        ctx = _CtxOK()
        env = _envelope()
        out = asyncio.run(ac.compress_adversarial(ctx, env, mode="defend"))
        assert out["defend_briefing"] == "DEFEND: the cycle is broken at init time."
        # The real round-trip received our assembled defend prompt.
        assert ctx.calls
        assert "--- CHALLENGES ---" in ctx.calls[0]["prompt"]

    def test_real_digest_round_trip_when_enabled(self, monkeypatch):
        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        ctx = _CtxOK()
        env = _envelope()
        out = asyncio.run(ac.compress_adversarial(ctx, env, mode="digest"))
        assert out["briefing"] == "DEFEND: the cycle is broken at init time."
        assert out["summary"]["verdict"] == "2 critical of 4 challenges"

    def test_ctx_without_sample_is_noop(self, monkeypatch):
        monkeypatch.setenv("ROAM_AI_ENABLED", "1")
        env = _envelope()
        # _Ctx has no .sample -> compress_with_sampling returns None.
        out = asyncio.run(ac.compress_adversarial(_Ctx(), env, mode="defend"))
        assert "defend_briefing" not in out
