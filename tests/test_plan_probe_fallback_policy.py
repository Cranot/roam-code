"""An unsuccessful L1 optimization must preserve the declared artifact policy."""

import pytest


@pytest.mark.parametrize(
    "procedure,has_answer,expected_label,expected_schema",
    [
        ("freeform_explore", False, "facts", "roam-plan-v0-facts"),
        ("freeform_explore", True, "l1_probe", "roam-plan-v0-l1-probe"),
        ("structural_coupling", False, "full", "roam-plan-v0"),
    ],
)
def test_probe_preserves_fallback_policy(tmp_path, monkeypatch, procedure, has_answer, expected_label, expected_schema):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("ROAM_TELEMETRY_LOCAL", "0")
    monkeypatch.setenv("ROAM_COMPILE_TELEMETRY", "0")
    monkeypatch.chdir(tmp_path)

    from roam.plan.compiler import PlanV0, compile_for_artifact, select_artifact

    # Backticks provide the answer-shaped trigger for facts -> L1 promotion.
    # Supply the classifier output directly to isolate artifact compilation.
    plan = PlanV0(
        task="Which tests cover `parse_widget` in src/widget.py?",
        procedure=procedure,
        likely_files=["src/widget.py"],
        required_checks=["run relevant tests"],
        forbidden_paths=[],
        plan_quality=0.8,
        model_calls_avoided=[],
        recommended_first_command="roam test-gaps src/widget.py",
        classifier_confidence=0.9,
    )
    declared_artifact = "facts" if procedure == "freeform_explore" else "full"
    assert select_artifact(plan) == declared_artifact
    attempts = []

    def probe(self, cwd=None):
        attempts.append(cwd)
        return {
            "schema": "roam-plan-v0-l1-probe",
            "plan": {
                "prefetched_facts": {"test_impact": ["tests/test_widget.py"]} if has_answer else {},
            },
        }

    # Replace only the expensive probe boundary; selection, promotion,
    # restraint, degradation, and all fallback serializers remain real.
    monkeypatch.setattr(PlanV0, "to_l1_probe_envelope", probe)
    envelope, label = compile_for_artifact(plan, cwd=str(tmp_path))

    assert attempts == [str(tmp_path)]
    assert label == expected_label
    assert envelope["schema"] == expected_schema
    if label == "facts":
        assert "recommended_first_command" not in envelope["plan"]
        assert "required_checks" not in envelope["plan"]
    if has_answer:
        assert envelope["plan"]["prefetched_facts"]["test_impact"]
