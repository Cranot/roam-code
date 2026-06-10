"""W-BATCH — self_contained_task fast-path (2026-06-09).

Cross-repo transcript mining (721 unique prompts from frontend/stoa/home
projects): 63% are self-contained batch payloads ("You are validating…",
"Synthesize the…" with explicit output specs). They need ZERO repo facts,
yet burned the always-on probe budget and polluted named_paths. The
fast-path routes them to a zero-probe notice envelope.
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import (
    _classify,
    _is_self_contained_task,
    _probe_self_contained,
)

BATCH_VALIDATE = """You are validating a behavior extraction adversarially.

Source file: /data/migrations/legacy/report_gen.bas
Extraction JSON: /tmp/pipeline/ab_test/n2v_report_gen.bas.json

For each behavior in the extraction, verify it against the source. Score
each as CONFIRMED / PARTIAL / WRONG.
Output JSON only: {"scores": [{"behavior_id": 1, "verdict": "CONFIRMED"}]}"""

BATCH_SYNTH = """Synthesize the producer + validator outputs into a final v8 markdown spec.

Inputs:
- Producer: /tmp/pipeline/out/producer.json
- Validator: /tmp/pipeline/out/validator.json

Return ONLY the markdown document, no preamble."""


class TestSelfContainedClassification:
    def test_validator_payload_routes(self):
        assert _classify(BATCH_VALIDATE)[0] == "self_contained_task"

    def test_synthesize_payload_routes(self):
        assert _classify(BATCH_SYNTH)[0] == "self_contained_task"

    @pytest.mark.parametrize(
        "task,expected",
        [
            # repo-relative path veto — long role-play prompts that anchor on
            # repo files still want probes
            (
                "You are a senior reviewer. Review src/roam/plan/compiler.py for "
                "consistency across the classifier integration tables and report "
                "anything that looks drifted between the sites.",
                "freeform_explore",
            ),
            # short prompts never fast-path
            ("You are a helpful assistant", "freeform_explore"),
            # short output-directive prompt: too short for the fast-path; the
            # "health score" phrase legitimately routes to repo_structure
            ("output json for the health score", "repo_structure"),
            # neighboring procedures unaffected
            ("write a pytest for _resolve_module_names in src/roam/plan/compiler.py", "synthesis_query"),
            ("what changed in src/roam/cli.py recently", "file_history"),
        ],
    )
    def test_guards(self, task, expected):
        assert _classify(task)[0] == expected

    def test_repo_path_veto(self):
        anchored = BATCH_VALIDATE.replace("/data/migrations/legacy/report_gen.bas", "src/roam/plan/compiler.py")
        assert not _is_self_contained_task(anchored)


class TestSelfContainedProbe:
    def test_probe_emits_notice(self):
        facts = _probe_self_contained([], None, task=BATCH_VALIDATE)
        assert facts and "self_contained_notice" in facts
        assert "No repo facts prefetched" in facts["self_contained_notice"]
