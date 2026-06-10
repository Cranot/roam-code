"""W-META — session-continuation directives (2026-06-09).

The single biggest freeform family in production telemetry (~20%+ of unique
freeform prompts): "ultrathink: lets keep going" / "think harder: continue".
These carry no task content, so the old freeform path compiled garbage
named_paths + blind probes at plan_quality 0.25. The session_meta procedure
embeds a tiny `roam brief` anchor instead and tells the agent the
conversation is authoritative.
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import _classify, _is_session_meta


class TestSessionMetaClassification:
    @pytest.mark.parametrize(
        "task",
        [
            "ultrathink: lets keep going",
            "think harder: continue",
            "ultrathink: Continue",
            "ultrathink: keep going",
            "keep going",
            "continue",
            "think harder: ok continue",
            "ultrathink: yes, proceed",
            "what's next",
        ],
    )
    def test_contentless_directives_route_to_session_meta(self, task):
        assert _classify(task)[0] == "session_meta"

    @pytest.mark.parametrize(
        "task",
        [
            # content-bearing prefixed prompts fall through to real procedures
            "ultrathink: what changed in src/roam/cli.py recently",
            "ultrathink: i want you to recall all memories about Compiler",
            "think harder: lets tackle everything that surfaced super smart",
            "ultrathink: check if we restarted",
            # continuation verb + real content
            "continue the refactor of cli.py",
            "keep going with the test fixes in tests/test_cmd_compile.py",
            # plain questions never match
            "what does src/roam/atomic_io.py do",
        ],
    )
    def test_content_bearing_prompts_fall_through(self, task):
        assert _classify(task)[0] != "session_meta"

    def test_is_session_meta_rejects_paths_and_backticks(self):
        assert not _is_session_meta("continue `compile_plan`")
        assert not _is_session_meta("continue src/roam/cli.py")
        assert _is_session_meta("ultrathink: continue")
